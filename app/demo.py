"""Clearly labeled synthetic data; never represents actual county infrastructure."""
from copy import deepcopy
from .normalizer import normalize

DEMO_SITES = [
    {'id': 'demo-main', 'name': 'Demo Main Campus'},
    {'id': 'demo-annex', 'name': 'Demo Annex'},
    {'id': 'demo-field', 'name': 'Demo Field Office'},
]


def demo_snapshot(site_id):
    site = next((s for s in DEMO_SITES if s['id'] == site_id), None)
    if not site:
        raise KeyError(site_id)
    variant = {'demo-main': (48, 24, 8, 4), 'demo-annex': (24, 8, 0, 2), 'demo-field': (8, 0, 0, 1)}[site_id]
    switches = []
    access_points = []
    clients = []
    specs = [(i, count) for i, count in enumerate(variant[:3], start=1) if count]
    for switch_no, count in specs:
        switch_mac = f'02:44:10:{site_id[-1].encode().hex()[:2]:0>2}:00:{switch_no:02x}'
        ports = []
        for idx in range(1, count + 1):
            up = idx <= (count * 3 // 4) and not (site_id == 'demo-field' and switch_no == 1)
            sfp = idx > count - 2 if count >= 24 else False
            speed = 10000 if sfp and up else (100 if idx % 13 == 0 else 1000) if up else 0
            poe = bool(up and not sfp and (idx % 3 != 0))
            ports.append({'port_idx': idx, 'name': f'Port {idx}', 'up': up,
                          'speed': speed, 'media': 'SFP+ 10G' if sfp else 'RJ45',
                          'poe_mode': 'auto' if not sfp else 'off',
                          'port_poe': poe, 'poe_power': round(4.2 + idx * 0.27, 1) if poe else 0,
                          'rx_bytes': 103210000 * idx if up else 0,
                          'tx_bytes': 45321000 * idx if up else 0,
                          'rx_errors': (2 if idx == 13 else 0),
                          'tx_errors': 0,
                          'portconf_id': 'p-office' if idx % 5 else 'p-voice',
                          'is_uplink': idx == 1 and switch_no > 1})
            if up and not sfp and idx % 4 == 0:
                clients.append({'is_wired': True, 'sw_mac': switch_mac, 'sw_port': idx,
                                'mac': f'02:ac:ce:{switch_no:02x}:{idx:02x}:01',
                                'name': ['Front Desk PC', 'Security Camera', 'Finance Workstation', 'VoIP Phone'][idx % 4],
                                'ip': f'10.5.{switch_no}.{20 + idx}'})
        switches.append({'name': f'Building {switch_no} · {count}-Port PoE Switch',
                         'type': 'usw', 'mac': switch_mac, 'ip': f'10.5.10.{10 + switch_no}',
                         'model': f'USW-Pro-{count}-PoE' if count > 8 else 'USW-Lite-8-PoE',
                         'state': 1 if site_id != 'demo-field' or switch_no != 1 else 0,
                         'version': '7.2.47', 'port_table': ports,
                         'total_max_power': count * 11, 'port_overrides': [
                             {'port_idx': 4, 'name': 'Reception / PC', 'portconf_id': 'p-office'},
                             {'port_idx': 5, 'name': 'Voice VLAN', 'portconf_id': 'p-voice'}],
                         'lldp_table': []})
    for i in range(variant[3]):
        sw = switches[i % len(switches)]
        port = 2 + i * 2
        ap_mac = f'02:44:ee:11:00:{i+1:02x}'
        access_points.append({'type': 'uap', 'name': f'AP · {"Lobby" if i==0 else "Hallway" if i==1 else "Meeting Room" if i==2 else "West Wing"}',
                              'mac': ap_mac, 'ip': f'10.5.20.{20+i}', 'model': 'U7-Pro',
                              'state': 1 if i != 3 else 0, 'version': '8.0.18',
                              'num_sta': [19, 12, 7, 0][i],
                              'uplink': {'uplink_mac': sw['mac'], 'uplink_remote_port': port}})
        for ci in range([19, 12, 7, 0][i]):
            clients.append({'is_wired': False, 'ap_mac': ap_mac,
                            'mac': f'02:ee:{i+1:02x}:01:{ci:02x}:01',
                            'name': f'Wi-Fi Client {ci+1}', 'ip': f'10.5.30.{ci + i*30 + 10}'})
    profiles = [{'_id': 'p-office', 'name': 'Office / Data'},
                {'_id': 'p-voice', 'name': 'Voice / VLAN 30'}]
    snapshot = normalize({'name': site['id'], 'desc': site['name']}, switches + access_points,
                         clients, profiles, [], source='demo')
    return deepcopy(snapshot)
