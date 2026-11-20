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


# The *supported* key API returns UUID site IDs and paginated device/client
# lists, unlike the classic Network API. Simulate the official 10.6 schema.
OFFICIAL_SITE = '6ecfe335-5555-4efb-9ed8-81e3dbe4befe'
OFFICIAL_SWITCH = '599a435a-01bf-48c8-867d-35c4a5639cd1'
OFFICIAL_AP = '40b87937-8817-4ac2-9ea7-fcdabc073f48'


def official_mock(request):
    path = request.url.path
    offset = int(request.url.params.get('offset', 0))
    if path == '/proxy/network/integration/v1/sites':
        return httpx.Response(200, json={'data': [{'id': OFFICIAL_SITE, 'internalReference':'default', 'name':'County Office'}],
                                         'offset':0, 'limit':200, 'count':1, 'totalCount':1})
    prefix = f'/proxy/network/integration/v1/sites/{OFFICIAL_SITE}'
    if path == prefix + '/devices':
        payload = [
            {'id': OFFICIAL_SWITCH, 'model':'USW-24-PoE', 'features':['switching'], 'interfaces':['ports']},
            {'id': OFFICIAL_AP, 'model':'U6-Pro', 'features':['accessPoint'], 'interfaces':['radios']},
            {'id':'11111111-1111-1111-1111-111111111111', 'model':'UDM',
             'features':['routing','switching'], 'interfaces':['ports']}]
        return httpx.Response(200, json={'data':payload,'offset':offset,'limit':200,'count':len(payload),'totalCount':3})
    if path == prefix + '/devices/' + OFFICIAL_SWITCH:
        return httpx.Response(200, json={
            'id':OFFICIAL_SWITCH,'name':'Building Switch','macAddress':'aa:bb:cc:00:00:01',
            'ipAddress':'10.0.0.33','model':'USW-24-PoE','state':'ONLINE',
            'firmwareVersion':'7.0.0','features':{'switching': {'lags':[]}},
            'interfaces':{'ports':[
                {'idx':1,'connector':'RJ45','state':'UP','speedMbps':1000,
                 'maxSpeedMbps':2500,'poe':{'enabled':True,'type':3,'standard':'802.3bt'}},
                {'idx':2,'connector':'SFP_PLUS','state':'DOWN','maxSpeedMbps':10000},
                {'idx':3,'state':'UNKNOWN'}]}})
    if path == prefix + '/devices/' + OFFICIAL_AP:
        return httpx.Response(200, json={
            'id':OFFICIAL_AP,'name':'Hall AP','macAddress':'aa:bb:cc:00:00:02',
            'ipAddress':'10.0.0.34','model':'U6-Pro','state':'OFFLINE',
            'firmwareVersion':'6.6.65','features':{'accessPoint':{}},'interfaces':{'radios':[]},
            'uplink':{'deviceId':OFFICIAL_SWITCH}})
    if path == prefix + '/clients':
        return httpx.Response(200, json={
            'data':[{'id':'c1','name':'Server','macAddress':'00:11:22:33:44:55','ipAddress':'10.0.0.150','type':'WIRED'}],
            'offset':0, 'limit':200,'count':1,'totalCount':1})
    return httpx.Response(404,json={'message':'Not found'})


def test_mock_api_key_does_not_send_device_writes():
    seen=[]
    def handler(request):
        seen.append((request.method, request.url.path, request.headers.get('x-api-key')))
        return official_mock(request)
    instance=UniFi('https://10.0.0.2','unifios','api_key',api_key='local-test-key')
    instance.http.close()
    instance.http=httpx.Client(transport=httpx.MockTransport(handler),headers={'X-API-Key':'local-test-key'})
    try:
        assert instance.connect()==[{'id':OFFICIAL_SITE,'name':'County Office'}]
        devices,clients,profiles,nets=instance.site_data(OFFICIAL_SITE)
        assert len(devices)==2 and len(clients)==1
        snap=normalize({'name':OFFICIAL_SITE,'desc':'County Office'},devices,clients,profiles,nets)
        assert snap['summary']['switches']==1 and snap['summary']['aps']==1
        assert snap['summary']['online_switches']==1 and snap['summary']['online_aps']==0
        assert snap['summary']['poe_w'] is None, 'Official API does not provide PoE watts'
        ports=snap['switches'][0]['ports']
        assert len(ports)==3 and ports[0]['state']=='up' and ports[1]['state']=='down'
        assert ports[2]['state']=='unknown' and ports[2]['poe_enabled'] is None
        assert ports[0]['speed']==1000 and ports[0]['max_speed']==2500
        assert ports[0]['poe_enabled'] is True and ports[0]['poe_w'] is None
        assert ports[1]['kind']=='SFP+'
        assert snap['aps'][0]['uplink'] is None, 'Never invent a parent port index'
        assert snap['aps'][0]['clients_count'] is None, 'No per-AP client counts in this response'
        assert all(method=='GET' and key=='local-test-key' for method,path,key in seen)
        assert all('/integration/v1/' in path for _,path,_ in seen)
        assert all('/actions' not in path for _,path,_ in seen)
    finally:
        instance.close()


def test_official_pages_fetch_every_page():
    calls=[]
    def handler(request):
        offset=int(request.url.params.get('offset', 0))
        calls.append(offset)
        batch=[{'id': str(i)} for i in range(offset,min(offset+200,205))]
        return httpx.Response(200,json={'data':batch,'offset':offset,'count':len(batch),'limit':200,'totalCount':205})
    instance=UniFi('https://10.0.0.2','unifios','api_key',api_key='local-test-key')
    instance.http.close()
    instance.http=httpx.Client(transport=httpx.MockTransport(handler))
    try:
        assert len(instance._official_pages('/proxy/network/integration/v1/sites'))==205
        assert calls==[0,200]
    finally:
        instance.close()


def test_official_pages_reject_unexpected_response():
    instance=UniFi('https://10.0.0.2','unifios','api_key',api_key='local-test-key')
    instance.http.close()
    instance.http=httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200,json={'data':{'not':'a-list'}})))
    try:
        with pytest.raises(UniFiError, match='Unexpected response'):
            instance._official_pages('/proxy/network/integration/v1/sites')
    finally:
        instance.close()
