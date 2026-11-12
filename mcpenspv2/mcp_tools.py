from scan_service import scan_ports_sync
from telnet_service import connect_device as backend_connect_device
from telnet_service import send_command as backend_send_command
from telnet_service import disconnect_device as backend_disconnect_device
from telnet_service import read_output
from session_manager import session_manager
from topo_service import get_topo_data, generate_ai_markdown, generate_readable_summary, generate_mermaid_topology, infer_network_hierarchy, generate_ai_prompt_context
from urllib.request import urlopen
from urllib.error import URLError
import json
def register_tools(mcp):
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
            output = backend_send_command(session["session_id"], "display version")
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
