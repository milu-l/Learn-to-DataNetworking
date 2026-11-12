import json
import queue
import time
import threading
import asyncio
import subprocess

from flask import Flask, request, jsonify, Response, render_template, stream_with_context
from flask_cors import CORS

from scan_service import start_scan_async, scan_ports_sync
from telnet_service import connect_device, send_command, disconnect_device, read_output
from session_manager import session_manager
from topo_service import parse_topology_from_content, generate_ai_markdown, generate_mermaid_topology, generate_ai_prompt_context, generate_readable_summary, infer_network_hierarchy
from topo_service import get_topo_data, get_topo_filename, set_topo_data, clear_topo_data
from topo_service import get_cached_report, set_cached_report
from traffic_service import traffic_monitor, list_interfaces

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/mcp/status")
def api_mcp_status():
    async def check():
        from fastmcp import Client
        async with Client("http://127.0.0.1:5001/sse", timeout=3) as client:
            await client.ping()

    try:
        asyncio.run(asyncio.wait_for(check(), timeout=4))
        response = jsonify({"online": True})
    except Exception:
        app.logger.info("MCP SSE status check failed", exc_info=True)
        response = jsonify({"online": False})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/scan", methods=["POST"])
def api_scan():
    data = request.get_json()
    ip = data.get("ip", "127.0.0.1")
    start_port = int(data.get("start_port", 2000))
    end_port = int(data.get("end_port", 2099))
    timeout = int(data.get("timeout", 2))

    if start_port < 1 or end_port > 65535 or start_port > end_port:
        return jsonify({"error": "端口范围无效"}), 400

    scan = start_scan_async(ip, start_port, end_port, timeout)
    return jsonify({
        "scan_id": scan["id"],
        "message": "扫描已启动",
        "total": scan["total"]
    })


@app.route("/api/scan/<scan_id>/status")
def api_scan_status(scan_id):
    def generate():
        scan = session_manager.get_scan(scan_id)
        if not scan:
            yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': '扫描不存在'})}\n\n"
            return

        yield f"event: start\ndata: {json.dumps({'type': 'start', 'total': scan['total']})}\n\n"

        while True:
            scan = session_manager.get_scan(scan_id)
            if not scan:
                break

            try:
                while True:
                    event = scan["events"].get_nowait()
                    event_type = event.get("type", "message")
                    yield f"event: {event_type}\ndata: {json.dumps(event)}\n\n"
                    if event["type"] in ("complete", "error"):
                        return
            except queue.Empty:
                pass

            if scan["status"] in ("completed", "error"):
                time.sleep(0.1)
                try:
                    while True:
                        event = scan["events"].get_nowait()
                        event_type = event.get("type", "message")
                        yield f"event: {event_type}\ndata: {json.dumps(event)}\n\n"
                except queue.Empty:
                    pass
                return

            time.sleep(0.2)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive"
        }
    )


@app.route("/api/telnet/connect", methods=["POST"])
def api_telnet_connect():
    data = request.get_json()
    ip = data.get("ip")
    port = int(data.get("port"))

    if not ip or not port:
        return jsonify({"error": "缺少 IP 或端口"}), 400

    try:
        session_id, initial_output = connect_device(ip, port)
        return jsonify({
            "session_id": session_id,
            "message": "连接成功",
            "initial_output": initial_output
        })
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/telnet/<session_id>/send", methods=["POST"])
def api_telnet_send(session_id):
    data = request.get_json()
    command = data.get("command", "")

    if not command:
        return jsonify({"error": "命令不能为空"}), 400

    try:
        output = send_command(session_id, command)
        return jsonify({"success": True, "output": output})
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/telnet/<session_id>/read")
def api_telnet_read(session_id):
    data, connected = read_output(session_id)
    return jsonify({"data": data, "connected": connected})


@app.route("/api/telnet/<session_id>/disconnect", methods=["POST"])
def api_telnet_disconnect(session_id):
    success, msg = disconnect_device(session_id)
    return jsonify({"success": success, "message": msg})


@app.route("/api/sessions")
def api_sessions():
    return jsonify({"sessions": session_manager.list_sessions()})


@app.route("/api/topo/upload", methods=["POST"])
def api_topo_upload():
    if "file" not in request.files:
        return jsonify({"error": "请选择文件上传"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "请选择文件"}), 400

    if not file.filename.lower().endswith((".txt", ".topo", ".xml", ".vbox")):
        return jsonify({"error": "不支持的文件格式，请上传 .txt 或 .topo 文件"}), 400

    try:
        content = file.read().decode("utf-8")
    except UnicodeDecodeError:
        try:
            file.seek(0)
            content = file.read().decode("gbk")
        except UnicodeDecodeError:
            return jsonify({"error": "文件编码不支持，请使用 UTF-8 或 GBK 编码"}), 400

    try:
        data = parse_topology_from_content(content)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400

    traffic_monitor.stop()
    traffic_monitor.clear_packets()
    set_topo_data(data, file.filename)

    return jsonify({
        "success": True,
        "filename": file.filename,
        "summary": data["summary"],
        "device_count": data["summary"]["total_devices"],
        "connection_count": data["summary"]["total_connections"],
        "vlan_count": data["summary"]["vlan_count"],
        "subnet_count": data["summary"]["subnet_count"]
    })


@app.route("/api/topo/analyze", methods=["GET"])
def api_topo_analyze():
    data = get_topo_data()
    filename = get_topo_filename()

    if not data:
        return jsonify({"error": "请先上传拓扑文件"}), 400

    output_type = request.args.get("type", "markdown")

    cached = get_cached_report(output_type)
    if cached is not None:
        return jsonify({
            "success": True,
            "filename": filename,
            "type": output_type,
            "content": cached,
            "cached": True
        })

    try:
        if output_type == "markdown":
            result = generate_ai_markdown(data)
        elif output_type == "mermaid":
            layers = infer_network_hierarchy(data)
            result = generate_mermaid_topology(data, layers)
        elif output_type == "prompt":
            result = generate_ai_prompt_context(data)
        elif output_type == "summary":
            result = generate_readable_summary(data)
        else:
            return jsonify({"error": f"不支持的分析类型: {output_type}"}), 400

        set_cached_report(output_type, result)

        return jsonify({
            "success": True,
            "filename": filename,
            "type": output_type,
            "content": result,
            "cached": False
        })
    except Exception as e:
        return jsonify({"error": f"分析失败: {str(e)}"}), 500


@app.route("/api/topo/status")
def api_topo_status():
    data = get_topo_data()
    filename = get_topo_filename()
    if data:
        return jsonify({
            "loaded": True,
            "filename": filename,
            "summary": data["summary"]
        })
    return jsonify({"loaded": False})


@app.route("/api/topo", methods=["DELETE"])
def api_topo_delete():
    traffic_monitor.stop()
    traffic_monitor.clear_packets()
    try:
        clear_topo_data()
    except OSError:
        app.logger.exception("Failed to clear topology data")
        return jsonify({"error": "无法删除拓扑数据，请检查文件权限"}), 500
    return jsonify({"success": True})


@app.route("/api/topo/graph")
def api_topo_graph():
    data = get_topo_data()
    if not data:
        return jsonify({"loaded": False})
    devices = [{"id": d["id"], "name": d["name"], "model": d["model"],
                "com_port": d.get("com_port", ""), "position": d["position"]}
               for d in data["device_inventory"].values()]
    connections = [{"source_id": c["source_id"], "target_id": c["target_id"],
                    "link_count": c["link_count"]} for c in data["connections"]]
    return jsonify({"loaded": True, "filename": get_topo_filename(), "summary": data["summary"],
                    "devices": devices, "connections": connections})


@app.route("/api/traffic/interfaces")
def api_traffic_interfaces():
    try:
        return jsonify({"interfaces": list_interfaces()})
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/traffic/start", methods=["POST"])
def api_traffic_start():
    try:
        interface = str((request.get_json(silent=True) or {}).get('interface', ''))
        count = traffic_monitor.start(interface, get_topo_data())
        return jsonify({"success": True, "links": count})
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/traffic/stop", methods=["POST"])
def api_traffic_stop():
    traffic_monitor.stop()
    return jsonify({"success": True})


@app.route("/api/traffic/status")
def api_traffic_status():
    response = jsonify(traffic_monitor.status())
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route("/api/traffic/packets", methods=["GET", "DELETE"])
def api_traffic_packets():
    if request.method == "DELETE":
        traffic_monitor.clear_packets()
        return jsonify({"success": True})
    response = jsonify({"packets": traffic_monitor.packet_snapshot()})
    response.headers['Cache-Control'] = 'no-store'
    return response


def _start_mcp_sse():
    from mcp_server import run_sse
    run_sse()


def run_server(host="0.0.0.0", port=5000):
    banner = f"""
{'='*60}
  eNSP Telnet Manager
  Web 界面: http://{host}:{port}
  MCP SSE:  http://{host}:5001/sse
{'='*60}
"""
    print(banner)

    mcp_thread = threading.Thread(target=_start_mcp_sse, daemon=True)
    mcp_thread.start()

    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    import sys
    host = sys.argv[1] if len(sys.argv) > 1 else "0.0.0.0"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
    run_server(host, port)
