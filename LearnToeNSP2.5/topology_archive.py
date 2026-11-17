"""Portable interface records, keyed by topology device and port identities."""
import hashlib
import json


def topology_keys(topology):
    devices = topology.get('device_inventory', {})
    device_records = sorted((str(key), str(value.get('name', '')), str(value.get('model', '')))
                            for key, value in devices.items())
    keys = {}
    for index, link in enumerate(topology.get('connections', [])):
        for pair_index, pair in enumerate(link.get('interface_pairs', [])):
            a = (str(link['source_id']), str(pair.get('src_port_index')))
            b = (str(link['target_id']), str(pair.get('dst_port_index')))
            if a == b:
                raise ValueError('拓扑包含无法区分端点的自环')
            canonical = tuple(sorted((a, b)))
            if canonical in keys:
                raise ValueError('拓扑存在重复端口连线，无法安全导入或导出')
            keys[canonical] = (f'{index}:{pair_index}', a != canonical[0])
    identity = {'devices': device_records, 'links': sorted(keys)}
    fingerprint = hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
    return keys, fingerprint


def export_archive(topology, verified, manual, restored):
    keys, fingerprint = topology_keys(topology)
    entries = []
    for endpoints, (key, reverse) in keys.items():
        record = {}
        for name, values in (('manual', manual), ('previously_verified', verified or {}), ('restored', restored)):
            if key not in values or name == 'restored' and 'previously_verified' in record:
                continue
            value = values[key]
            a, b = value.get('source_interface', ''), value.get('target_interface', '')
            if reverse:
                a, b = b, a
            record['previously_verified' if name == 'restored' else name] = [a, b]
        if record:
            entries.append({'endpoints': endpoints, **record})
    return {'format': 'learn-to-ensp-interfaces', 'version': 1, 'fingerprint': fingerprint, 'links': entries}


def import_archive(topology, data):
    keys, fingerprint = topology_keys(topology)
    if not isinstance(data, dict) or data.get('format') != 'learn-to-ensp-interfaces' or data.get('version') != 1:
        raise ValueError('互联信息文件格式或版本无效')
    if data.get('fingerprint') != fingerprint:
        raise ValueError('文件与当前拓扑不匹配，请使用对应拓扑导出的文件')
    records = data.get('links')
    if not isinstance(records, list) or len(records) > len(keys):
        raise ValueError('互联信息记录数量无效')
    manual, restored, seen = {}, {}, set()
    for entry in records:
        if not isinstance(entry, dict) or not isinstance(entry.get('endpoints'), list):
            raise ValueError('链路记录格式无效')
        raw = entry['endpoints']
        if len(raw) != 2 or any(not isinstance(side, list) or len(side) != 2 or any(not isinstance(v, str) for v in side) for side in raw):
            raise ValueError('链路端点格式无效')
        endpoints = tuple(tuple(side) for side in raw)
        if endpoints not in keys or endpoints in seen:
            raise ValueError('链路不存在或记录重复')
        seen.add(endpoints)
        key, reverse = keys[endpoints]
        for field, output in (('manual', manual), ('previously_verified', restored)):
            if field not in entry:
                continue
            values = entry[field]
            if not isinstance(values, list) or len(values) != 2 or any(not isinstance(v, str) or len(v) > 64 or any(ord(c) < 32 or ord(c) == 127 for c in v) for v in values):
                raise ValueError('接口名称格式无效')
            if not any(v.strip() for v in values) or field == 'previously_verified' and not all(v.strip() for v in values):
                raise ValueError('接口名称不能为空')
            a, b = (v.strip() for v in values)
            if reverse:
                a, b = b, a
            output[key] = {'source_interface': a, 'target_interface': b}
    return manual, restored
