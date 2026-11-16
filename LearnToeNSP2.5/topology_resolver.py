"""Connect topology devices via MCP and conservatively verify LLDP link endpoints."""
import asyncio
import json
import re
from collections import Counter

from fastmcp import Client
from session_manager import session_manager

MCP_URL = 'http://127.0.0.1:5001/sse'
NEIGHBOR_COMMAND = 'display lldp neighbor brief'


def skipped_device_type(device):
    model = str(device.get('model', '')).strip().upper()
    if model == 'PC':
        return 'PC'
    if model == 'SERVER':
        return 'Server'
    if re.fullmatch(r'AP(?:\d[A-Z0-9-]*)?', model):
        return 'AP'
    return None


def command_body(output, command):
    """Reject incomplete, paginated, non-echoed or failed command output."""
    if not isinstance(output, str) or command.lower() not in output.lower():
        return ''
    body = output[output.lower().rfind(command.lower()) + len(command):]
    if re.search(r'----\s*More\s*----|\bError:|Unrecognized command|Wrong parameter', body, re.I):
        return ''
    if not re.search(r'(?:<[^\r\n<>]+>|\[[^\r\n\[\]]+\])\s*(?:\r?\n|$)', body):
        return ''
    return body


def interface_key(name):
    value = re.sub(r'\s+', '', name).lower()
    aliases = (('gigabitethernet', 'ge'), ('10ge', 'xge'), ('xgigabitethernet', 'xge'),
               ('ethernet', 'eth'), ('eth-trunk', 'eth-trunk'))
    for long_name, short in aliases:
        if value.startswith(long_name):
            return short + value[len(long_name):]
    return value


def parse_interfaces(body):
    interfaces = {}
    for line in body.splitlines():
        match = re.match(r'^\s*([A-Za-z][\w./:-]*\d[\w./:-]*)\s+\*?(?:up|down|administratively\s+down)', line, re.I)
        if match:
            name = match.group(1)
            key = interface_key(name)
            if key in interfaces and interfaces[key] != name:
                interfaces[key] = None
            else:
                interfaces[key] = name
    return interfaces


def parse_local_name(body):
    matches = re.findall(r'^\s*System\s+name\s*:\s*(\S.*?)\s*$', body, re.I | re.M)
    unique = {name.strip() for name in matches if name.strip() and name.strip() not in ('-', '--')}
    return next(iter(unique)) if len(unique) == 1 else None


def parse_neighbors(body):
    """Accept only LLDP brief tables with all four expected columns."""
    lines = body.splitlines()
    header = next((i for i, line in enumerate(lines)
                   if re.search(r'Local\s+Intf', line, re.I)
                   and re.search(r'Neighbor\s+Dev', line, re.I)
                   and re.search(r'Neighbor\s+Intf', line, re.I)
                   and re.search(r'Exp\w*', line, re.I)), None)
    if header is None:
        return []
    neighbors = []
    for line in lines[header + 1:]:
        match = re.match(r'^\s*(\S+)\s+(\S+)\s+(\S+)\s+\d+\s*$', line)
        if match:
            neighbors.append({'local': match.group(1), 'neighbor': match.group(2), 'remote': match.group(3)})
    return neighbors


def verified_links(topology, observations):
    devices = topology.get('device_inventory', {})
    names = Counter(obs.get('system_name', '').casefold() for obs in observations.values() if obs.get('system_name'))
    topology_names = Counter(str(device.get('name', '')).casefold() for device in devices.values() if not skipped_device_type(device))
    pair_counts = Counter(tuple(sorted((link['source_id'], link['target_id'])))
                          for link in topology.get('connections', []) for _ in link.get('interface_pairs', []))
    verified = {}
    for index, link in enumerate(topology.get('connections', [])):
        src, dst = link['source_id'], link['target_id']
        if src == dst or src not in devices or dst not in devices or pair_counts[tuple(sorted((src, dst)))] != 1:
            continue
        a, b = observations.get(src, {}), observations.get(dst, {})
        name_a, name_b = a.get('system_name'), b.get('system_name')
        if not name_a or not name_b or names[name_a.casefold()] != 1 or names[name_b.casefold()] != 1:
            continue
        if (name_a.casefold() != str(devices[src].get('name', '')).casefold()
                or name_b.casefold() != str(devices[dst].get('name', '')).casefold()
                or topology_names[name_a.casefold()] != 1 or topology_names[name_b.casefold()] != 1):
            continue
        forward = [n for n in a.get('neighbors', []) if n['neighbor'].casefold() == name_b.casefold()]
        reverse = [n for n in b.get('neighbors', []) if n['neighbor'].casefold() == name_a.casefold()]
        if len(forward) != 1 or len(reverse) != 1:
            continue
        f, r = forward[0], reverse[0]
        if interface_key(f['local']) != interface_key(r['remote']) or interface_key(f['remote']) != interface_key(r['local']):
            continue
        src_name, dst_name = f['local'], r['local']
        if not all(re.fullmatch(r'[A-Za-z][\w./:-]*\d[\w./:-]*', value) for value in (src_name, dst_name, f['remote'], r['remote'])):
            continue
        if src_name and dst_name:
            verified[f'{index}:0'] = {'source_interface': src_name, 'target_interface': dst_name, 'source': '双向 LLDP'}
    return verified


async def discover(topology, connect=True, verify=True, progress=None, on_update=None):
    devices = list(topology.get('device_inventory', {}).values())
    if not devices:
        raise ValueError('请先上传包含设备的拓扑')
    if len(devices) > 32:
        raise ValueError('一次最多处理 32 台设备，请缩小拓扑范围')
    def normalized_port(device):
        value = str(device.get('com_port', ''))
        return int(value) if value.isdigit() and 1 <= int(value) <= 65535 else None
    ports = Counter(normalized_port(device) for device in devices if not skipped_device_type(device))
    names = Counter(str(device.get('name', '')).casefold() for device in devices if not skipped_device_type(device))
    semaphore = asyncio.Semaphore(4)
    observations, results = {}, []
    async with Client(MCP_URL, timeout=300) as client:
        async def invoke(name, args):
            result = await client.call_tool(name, args, timeout=300 if name == 'configure_topology_device' else 30)
            return '\n'.join(getattr(item, 'text', '') for item in result.content)

        async def process(device):
            async with semaphore:
                device_id = device['id']
                raw_port = str(device.get('com_port', ''))
                record = {'id': device_id, 'name': device.get('name', device_id), 'port': raw_port}
                results.append(record)
                skipped = skipped_device_type(device)
                if skipped:
                    record['status'] = f'{skipped} 设备已跳过'
                    return
                port = normalized_port(device)
                if port is None or ports[port] != 1:
                    record['status'] = '端口无效或重复，已跳过'
                    return
                session = next((found for host in ('127.0.0.1', 'localhost', '::1')
                                if (found := session_manager.get_session_by_port(host, port))), None)
                try:
                    if not session:
                        if not connect:
                            record['status'] = '未连接；请先在设备列表一键连接'
                            return
                        result = await invoke('connect_device', {'ip': '127.0.0.1', 'port': port})
                        session = session_manager.get_session_by_port('127.0.0.1', port)
                        if not session or not result.startswith('连接成功'):
                            record['status'] = '连接失败'
                            return
                    session_ip = session.get('ip', '127.0.0.1')
                    record.update(status='已连接', session_id=session['session_id'], port=port, ip=session_ip)
                    if not verify:
                        return
                    device_name = device.get('name', '')
                    if not isinstance(device_name, str) or names[str(device_name).casefold()] != 1:
                        record['status'] = '已连接；拓扑设备名缺失或重复，已跳过配置'
                        return
                    renamed = json.loads(await invoke('rename_topology_device', {'ip': session_ip, 'port': port, 'name': device_name}))
                    if not renamed.get('ok'):
                        record['status'] = '已连接；' + renamed.get('message', '设备改名失败')
                        return
                    record.update(status='已连接；' + renamed.get('message', '设备名已设置'), renamed=True)
                except Exception as exc:
                    record['status'] = f'连接或设备改名失败：{str(exc)[:200]}'

        async def configure(record):
            if not record.get('renamed'):
                return
            async with semaphore:
                try:
                    device_id, session_ip, port = record['id'], record['ip'], record['port']
                    enabled = json.loads(await invoke('configure_topology_device', {'ip': session_ip, 'port': port, 'name': record['name']}))
                    if not enabled.get('ok') and not enabled.get('query_safe'):
                        record['status'] += '；' + enabled.get('message', 'LLDP 配置失败')
                        return
                    record['status'] += '；' + enabled.get('message', '全局 LLDP 已开启')
                    output = await invoke('send_command', {'ip': session_ip, 'port': port, 'command': NEIGHBOR_COMMAND})
                    body = command_body(output, NEIGHBOR_COMMAND)
                    observations[device_id] = {'system_name': record['name'], 'neighbors': parse_neighbors(body)}
                    if not body:
                        record['status'] += '；LLDP 邻居查询不完整'
                    elif not observations[device_id]['neighbors']:
                        record['status'] += '；等待 LLDP 邻居'
                except Exception as exc:
                    record['status'] += f'；LLDP 配置或查询失败：{str(exc)[:200]}'
        completed = 0
        async def process_with_progress(device):
            nonlocal completed
            try:
                await process(device)
            finally:
                completed += 1
                if progress:
                    progress(int((35 if verify else 75) * completed / len(devices)), f'设备改名阶段：已处理 {completed}/{len(devices)} 台设备' if verify else f'已处理 {completed}/{len(devices)} 台设备')
        await asyncio.gather(*(process_with_progress(device) for device in devices))
        if verify:
            if any(record.get('renamed') for record in results):
                if progress:
                    progress(35, '设备改名阶段结束，等待 5 秒后配置 LLDP')
                await asyncio.sleep(5)
            completed = 0
            async def configure_with_progress(record):
                nonlocal completed
                try:
                    await configure(record)
                    if on_update:
                        on_update(verified_links(topology, observations))
                finally:
                    completed += 1
                    if progress:
                        progress(35 + int(40 * completed / len(devices)), f'LLDP 和接口查询阶段：已处理 {completed}/{len(devices)} 台设备')
            await asyncio.gather(*(configure_with_progress(record) for record in results))
        # Newly enabled LLDP may take an advertisement interval to discover peers.
        link_count = sum(len(link.get('interface_pairs', [])) for link in topology.get('connections', []))
        if verify and link_count and observations and len(verified_links(topology, observations)) < link_count:
            if progress:
                progress(80, '等待 LLDP 邻居发现，约 30 秒')
            await asyncio.sleep(30)
            if progress:
                progress(90, '正在重新查询邻居并核对链路')
            async def retry_neighbors(record):
                device_id = record['id']
                if device_id not in observations:
                    return
                async with semaphore:
                    try:
                        command = NEIGHBOR_COMMAND
                        output = await invoke('send_command', {'ip': record['ip'], 'port': record['port'], 'command': command})
                        body = command_body(output, command)
                        observations[device_id]['neighbors'] = parse_neighbors(body)
                        record['status'] = record['status'].replace('；等待 LLDP 邻居', '')
                        if not observations[device_id]['neighbors']:
                            record['status'] += '；邻居尚未发现或接口 LLDP 未开启'
                    except Exception:
                        observations[device_id]['neighbors'] = []
                        record['status'] += '；邻居重查失败'
            await asyncio.gather(*(retry_neighbors(record) for record in results))
            if on_update:
                on_update(verified_links(topology, observations))
    verified = verified_links(topology, observations)
    return {'devices': results, 'verified': verified, 'candidates': {}}
