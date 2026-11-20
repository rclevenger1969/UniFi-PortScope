"""UniFi PortScope API and static frontend.

Designed for a single administrative operator on localhost or behind a trusted
HTTPS reverse proxy. Controller credentials are never written to disk.
"""
from contextlib import asynccontextmanager
import csv
import hmac
import io
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from .demo import DEMO_SITES, demo_snapshot
from .normalizer import normalize
from .unifi import UniFi, UniFiError

BASE = Path(__file__).parent
APP_PASSWORD = os.getenv('PORTSCOPE_ADMIN_PASSWORD') or secrets.token_urlsafe(13)
if not os.getenv('PORTSCOPE_ADMIN_PASSWORD'):
    # Visible in the launch terminal / Docker logs; never exposed via HTTP.
    print('\n' + '=' * 62 + '\nUniFi PortScope generated admin password: ' + APP_PASSWORD +
          '\nSet PORTSCOPE_ADMIN_PASSWORD for a persistent password.\n' + '=' * 62 + '\n', flush=True)
COOKIE_SECRET = os.getenv('PORTSCOPE_SESSION_SECRET') or secrets.token_urlsafe(32)
SECURE_COOKIES = os.getenv('PORTSCOPE_SECURE_COOKIES', '0') == '1'
_lock = threading.RLock()
_connection = None
_failed = {}
log = logging.getLogger('portscope')


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    with _lock:
        if _connection:
            _connection.close()


app = FastAPI(title='UniFi PortScope', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=COOKIE_SECRET, https_only=SECURE_COOKIES,
                   same_site='strict', session_cookie='portscope_session', max_age=8*60*60)
app.mount('/static', StaticFiles(directory=str(BASE / 'static')), name='static')


@app.middleware('http')
async def security_headers(req: Request, call_next):
    if req.method not in ('GET', 'HEAD', 'OPTIONS'):
        # JSON + a custom header enforce browser same-origin for state changes.
        if req.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
            return JSONResponse({'detail': 'JSON request required.'}, status_code=415)
        if req.headers.get('x-requested-with') != 'PortScope':
            return JSONResponse({'detail': 'Same-origin request header required.'}, status_code=403)
    resp = await call_next(req)
    resp.headers['Cache-Control'] = 'no-store'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-Frame-Options'] = 'DENY'
    resp.headers['Referrer-Policy'] = 'no-referrer'
    resp.headers['Content-Security-Policy'] = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    return resp


def require_auth(request: Request):
    if request.session.get('authorized') is not True:
        raise HTTPException(401, 'Sign in to PortScope.')
    return True


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class ConnectBody(BaseModel):
    url: str = Field(max_length=250)
    mode: str = 'auto'
    auth: str = 'api_key'
    api_key: str = Field(default='', max_length=1024)
    username: str = Field(default='', max_length=250)
    password: str = Field(default='', max_length=1024)
    verify: bool = True


def safe_client_error(err):
    return HTTPException(status_code=422, detail=str(err))


@app.get('/', include_in_schema=False)
def home():
    return FileResponse(BASE / 'static' / 'index.html')


@app.get('/api/healthz')
def healthz():
    return {'status': 'ok'}


@app.get('/api/session')
def session(req: Request):
    return {'authenticated': req.session.get('authorized') is True}


@app.post('/api/login')
def login(body: LoginBody, req: Request):
    ip = req.client.host if req.client else 'unknown'
    now = time.monotonic()
    with _lock:
        attempts = [t for t in _failed.get(ip, []) if now - t < 300]
        if len(attempts) >= 8:
            raise HTTPException(429, 'Too many attempts. Try again after five minutes.')
        if not hmac.compare_digest(body.password.encode(), APP_PASSWORD.encode()):
            attempts.append(now)
            _failed[ip] = attempts
            raise HTTPException(401, 'Incorrect dashboard password.')
        _failed.pop(ip, None)
    req.session.clear()
    req.session['authorized'] = True
    return {'ok': True}


@app.post('/api/logout', dependencies=[Depends(require_auth)])
def logout(req: Request):
    req.session.clear()
    return {'ok': True}


@app.get('/api/connection', dependencies=[Depends(require_auth)])
def get_connection():
    with _lock:
        return {'source': 'live' if _connection else 'demo',
                'connected': _connection is not None,
                'url': _connection.url if _connection else None,
                'mode': _connection.active_mode if _connection else None,
                'auth': _connection.auth if _connection else None,
                'verify': _connection.verify if _connection else True}


@app.post('/api/connection', dependencies=[Depends(require_auth)])
def set_connection(body: ConnectBody):
    global _connection
    try:
        candidate = UniFi(body.url, body.mode, body.auth, api_key=body.api_key,
                          username=body.username, password=body.password, verify=body.verify)
        try:
            sites = candidate.connect()
        except Exception:
            candidate.close()
            raise
    except UniFiError as exc:
        raise safe_client_error(exc)
    with _lock:
        old = _connection
        _connection = candidate
        if old:
            old.close()
    return {'ok': True, 'sites': sites, 'mode': candidate.active_mode}


@app.post('/api/demo', dependencies=[Depends(require_auth)])
def reset_demo():
    global _connection
    with _lock:
        if _connection:
            _connection.close()
        _connection = None
    return {'ok': True}


@app.get('/api/sites', dependencies=[Depends(require_auth)])
def sites():
    with _lock:
        if _connection:
            try:
                return {'sites': _connection.list_sites(), 'source': 'live'}
            except UniFiError as exc:
                raise HTTPException(502, str(exc))
    return {'sites': DEMO_SITES, 'source': 'demo'}


def snapshot_for(site_id: str):
    if len(site_id) > 110 or not re.fullmatch(r'[A-Za-z0-9_-]+', site_id):
        raise HTTPException(404, 'Site not found.')
    with _lock:
        if not _connection:
            try:
                return demo_snapshot(site_id)
            except KeyError:
                raise HTTPException(404, 'Site not found.')
        try:
            sites = _connection.list_sites()
            match = next((s for s in sites if s['id'] == site_id), None)
            if not match:
                raise HTTPException(404, 'Site not found or not accessible to this account.')
            devices, clients, profiles, networks = _connection.site_data(site_id)
            return normalize({'name': match['id'], 'desc': match['name']},
                             devices, clients, profiles, networks, source='live')
        except UniFiError as exc:
            raise HTTPException(502, str(exc))


@app.get('/api/sites/{site_id}/snapshot', dependencies=[Depends(require_auth)])
def get_snapshot(site_id: str):
    return snapshot_for(site_id)


def csv_safe(value):
    if value is None or value == '—':
        return ''
    s = str(value).replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')
    if s.lstrip().startswith(('=', '+', '-', '@')):
        s = "'" + s
    return s


@app.get('/api/sites/{site_id}/export.csv', dependencies=[Depends(require_auth)])
def csv_report(site_id: str):
    data = snapshot_for(site_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['Record type', 'Site', 'Device', 'Model', 'IP', 'MAC', 'Device status',
                     'Port', 'Port label', 'Media', 'Link', 'Speed Mbps', 'PoE watts',
                     'PoE mode', 'Port profile', 'VLAN', 'Native network', 'Attached clients / AP clients',
                     'Neighbors / AP uplink', 'RX errors', 'TX errors'])
    for sw in data['switches']:
        for p in sw['ports']:
            writer.writerow([csv_safe(v) for v in [
                'Switchport', data['site']['name'], sw['name'], sw['model'], sw['ip'], sw['mac'], sw['state'],
                p['idx'], p['label'], p['kind'], p['state'], p['speed'], p['poe_w'], p['poe_mode'],
                p['profile'], p['vlan'], p['network'],
                ', '.join(c['name'] for c in p['clients']),
                ', '.join(n['name'] for n in p['neighbors']), p['rx_errors'], p['tx_errors']]])
    for ap in data['aps']:
        uplink = (f"{ap['uplink']['device']} / port {ap['uplink']['port_idx']}"
                  if ap.get('uplink') else '')
        writer.writerow([csv_safe(v) for v in [
            'Access point', data['site']['name'], ap['name'], ap['model'], ap['ip'], ap['mac'],
            ap['state'], '', '', '', '', '', '', '', '', '', '', ap['clients_count'],
            uplink, '', '']])
    return Response(content=buf.getvalue(), media_type='text/csv; charset=utf-8', headers={
        'Content-Disposition': f'attachment; filename="portscope-{site_id}.csv"'})


@app.get('/api/sites/{site_id}/export.json', dependencies=[Depends(require_auth)])
def json_report(site_id: str):
    return Response(content=json.dumps(snapshot_for(site_id), indent=2),
                    media_type='application/json', headers={
                        'Content-Disposition': f'attachment; filename="portscope-{site_id}.json"'})
