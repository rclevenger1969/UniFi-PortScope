"""UniFi Network Cloud Key client. All controller data operations are HTTP GET.

Uses the classic local Network endpoints because they expose port_table and LLDP
across switch models. The official Integration API is useful for inventory, but
its device listing is not a substitute for complete port_table on every release.
"""
from urllib.parse import urlsplit, quote
import ipaddress
import os
import ssl
import httpx


class UniFiError(Exception):
    pass


def validate_controller_url(raw: str) -> str:
    if not isinstance(raw, str) or len(raw) > 250:
        raise UniFiError('Enter a valid controller HTTPS URL, for example https://192.168.1.2')
    raw = raw.strip().rstrip('/')
    try:
        parsed = urlsplit(raw)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
                parsed.password or parsed.path or parsed.query or parsed.fragment or
                not parsed.netloc or parsed.port == 0):
            raise ValueError('Bad URL')
        # Avoid credentials inadvertently sent to external sites; use a LAN hostname/IP.
        host = parsed.hostname
        try:
            parsed_ip = ipaddress.ip_address(host)
            if not (parsed_ip.is_private or parsed_ip.is_loopback or parsed_ip.is_link_local):
                raise UniFiError('Enter a private/LAN controller address, not a public IP.')
        except ValueError:
            if not (host.endswith(('.local', '.lan', '.internal')) or '.' not in host or
                    host.endswith(('.gov', '.us', '.org', '.com', '.net'))):
                raise UniFiError('Use the local hostname or private LAN IP address of the Cloud Key.')
        return raw
    except (ValueError, AttributeError):
        raise UniFiError('Use an HTTPS address without a path, e.g. https://cloudkey.lan or https://10.0.0.2:8443')


class UniFi:
    def __init__(self, url: str, mode: str, auth: str, *, api_key='', username='', password='', verify=True):
        self.url = validate_controller_url(url)
        self.mode = mode
        self.auth = auth
        self.verify = verify
        if mode not in ('auto', 'unifios', 'standalone'):
            raise UniFiError('Invalid controller type.')
        if auth not in ('api_key', 'password'):
            raise UniFiError('Invalid authentication type.')
        if auth == 'api_key' and not api_key.strip():
            raise UniFiError('A local UniFi Network API key is required.')
        if auth == 'password' and not (username and password):
            raise UniFiError('Username and password are required.')
        ca_bundle = os.getenv('PORTSCOPE_CA_BUNDLE', '').strip()
        verify_config = ssl.create_default_context(cafile=ca_bundle) if verify and ca_bundle else verify
        # Never send LAN controller credentials through ambient HTTP(S)_PROXY settings.
        self.http = httpx.Client(verify=verify_config, trust_env=False, timeout=httpx.Timeout(20, connect=7), follow_redirects=False,
                                 headers={'Accept': 'application/json', 'User-Agent': 'UniFi-PortScope/1.0'})
        if auth == 'api_key':
            self.http.headers['X-API-Key'] = api_key.strip()
        self.active_mode = None
        self._login_username = username
        self._login_password = password

    def close(self):
        self.http.close()

    def request_json(self, method: str, path: str, payload=None):
        # Auth POST is permitted; all inventory reads are GET. No mutations.
        if method not in ('GET', 'POST') or (method == 'POST' and path not in ('/api/auth/login', '/api/login')):
            raise UniFiError('This application does not support controller configuration changes.')
        try:
            response = self.http.request(method, self.url + path, json=payload if method == 'POST' else None)
            if response.status_code in (401, 403):
                raise UniFiError('Controller denied access (401/403). Check site permissions, credentials or API key.')
            if response.status_code in (301, 302, 303, 307, 308):
                raise UniFiError('Controller redirected the request. Verify the local Cloud Key URL.')
            if response.status_code == 404:
                raise UniFiError('Endpoint unavailable (404). Check whether this is UniFi OS or standalone Network.')
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, (dict, list)):
                raise UniFiError('The controller did not return JSON inventory data.')
            if isinstance(data, dict) and isinstance(data.get('meta'), dict) and data['meta'].get('rc') == 'error':
                raise UniFiError('The controller returned an API error. Verify permissions and local API access.')
            return data
        except httpx.ConnectError as exc:
            if 'CERTIFICATE_VERIFY_FAILED' in str(exc) or 'certificate verify failed' in str(exc).lower():
                raise UniFiError('Certificate validation failed. Install/trust the Cloud Key certificate, or explicitly enable the self-signed certificate option on a trusted LAN.') from None
            raise UniFiError('Cannot reach Cloud Key. Check the local HTTPS address, routing and firewall.') from None
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout):
            raise UniFiError('Cloud Key did not respond before the connection timeout.') from None
        except (httpx.HTTPError, ValueError):
            raise UniFiError('Unable to read the local UniFi Network API response.') from None

    @staticmethod
    def unwrap(body):
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            if isinstance(body.get('data'), list):
                return body['data']
            if isinstance(body.get('sites'), list):
                return body['sites']
        raise UniFiError('Unexpected response format from Network API.')

    def _prefix(self):
        return '/proxy/network' if self.active_mode == 'unifios' else ''

    def _attempt(self, candidate):
        self.http.cookies.clear()
        if self.auth == 'password':
            endpoint = '/api/auth/login' if candidate == 'unifios' else '/api/login'
            self.request_json('POST', endpoint, {'username': self._login_username, 'password': self._login_password})
        root = '/proxy/network' if candidate == 'unifios' else ''
        body = self.request_json('GET', root + '/api/self/sites')
        sites = self.unwrap(body)
        if not isinstance(sites, list):
            raise UniFiError('No accessible sites were returned.')
        return [{'id': str(s.get('name', '')), 'name': str(s.get('desc') or s.get('name') or '')}
                for s in sites if isinstance(s, dict) and s.get('name')]

    def connect(self):
        candidates = ['unifios', 'standalone'] if self.mode == 'auto' else [self.mode]
        failures = []
        for candidate in candidates:
            try:
                sites = self._attempt(candidate)
                self.active_mode = candidate
                # No need to retain a user's password after the initial login.
                self._login_password = ''
                self._login_username = ''
                return sites
            except UniFiError as exc:
                failures.append(str(exc))
        # Avoid exposing sensitive URLs/tokens/passwords, preserve useful diagnostic.
        if failures and any('Certificate validation' in f for f in failures):
            raise UniFiError(next(f for f in failures if 'Certificate validation' in f))
        if failures and all('denied access' in f for f in failures):
            raise UniFiError('Controller rejected the credentials or API key. For complete per-port detail, try a local UniFi Network account with site read access.')
        raise UniFiError('; '.join(dict.fromkeys(failures)) or 'Could not connect to controller.')

    def list_sites(self):
        if not self.active_mode:
            raise UniFiError('Not connected to a Cloud Key.')
        body = self.request_json('GET', self._prefix() + '/api/self/sites')
        return [{'id': str(s.get('name')), 'name': str(s.get('desc') or s.get('name'))}
                for s in self.unwrap(body) if isinstance(s, dict) and s.get('name')]

    def site_data(self, site_id: str):
        # Protect against path traversal/injection; caller must validate site membership too.
        safe = quote(site_id, safe='')
        base = f'{self._prefix()}/api/s/{safe}'
        devices = self.unwrap(self.request_json('GET', base + '/stat/device'))
        clients = self.unwrap(self.request_json('GET', base + '/stat/sta'))
        # Some firmware hides optional config endpoints behind access permissions.
        options = {}
        for label, suffix in (('profiles', '/rest/portconf'), ('networks', '/rest/networkconf')):
            try:
                options[label] = self.unwrap(self.request_json('GET', base + suffix))
            except UniFiError:
                options[label] = []
        return devices, clients, options['profiles'], options['networks']
