# UniFi PortScope v1.0

**Graphical, site-scoped UniFi switchport and access point reporting.** A lightweight read-only web application for a local UniFi Cloud Key / Cloud Key+ running UniFi Network. Works with standalone UniFi Network controllers too.

## What it does

- Connects by HTTPS to the **local Cloud Key** using a local Network API key or a local UniFi administrator account.
- Lists UniFi sites available to the connected account. Select exactly one site to query.
- Shows every reported managed **UniFi switch** in a graphical port-face view, with up/down/unknown link status, negotiated link rate, PoE draw, port profile, VLAN where reported, counters, and discovered LLDP/connected clients.
- Shows every reported **UniFi access point** with status, IP, model, firmware, client count and **confirmed** uplink switch/port (when the controller reports it).
- Detailed port inspector; search; connected / PoE / AP / errors filters; printable landscape report (also use **Print to PDF**); CSV export (switchports **and** AP inventory) and JSON export.
- Default synthetic demo mode; authenticates the PortScope dashboard separately from the Cloud Key.
- Uses only **GET** methods for controller inventory reads. The only controller POST is the local sign-in endpoint when using username/password. **Never provisions, resets, adopts, cycles PoE, or edits network settings.**

### Supported API method

This build uses the local **classic Network API**, which still supplies valuable `port_table` and `lldp_table` data in UniFi Network 10.x. The controller supplies the truth; if it omits a port attribute, PortScope prints an em dash rather than making one up. Port layout is a generic index-based approximation, not a pixel-perfect front panel for every UniFi model.

UniFi OS Cloud Key routes:

```text
POST /api/auth/login              # only when username/password authentication is selected
GET  /proxy/network/api/self/sites
GET  /proxy/network/api/s/{site}/stat/device
GET  /proxy/network/api/s/{site}/stat/sta
GET  /proxy/network/api/s/{site}/rest/portconf
GET  /proxy/network/api/s/{site}/rest/networkconf
```

Standalone Network controllers use `POST /api/login` and no `/proxy/network` prefix. A local Network API key, when supported on classic endpoints, is sent in `X-API-Key`; whether a key is permitted to read legacy endpoints depends on the controller version and key permissions. **If the API key authenticates but the controller won't return port details, try a local Network account.** Cloud **Site Manager API** keys do *not* provide direct local port-level access and aren't interchangeable with local console API keys.

## Windows 11 / Windows Server 2025 (quick start)

1. Extract the ZIP to a folder, for example `C:\Tools\UniFi-PortScope`.
2. Install **Python 3.11+** and ensure the `py` launcher works (run `py -3 --version`).
3. Double-click `start-windows.bat` (initial run installs the small Python dependencies into `.venv`).
4. The terminal shows a generated **dashboard password**. Open <http://127.0.0.1:8899> on **that same computer** and sign in.
5. Click **Connection settings**, enter the local Cloud Key URL, and choose **Local Network API key** or **Local admin account**. Then choose your site.

The default Windows launcher only listens on `127.0.0.1`, so no one else on the network can open it. To make the dashboard available to IT staff from other computers, put it behind an authenticated **HTTPS reverse proxy**, such as IIS or your existing secure internal app gateway; do not directly expose an unencrypted dashboard on the LAN.

### Persistent dashboard password on Windows

Run the following in the same command prompt before launching:

```bat
set PORTSCOPE_ADMIN_PASSWORD=YOUR_LONG_UNIQUE_PASSWORD
set PORTSCOPE_SESSION_SECRET=YOUR_LONG_RANDOM_32_PLUS_CHARACTER_STRING
start-windows.bat
```

Use long random strings. If you use HTTPS for the PortScope web interface itself, set `PORTSCOPE_SECURE_COOKIES=1`. Don't set this for the default plain-HTTP localhost setup because the browser will then refuse the cookie.

## Docker Compose (Linux, NAS, Windows Docker Desktop)

Copy `.env.example` to `.env` and replace **both** placeholders with long, random values. Do not put a UniFi API key or password in `.env`—the connection screen keeps controller secrets only in the running process.

```bash
cp .env.example .env
# edit .env, replace the placeholders
docker compose up --build -d
```

Open <http://127.0.0.1:8899>. The Docker compose configuration intentionally binds loopback (`127.0.0.1`) rather than exposing port 8899 to the whole LAN.

`docker compose logs -f` shows server errors if a connection fails. API keys and controller passwords aren't logged by the application. If you change `.env`, recreate the container with `docker compose up -d --force-recreate`.

## Local Cloud Key connection checklist

- Be on the same trusted LAN or a VPN with routing to the Cloud Key's **management address**.
- UniFi OS Cloud Key usually uses `https://CLOUDKEY-IP` (443). Standalone Network often uses `https://SERVER-IP:8443`.
- Make sure the selected account can **read the target UniFi Network site and devices**.
- Local API keys can be created under UniFi Network **Settings → Integrations** (the exact navigation varies by Network version). For switchport data, you may need the local controller account if classic endpoints deny the key.
- Default setting is **certificate verification ON**. For an untrusted/self-signed local Cloud Key certificate, install the certificate/CA on the PortScope host (for custom CA bundles, set `PORTSCOPE_CA_BUNDLE` to the trusted PEM path before starting); alternatively, you may explicitly turn off verification while on an isolated trusted management LAN. This is less secure.
- Port values come from adopted **UniFi** switches. Third-party switches connected to the Cloud Key are not inventoriable by this API. Offline devices may have stale or missing live port values.
- Connected device / AP uplink mapping only appears when the controller provides a wired client association or AP uplink/LLDP. It is intentionally **not guessed** from IPs or names.

## Data retention and access controls

- The app runs a **single controller connection per process** and is intended for a trusted IT admin/team, not as a multi-tenant public service.
- Controller API key, auth cookies, and any temporary local login data are kept **in process memory only** and are never included in the site's CSV/JSON report.
- Local login password is discarded after it establishes a controller session; when that session expires, reconnect. The API key remains in process memory to authenticate reads, and is dropped when switching to demo or restarting the app.
- PortScope doesn't store historical snapshots; clicking export downloads a **point-in-time** report. JSON/CSV can contain network-sensitive IPs/MACs—store exports according to your organization's policy.
- Uses a separate PortScope dashboard password, signed HTTP-only SameSite cookies, same-origin custom-header enforcement, and request throttling on login.
- Avoid exposing PortScope or the Cloud Key directly to the public Internet.

## Compatibility notes / limitations

- Intended first for **UniFi Network 10.x**, including deployments around 10.6.x. Actual compatibility with your Cloud Key version is verified only once you connect; this package has been tested with simulated controller responses, **not** against your Cloud Key.
- This is a site inventory and switchport report, **not** a topology editor, switch configuration UI, historical traffic monitor or device-configuration management interface.
- Some UniFi controller firmware/API combinations do not expose every field, such as native VLAN, PoE budget, negotiated speed or LLDP neighbors. Missing data is shown as `—` or "not confirmed."
- Chrome/Edge Print → **Save as PDF** produces the printable landscape diagram report for the currently selected site. Report center also offers detailed CSV and JSON exports.
- A site is a UniFi **Network site**, not merely a named geographical location. Select among the sites to which your account has access.

## Testing

```bash
python -m pip install -r requirements.txt pytest
python -m pytest -q
```

Tests cover synthetic inventory, normalization, site boundaries, API allow-list, authentication, CSV export, and simulated Cloud Key endpoints. No hardware connection is needed for tests.

## References

- [Official UniFi Local Application API overview](https://help.ui.com/hc/en-us/articles/30076656117655-Getting-Started-with-the-Official-UniFi-API)
- [UniFi Network API Developer Portal](https://developer.ui.com/network/v10.0.162)
- [Community UniFi Network API endpoint reference](https://ubntwiki.com/products/software/unifi-controller/api)

PortScope is an independent reporting tool, not an official Ubiquiti product.
