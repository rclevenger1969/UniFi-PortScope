"""Adapt UniFi's supported local Integration API to PortScope's read-only display.

Official Integration v1 has different site IDs and device field names from the
older controller API. It exposes physical ports but not every legacy port stat.
Absent fields stay absent rather than being guessed.
"""


def is_reportable(device):
    if not isinstance(device, dict):
        return False
    features = device.get('features') or []
    interfaces = device.get('interfaces') or []
    if isinstance(features, dict):
        features = list(features)
    if isinstance(interfaces, dict):
        interfaces = list(interfaces)
    tags = {str(tag).lower() for tag in features}
    iface_tags = {str(tag).lower() for tag in interfaces}
    if 'accesspoint' in tags or 'access_point' in tags:
        return True
    if 'switching' in tags and 'routing' not in tags and 'gateway' not in tags:
        return True
    model = str(device.get('model') or '').upper()
    if model.startswith(('USW', 'US-', 'UAP', 'U6-', 'U7-')):
        return True
    # Some combined network appliances also advertise ports; those are not
    # dedicated switches and should not be classified by port presence alone.
    return 'radios' in iface_tags and 'ports' not in iface_tags


def device_to_classic(raw, by_id):
    """Translate official device details, conservatively preserving unknowns."""
    features = raw.get('features') or {}
    if isinstance(features, dict):
        tags = {str(k).lower() for k in features}
    else:
        tags = {str(k).lower() for k in features} if isinstance(features, list) else set()
    model = str(raw.get('model') or '').upper()
    is_ap = ('accesspoint' in tags or 'access_point' in tags or
             model.startswith(('UAP', 'U6-', 'U7-')))
    ports = []
    interfaces = raw.get('interfaces') if isinstance(raw.get('interfaces'), dict) else {}
    for port in interfaces.get('ports', []) if isinstance(interfaces.get('ports'), list) else []:
        if not isinstance(port, dict):
            continue
        pstate = str(port.get('state') or '').upper()
        poe = port.get('poe') if isinstance(port.get('poe'), dict) else {}
        ports.append({
            'port_idx': port.get('idx'),
            'name': port.get('name'),
            'media': port.get('connector'),
            'up': True if pstate == 'UP' else False if pstate == 'DOWN' else None,
            'speed': port.get('speedMbps'),
            'max_speed': port.get('maxSpeedMbps'),
            'port_poe': poe.get('enabled'),
            # No native VLAN, LLDP, power draw, or byte/error counters in this
            # API's documented device interface representation.
        })
    uplink = raw.get('uplink') if isinstance(raw.get('uplink'), dict) else {}
    parent_id = uplink.get('deviceId')
    parent = by_id.get(str(parent_id)) if parent_id else None
    # The official API can identify the parent device but generally not its
    # *physical* port index. Do not manufacture a switchport association.
    result = {
        'type': 'uap' if is_ap else 'usw',
        'name': raw.get('name'),
        'model': raw.get('model'),
        'ip': raw.get('ipAddress'),
        'mac': raw.get('macAddress'),
        'state': raw.get('state'),
        'version': raw.get('firmwareVersion'),
        'port_table': ports if not is_ap else [],
    }
    if parent and uplink.get('portIdx') is not None:
        result['uplink'] = {
            'uplink_mac': parent.get('macAddress'),
            'uplink_remote_port': uplink['portIdx'],
        }
    return result


def client_to_classic(client):
    ctype = str(client.get('type') or '').upper()
    return {
        'name': client.get('name'),
        'mac': client.get('macAddress'),
        'ip': client.get('ipAddress'),
        'is_wired': ctype == 'WIRED',
    }
