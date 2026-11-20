import json
import pytest
from fastapi.testclient import TestClient
from app import main
from app.demo import demo_snapshot
from app.normalizer import normalize
from app.unifi import UniFi, UniFiError, validate_controller_url
import httpx


def test_demo_sites_isolation_and_ports():
    snap = demo_snapshot('demo-main')
    assert snap['source'] == 'demo'
    assert snap['summary']['switches'] == 3
    assert snap['summary']['aps'] == 4
    assert snap['summary']['ports'] == 80
    assert snap['summary']['active_ports'] > 20
    assert any(p['is_ap_link'] for s in snap['switches'] for p in s['ports'])
    assert any(p['neighbors'] for s in snap['switches'] for p in s['ports'])
    with pytest.raises(KeyError):
        demo_snapshot('unapproved-site')


def test_unknown_telemetry_is_not_invented():
    data = normalize({'name':'default'}, [
        {'type':'usw','mac':'aa:bb:cc:dd:ee:01','name':'Switch 1',
         'port_table':[{'port_idx':1, 'name':'uplink'}, {'port_idx':2,'up':False, 'speed':0}]},
        {'type':'uap','mac':'aa:bb:cc:dd:ee:02','name':'AP 1','uplink':{'uplink_mac':'aa:bb:cc:dd:ee:01','uplink_remote_port':1}},
    ], [])
    assert data['switches'][0]['ports'][0]['state']=='unknown'
    assert data['switches'][0]['ports'][0]['speed'] is None
    assert data['switches'][0]['ports'][0]['is_ap_link'] is True
    assert data['switches'][0]['ports'][1]['state']=='down'
    assert data['aps'][0]['uplink']['port_idx']==1


def test_url_validation():
    assert validate_controller_url('https://192.168.2.2') == 'https://192.168.2.2'
    assert validate_controller_url('https://cloudkey.dp.local') == 'https://cloudkey.dp.local'
    for value in ['http://10.0.0.1','https://user:pass@10.0.0.2','https://1.1.1.1','https://10.0.0.2/path','file:///etc/passwd','https://10.0.0.2/?key=secret']:
        with pytest.raises(UniFiError):
            validate_controller_url(value)


def test_read_only_controller_allowlist():
    instance=UniFi('https://10.0.0.2','unifios','api_key',api_key='x')
    try:
        with pytest.raises(UniFiError):
            instance.request_json('POST','/proxy/network/api/s/default/cmd/devmgr',{'cmd':'power-cycle'})
        with pytest.raises(UniFiError):
            instance.request_json('PUT','/proxy/network/api/s/default/rest/device')
    finally:
        instance.close()


def mock_cloudkey(request):
    path=request.url.path
    if path=='/api/auth/login':
        return httpx.Response(200,json={'meta':{'rc':'ok'}})
    if path=='/proxy/network/api/self/sites':
        return httpx.Response(200,json={'meta':{'rc':'ok'},'data':[{'name':'default','desc':'Office'}]})
    if path.endswith('/stat/device'):
        return httpx.Response(200,json={'meta':{'rc':'ok'},'data':[
            {'type':'usw','state':1,'model':'USW-Pro-24-PoE','name':'Switch','mac':'aa:bb:cc:00:00:01','port_table':[{'port_idx':1,'up':True,'speed':1000,'poe_power':9.1}]},
            {'type':'uap','state':1,'model':'U6-Pro','name':'Lobby AP','mac':'aa:bb:cc:00:00:02','uplink':{'uplink_mac':'aa:bb:cc:00:00:01','uplink_remote_port':1}}]})
    if path.endswith('/stat/sta'):
        return httpx.Response(200,json={'meta':{'rc':'ok'},'data':[]})
    if path.endswith('/rest/portconf') or path.endswith('/rest/networkconf'):
        return httpx.Response(200,json={'meta':{'rc':'ok'},'data':[]})
    return httpx.Response(404,json={'meta':{'rc':'error'}})


def test_mock_cloudkey_full_flow():
    instance=UniFi('https://10.0.0.2','unifios','password',username='localadmin',password='localpass')
    instance.http.close()
    instance.http=httpx.Client(transport=httpx.MockTransport(mock_cloudkey))
    try:
        assert instance.connect()==[{'id':'default','name':'Office'}]
        assert not instance._login_password
        devices, clients, profiles, nets=instance.site_data('default')
        snap=normalize({'name':'default','desc':'Office'},devices,clients,profiles,nets)
        assert snap['summary']['switches']==1
        assert snap['summary']['aps']==1
        assert snap['switches'][0]['ports'][0]['is_ap_link']
    finally:
        instance.close()


def test_app_security_and_export():
    with TestClient(main.app) as client:
        assert client.get('/api/sites').status_code==401
        assert client.get('/api/sites/demo-main/snapshot').status_code==401
        assert client.post('/api/login', json={'password':main.APP_PASSWORD}).status_code==403
        assert client.post('/api/login', headers={'X-Requested-With':'PortScope'},json={'password':main.APP_PASSWORD}).status_code==200
        response=client.get('/api/sites')
        assert response.status_code==200 and len(response.json()['sites'])==3
        detail=client.get('/api/sites/demo-main/snapshot')
        assert detail.status_code==200 and detail.json()['source']=='demo'
        assert client.get('/api/sites/not-real/snapshot').status_code==404
        assert client.get('/api/sites/../../../snapshot').status_code in (404, 400)
        csv=client.get('/api/sites/demo-main/export.csv')
        assert csv.status_code==200
        assert 'Content-Disposition' in csv.headers
        assert 'Building' in csv.text and 'Port profile' in csv.text and 'Access point' in csv.text
        assert client.get('/api/sites/demo-main/export.json').json()['summary']['aps']==4
        assert client.post('/api/logout',headers={'X-Requested-With':'PortScope'},json={}).status_code==200
        assert client.get('/api/sites').status_code==401


def test_csv_injection_defense():
    assert main.csv_safe('=SUM(A1:A2)') == "'=SUM(A1:A2)"
    assert main.csv_safe('+cmd') == "'+cmd"
    assert main.csv_safe('foo\nbar')=='foo bar'


def test_mock_api_key_does_not_send_device_writes():
    seen=[]
    def handler(request):
        seen.append((request.method, request.url.path, request.headers.get('x-api-key')))
        return mock_cloudkey(request)
    instance=UniFi('https://10.0.0.2','unifios','api_key',api_key='local-test-key')
    instance.http.close()
    instance.http=httpx.Client(transport=httpx.MockTransport(handler),headers={'X-API-Key':'local-test-key'})
    try:
        assert instance.connect()[0]['id']=='default'
        instance.site_data('default')
        assert len(seen)==5
        assert all(method=='GET' and key=='local-test-key' for method,path,key in seen)
        assert all('/cmd/' not in path and '/actions' not in path for method,path,key in seen)
    finally:
        instance.close()
