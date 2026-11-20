"""Normalize UniFi Network inventory from classic and official APIs into one model.

UniFi changes field names across models and firmware releases. Missing observations
remain unknown: this module never invents negotiated links or device connections.
"""
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
import re

MAC_PATTERN = re.compile(r'[^0-9a-f]')


def mac(value: Any) -> str:
    if not value:
        return ''
    raw = MAC_PATTERN.sub('', str(value).lower())
    return ':'.join(raw[i:i + 2] for i in range(0, 12, 2)) if len(raw) == 12 else str(value).lower()


def number(value, default=None):
    if value is None or isinstance(value, bool):
        return default
    try:
        n = float(value)
        if not -1e18 < n < 1e18:
            return default
        return int(n) if n.is_integer() else round(n, 3)
    except (TypeError, ValueError, OverflowError):
        return default


def text(value, fallback='—') -> str:
    if value is None or value == '':
        return fallback
    return str(value)[:280]


def pick(obj, *keys):
    for k in keys:
        if isinstance(obj, dict) and obj.get(k) is not None:
            return obj[k]
    return None


def as_dict(v):
    return v if isinstance(v, dict) else {}


def as_list(v):
    return v if isinstance(v, list) else []


def device_status(d):
    state = d.get('state')
    if state is not None:
        status = str(state).strip().upper()
        if status in ('1', 'ONLINE', 'CONNECTED'):
            return 'online'
        if status in ('0', 'OFFLINE', 'DISCONNECTED', 'UNREACHABLE'):
            return 'offline'
        return 'unknown'
    for k in ('connected', 'online', 'is_connected'):
        if k in d and isinstance(d[k], bool):
            return 'online' if d[k] else 'offline'
    return 'unknown'


def device_type(d):
    value = str(pick(d, 'type', 'device_type', 'category') or '').lower()
    if value in ('usw', 'switch') or value.startswith('usw'):
        return 'switch'
    if value in ('uap', 'ap', 'access_point') or value.startswith('uap'):
        return 'ap'
    model = str(d.get('model', '')).upper()
    if model.startswith(('USW', 'US-', 'U6', 'U7', 'UAP')):
        return 'ap' if model.startswith(('U6', 'U7', 'UAP')) else 'switch'
    return None


def port_state(p):
    up = pick(p, 'up', 'link_up', 'is_link_up')
    if isinstance(up, bool):
        return 'up' if up else 'down'
    if str(up).lower() in ('true', 'false', '1', '0'):
        return 'up' if str(up).lower() in ('true', '1') else 'down'
    return 'unknown'


def connected_client(d):
    return {
        'name': text(pick(d, 'name', 'hostname', 'display_name', 'mac')),
        'ip': text(d.get('ip')),
        'mac': mac(d.get('mac')),
        'wired': bool(d.get('is_wired')),
    }


def port_type(p):
    media = str(pick(p, 'media', 'media_type', 'connector', 'type') or '').lower()
    name = str(pick(p, 'name', 'ifname', 'port_name') or '').lower()
    if 'sfp' in media or 'sfp' in name or '10g' in media:
        return 'SFP+' if any(x in media + name for x in ('+', '10g', '25g', 'plus')) else 'SFP'
    if media in ('rj45', 'ethernet', 'copper') or 'eth' in name:
        return 'RJ45'
    return 'Unknown'


def upstream(d, devices_by_mac):
    u = as_dict(d.get('uplink')) or as_dict(d.get('last_uplink'))
    parent_mac = mac(pick(u, 'uplink_mac', 'uplink_device_mac', 'remote_mac') or
                     pick(d, 'uplink_mac', 'uplink_device_mac'))
    idx = number(pick(u, 'uplink_remote_port', 'remote_port', 'uplink_port') or
                 pick(d, 'uplink_remote_port'))
    parent = devices_by_mac.get(parent_mac)
    if not parent or idx is None:
        return None
    return {'device': text(pick(parent, 'name', 'hostname', 'mac')),
            'device_mac': parent_mac, 'port_idx': idx}


def normalize(site: dict, devices: list, clients: list, profiles=None, networks=None,
              source='live') -> dict:
    profiles = as_list(profiles)
    networks = as_list(networks)
    devices = [d for d in as_list(devices) if isinstance(d, dict)]
    clients = [c for c in as_list(clients) if isinstance(c, dict)]
    devices_by_mac = {mac(d.get('mac')): d for d in devices if mac(d.get('mac'))}
    by_port = defaultdict(list)
    ap_clients = defaultdict(int)
    for c in clients:
        wired = c.get('is_wired') is True or str(c.get('is_wired')).lower() in ('1', 'true')
        if wired:
            sw_mac = mac(pick(c, 'sw_mac', 'switch_mac'))
            sw_port = number(pick(c, 'sw_port', 'switch_port'))
            if sw_mac and sw_port is not None:
                by_port[(sw_mac, sw_port)].append(connected_client(c))
        else:
            ap_mac = mac(pick(c, 'ap_mac', 'access_point_mac'))
            if ap_mac:
                ap_clients[ap_mac] += 1

    profiles_by_id = {str(p.get('_id') or p.get('id')): p for p in profiles if p.get('_id') or p.get('id')}
    networks_by_id = {str(n.get('_id') or n.get('id')): n for n in networks if n.get('_id') or n.get('id')}
    switches, aps = [], []
    for d in devices:
        kind = device_type(d)
        if kind not in ('switch', 'ap'):
            continue
        key = mac(d.get('mac'))
        common = {
            'name': text(pick(d, 'name', 'hostname', 'mac', 'model')),
            'model': text(pick(d, 'model', 'model_name')),
            'mac': key,
            'ip': text(pick(d, 'ip', 'ip_address')),
            'state': device_status(d),
            'firmware': text(pick(d, 'version', 'firmware_version')),
            'uplink': upstream(d, devices_by_mac),
        }
        if kind == 'ap':
            raw_count = number(pick(d, 'num_sta', 'num_clients'))
            aps.append({**common, 'clients_count': ap_clients.get(key, raw_count)})
            continue
        overrides = {number(p.get('port_idx')): p for p in as_list(d.get('port_overrides')) if isinstance(p, dict)}
        lldp_by_port = defaultdict(list)
        for item in as_list(d.get('lldp_table')):
            if not isinstance(item, dict):
                continue
            idx = number(pick(item, 'local_port_idx', 'port_idx', 'local_port_num'))
            if idx is None:
                continue
            lldp_by_port[idx].append({
                'name': text(pick(item, 'chassis_name', 'system_name', 'name', 'remote_port_id')),
                'mac': mac(pick(item, 'chassis_id', 'remote_chassis_id', 'mac')),
                'port': text(pick(item, 'port_id', 'remote_port', 'port')),
                'kind': 'LLDP',
            })
        ap_by_port = defaultdict(list)
        for other in devices:
            if other is d or device_type(other) != 'ap':
                continue
            parent = upstream(other, devices_by_mac)
            if parent and parent['device_mac'] == key:
                ap_by_port[parent['port_idx']].append({
                    'name': text(pick(other, 'name', 'hostname', 'mac')),
                    'mac': mac(other.get('mac')), 'port': 'AP uplink', 'kind': 'Access point'
                })
        rows = []
        for p in as_list(d.get('port_table')):
            if not isinstance(p, dict):
                continue
            idx = number(pick(p, 'port_idx', 'idx'))
            if idx is None:
                continue
            override = as_dict(overrides.get(idx))
            profile_id = pick(override, 'port_profile_id', 'portconf_id') or pick(p, 'port_profile_id', 'portconf_id')
            profile = as_dict(profiles_by_id.get(str(profile_id)))
            net_id = pick(override, 'native_networkconf_id') or pick(p, 'native_networkconf_id') or pick(profile, 'native_networkconf_id')
            net = as_dict(networks_by_id.get(str(net_id)))
            vlan = pick(override, 'native_vlan', 'vlan')
            if vlan is None:
                vlan = pick(p, 'native_vlan', 'vlan')
            if vlan is None:
                vlan = pick(net, 'vlan', 'vlan_id')
            poe_w = number(pick(p, 'poe_power', 'poe_power_w'))
            poe_mode = pick(override, 'poe_mode') or pick(p, 'poe_mode') or pick(profile, 'poe_mode')
            poe_enabled = (str(poe_mode).lower() not in ('off', 'none', 'disabled', '') if poe_mode is not None else None)
            if poe_enabled is None and isinstance(p.get('port_poe'), bool):
                poe_enabled = p['port_poe']
            neighbor_rows = ap_by_port.get(idx, []) + lldp_by_port.get(idx, [])
            # Avoid double-counting an AP repeated in LLDP and explicit uplink metadata.
            seen = set()
            neighbors = []
            for n in neighbor_rows:
                identity = n['mac'] or n['name']
                if identity not in seen:
                    seen.add(identity)
                    neighbors.append(n)
            rows.append({
                'idx': idx,
                'label': text(pick(override, 'name', 'port_name') or pick(p, 'name', 'ifname') or f'Port {idx}'),
                'kind': port_type(p),
                'state': port_state(p),
                'enabled': pick(override, 'port_enabled', 'enabled') if pick(override, 'port_enabled', 'enabled') is not None else pick(p, 'port_enabled', 'enabled'),
                'speed': number(pick(p, 'speed', 'speed_mbps')),
                'max_speed': number(pick(p, 'max_speed', 'max_speed_mbps')),
                'poe_enabled': poe_enabled,
                'poe_w': poe_w,
                'poe_mode': text(poe_mode),
                'profile': text(pick(profile, 'name') or profile_id),
                'vlan': vlan,
                'network': text(pick(net, 'name')),
                'rx_bytes': number(pick(p, 'rx_bytes', 'rx_byte')),
                'tx_bytes': number(pick(p, 'tx_bytes', 'tx_byte')),
                'rx_errors': number(pick(p, 'rx_errors')),
                'tx_errors': number(pick(p, 'tx_errors')),
                'clients': by_port.get((key, idx), []),
                'neighbors': neighbors,
                'is_ap_link': bool(ap_by_port.get(idx)),
                'is_uplink': bool(p.get('is_uplink')) or bool(p.get('uplink')) or number(as_dict(d.get('uplink')).get('uplink_port')) == idx,
            })
        rows.sort(key=lambda x: x['idx'])
        budget = number(pick(d, 'total_max_power', 'poe_budget', 'poe_budget_w'))
        total_poe = number(pick(d, 'total_power', 'poe_power'))
        if total_poe is None and any(p['poe_w'] is not None for p in rows):
            total_poe = round(sum(p['poe_w'] or 0 for p in rows), 2)
        switches.append({**common, 'ports': rows, 'port_count': len(rows),
                         'poe_total_w': total_poe, 'poe_budget_w': budget})
    switches.sort(key=lambda x: x['name'].lower())
    aps.sort(key=lambda x: x['name'].lower())
    all_ports = [p for s in switches for p in s['ports']]
    observed_poe = any(sw['poe_total_w'] is not None for sw in switches)
    kpis = {
        'switches': len(switches), 'aps': len(aps),
        'online_switches': sum(s['state'] == 'online' for s in switches),
        'online_aps': sum(a['state'] == 'online' for a in aps),
        'ports': len(all_ports),
        'active_ports': sum(p['state'] == 'up' for p in all_ports),
        'down_ports': sum(p['state'] == 'down' for p in all_ports),
        'unknown_ports': sum(p['state'] == 'unknown' for p in all_ports),
        'poe_w': round(sum(sw['poe_total_w'] or 0 for sw in switches), 1) if observed_poe else None,
        'clients': len(clients),
    }
    return {
        'site': {'id': str(site.get('id') or site.get('name') or 'default'),
                 'name': text(site.get('desc') or site.get('display_name') or site.get('name') or 'Site')},
        'source': source, 'collected_at': datetime.now(timezone.utc).isoformat(),
        'switches': switches, 'aps': aps, 'summary': kpis,
        'warnings': [f'{s["name"]}: controller did not return port_table.' for s in switches if not s['ports']],
    }
