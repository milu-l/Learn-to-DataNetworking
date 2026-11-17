from scan_service import scan_ports_sync
from telnet_service import connect_device as backend_connect_device
from telnet_service import send_command as backend_send_command
from telnet_service import send_command_complete as backend_send_command_complete
from telnet_service import disconnect_device as backend_disconnect_device
from telnet_service import read_output
from session_manager import session_manager
from lldp_service import enable_lldp as backend_enable_lldp
from lldp_service import configure_topology_device as backend_configure_topology_device
from lldp_service import rename_topology_device as backend_rename_topology_device
from topology_resolver import interface_key
from topo_service import get_topo_data, generate_ai_markdown, generate_readable_summary, generate_mermaid_topology, infer_network_hierarchy, generate_ai_prompt_context
from urllib.request import urlopen
from urllib.error import URLError
import json
import socket


def interconnection_snapshot(topology, sessions, device_name=''):
    """Build AI-readable data from the current graph, retaining interface provenance."""
    devices = {device['id']: device for device in topology.get('devices', [])}
    target = device_name.strip().casefold()
    result = []
    for link_index, link in enumerate(topology.get('connections', [])):
        source, target_device = devices.get(link.get('source_id'), {}), devices.get(link.get('target_id'), {})
        if target and target not in (str(source.get('name', '')).casefold(), str(target_device.get('name', '')).casefold()):
            continue
        def endpoint(device, side, pair):
            port = str(device.get('com_port', ''))
            session = next((s for s in sessions if s.get('ip') in ('127.0.0.1', 'localhost', '::1')
                            and port.isdigit() and s.get('port') == int(port)), None)
            manual = (pair.get('manual') or {}).get(side + '_interface') or None
            verified = (pair.get('verified') or {}).get(side + '_interface') or None
            restored = (pair.get('restored') or {}).get(side + '_interface') or None
            candidate = (pair.get('candidate') or {}).get(side + '_interface') or None
            return {'id': device.get('id'), 'name': device.get('name'), 'model': device.get('model'),
                    'telnet_ip': '127.0.0.1' if port.isdigit() and 1 <= int(port) <= 65535 else None,
                    'telnet_port': int(port) if port.isdigit() and 1 <= int(port) <= 65535 else None,
                    'session_id': session.get('session_id') if session else None,
                    'interface': manual or verified or restored or candidate,
                    'interface_source': '手动填写，未经 LLDP 验证' if manual else ('双向 LLDP 已验证' if verified else ('导入的历史验证记录，当前未重新验证' if restored else ('LLDP 候选，未经验证' if candidate else '待验证'))),
                    'manual_interface': manual, 'lldp_verified_interface': verified,
                    'previously_verified_interface': restored,
                    'interface_conflict': bool(manual and verified and interface_key(manual) != interface_key(verified))}
        for pair_index, pair in enumerate(link.get('interface_pairs', [])):
            result.append({'link_id': f'{link_index}:{pair_index}', 'line_type': pair.get('line_type', ''),
                           'source': endpoint(source, 'source', pair), 'target': endpoint(target_device, 'target', pair)})
    return {'loaded': True, 'filename': topology.get('filename'), 'revision': topology.get('revision'),
            'device_filter': device_name.strip() or None, 'links': result,
            'note': '接口来自当前拓扑的临时记录；手动填写或候选接口未经验证，配置前应检查设备实际接口和现有 IP。'}


def register_tools(mcp):
    @mcp.tool()
    def get_topology_links(device_name: str = '', offset: int = 0, limit: int = 10) -> str:
        """读取临时互联信息、接口来源和会话；按设备名精确筛选，或按 offset 分页（每页最多 10 条）。不发送设备命令。"""
        if not isinstance(device_name, str) or len(device_name) > 200 or any(ord(c) < 32 for c in device_name):
            return '设备名称无效。'
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 10:
            return '分页参数无效，offset 必须非负，limit 须为 1 至 10。'
        try:
            with urlopen('http://127.0.0.1:5000/api/topo/graph', timeout=5) as response:
                topology = json.load(response)
        except (URLError, OSError, ValueError):
            return '无法读取 Web 拓扑，请确认 Web 服务正在运行。'
        if not topology.get('loaded'):
            return '尚未上传拓扑，请先在网页中上传拓扑文件。'
        snapshot = interconnection_snapshot(topology, session_manager.list_sessions(), device_name)
        links = snapshot['links']
        snapshot.update(total=len(links), offset=offset, next_offset=offset + limit if offset + limit < len(links) else None,
                        links=links[offset:offset + limit])
        return json.dumps(snapshot, ensure_ascii=False)

    @mcp.tool()
    def rename_topology_device(ip: str, port: int, name: str) -> str:
        """将设备名修改为拓扑名称，不保存配置。"""
        return json.dumps(backend_rename_topology_device(ip, port, name), ensure_ascii=False)

    @mcp.tool()
    def configure_topology_device(ip: str, port: int, name: str) -> str:
        """检查并启用全局 LLDP，不修改接口级 LLDP，也不保存配置。"""
        return json.dumps(backend_configure_topology_device(ip, port, name), ensure_ascii=False)

    @mcp.tool()
    def enable_lldp(ip: str, port: int) -> str:
        """检查并启用已连接设备的全局 LLDP；修改运行配置但不保存。"""
        return json.dumps(backend_enable_lldp(ip, port), ensure_ascii=False)

    @mcp.tool()
    def detect_topology_devices() -> str:
        """检测当前 Web 拓扑中设备的本机 Telnet 端口，并列出已有会话；不会发送设备命令。"""
        try:
            with urlopen('http://127.0.0.1:5000/api/topo/graph', timeout=5) as response:
                topology = json.load(response)
        except (URLError, OSError, json.JSONDecodeError):
            return '无法读取 Web 拓扑，请确认 Web 服务正在运行。'
        if not topology.get('loaded'):
            return '尚未上传拓扑，请先在网页中上传拓扑文件。'
        sessions = session_manager.list_sessions()
        results = []
        for device in topology.get('devices', []):
            raw_port = str(device.get('com_port', ''))
            if not raw_port.isdigit() or not 1 <= int(raw_port) <= 65535:
                results.append({'name': device.get('name'), 'status': '拓扑未提供有效端口'})
                continue
            port = int(raw_port)
            existing = next((s for s in sessions if s['ip'] in ('127.0.0.1', 'localhost', '::1') and s['port'] == port), None)
            if existing:
                status = '已连接'
            else:
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=0.5):
                        status = '端口开放'
                except OSError:
                    status = '端口不可用'
            results.append({'name': device.get('name'), 'ip': '127.0.0.1', 'port': port,
                            'status': status, 'session_id': existing['session_id'] if existing else None})
        return json.dumps(results, ensure_ascii=False)

    @mcp.tool()
    def scan_ports(ip: str, start_port: int = 2000, end_port: int = 2099, timeout: int = 2) -> str:
        """扫描目标 IP 的端口范围，返回开放端口列表

        参数:
        - ip: 目标 IP 地址（如 127.0.0.1）
        - start_port: 起始端口（默认 2000）
        - end_port: 结束端口（默认 2099）
        - timeout: 单端口超时秒数（默认 2）
        """
        import time
        t0 = time.time()
        ports = scan_ports_sync(ip, start_port, end_port, timeout)
        elapsed = round(time.time() - t0, 2)
        if ports:
            return f"扫描完成，共 {end_port - start_port + 1} 个端口，发现 {len(ports)} 个开放端口：{ports}，耗时 {elapsed} 秒"
        return f"扫描完成，共 {end_port - start_port + 1} 个端口，未发现开放端口，耗时 {elapsed} 秒"

    @mcp.tool()
    def connect_device(ip: str, port: int) -> str:
        """通过 Telnet 连接到指定设备，返回会话 ID

        参数:
        - ip: 设备 IP 地址
        - port: Telnet 端口号
        """
        try:
            session_id, initial_output = backend_connect_device(ip, port)
            header = initial_output.strip()[:500] if initial_output else "（无初始输出）"
            return f"连接成功！会话 ID: {session_id}\n设备初始输出:\n{header}"
        except RuntimeError as e:
            return f"连接失败：{str(e)}"

    @mcp.tool()
    def send_command(ip: str, port: int, command: str) -> str:
        """向已连接的设备发送命令并获取返回结果

        参数:
        - ip: 设备 IP 地址
        - port: Telnet 端口号
        - command: 要执行的命令（如 display version、display ip interface brief 等）
        """
        try:
            session = session_manager.get_session_by_port(ip, port)
            if not session:
                return f"设备未连接：{ip}:{port}，请先使用 connect_device 连接"
            output = backend_send_command(session["session_id"], command)
            if output:
                return output.strip()
            return "命令已发送，暂无返回数据"
        except RuntimeError as e:
            return f"发送失败：{str(e)}"

    @mcp.tool()
    def disconnect_device(session_id: str) -> str:
        """断开与设备的 Telnet 连接

        参数:
        - session_id: 要断开的会话 ID
        """
        success, msg = backend_disconnect_device(session_id)
        return msg

    @mcp.tool()
    def list_sessions() -> str:
        """列出当前所有活跃的 Telnet 会话"""
        sessions = session_manager.list_sessions()
        if not sessions:
            return "当前没有活跃的 Telnet 会话"
        lines = ["当前活跃会话:"]
        for s in sessions:
            lines.append(f"  - {s['session_id']}: {s['ip']}:{s['port']} [{s['status']}]")
        return "\n".join(lines)

    @mcp.tool()
    def get_device_info(ip: str, port: int) -> str:
        """获取设备基本信息（执行 display version 命令）

        参数:
        - ip: 设备 IP 地址
        - port: Telnet 端口号
        """
        try:
            session = session_manager.get_session_by_port(ip, port)
            if not session:
                return f"设备未连接：{ip}:{port}，请先使用 connect_device 连接"
            output = backend_send_command_complete(session["session_id"], "display version")
            return output.strip() if output else "暂无返回数据"
        except RuntimeError as e:
            return f"获取失败：{str(e)}"

    @mcp.tool()
    def get_topo_report(report_type: str = "markdown") -> str:
        """获取当前 Web 服务中已上传的拓扑报告：markdown、summary、mermaid 或 prompt。"""
        if report_type not in ("markdown", "summary", "mermaid", "prompt"):
            return "不支持的报告类型，可选：markdown、summary、mermaid、prompt"
        try:
            with urlopen(f"http://127.0.0.1:5000/api/topo/analyze?type={report_type}", timeout=5) as response:
                result = json.load(response)
            return result.get("content", "暂无拓扑报告")
        except URLError as exc:
            if getattr(exc, 'code', None) == 400:
                return "暂无拓扑数据，请先在 Web 界面上传拓扑文件"
            return "无法连接 Web 服务，请先启动 app.py"
