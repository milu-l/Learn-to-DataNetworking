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
from topology_archive import export_archive, import_archive
from urllib.parse import urlsplit

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)
topology_resolve_lock = threading.Lock()
topology_progress_lock = threading.Lock()
topology_progress = {'state': 'idle', 'percent': 0, 'message': ''}
verified_topology = None
verified_topology_links = {}
candidate_topology_links = {}
manual_topology = None
manual_topology_links = {}
restored_topology_links = {}
topology_revision = 0


def is_local_chat_request():
    origin = request.headers.get('Origin')
    try:
        host = urlsplit('http://' + request.host).hostname
        return (request.remote_addr in ('127.0.0.1', '::1') and host in ('localhost', '127.0.0.1', '::1')
                and (not origin or urlsplit(origin).netloc == request.host))
    except ValueError:
        return False


@app.route('/api/chat/config', methods=['GET', 'POST'])
def api_chat_config():
    if not is_local_chat_request():
        return jsonify({'error': 'AI 设置仅允许本机页面访问'}), 403
    from chat_service import get_config, set_config
    try:
        data = get_config() if request.method == 'GET' else set_config(request.get_json(silent=True))
        response = jsonify(data)
        response.headers['Cache-Control'] = 'no-store'
        return response
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@app.route('/api/chat/test', methods=['POST'])
def api_chat_test():
    if not is_local_chat_request():
        return jsonify({'error': 'AI 连接测试仅允许本机页面访问'}), 403
    from chat_service import model_request
    try:
        reply = model_request([{'role': 'user', 'content': '请回复：连接成功。'}], [])
        if not isinstance(reply, dict) or not isinstance(reply.get('content'), str) or not reply['content'].strip():
            raise ValueError('AI 服务未返回有效的文本回复')
        response = jsonify({'success': True, 'message': 'AI 服务连接和认证成功'})
        response.headers['Cache-Control'] = 'no-store'
        return response
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@app.route('/api/chat', methods=['POST'])
@app.route('/api/chat/confirm', methods=['POST'])
def api_chat():
    # AI tool calls are available only from this computer and its own web page.
    if not is_local_chat_request():
        return jsonify({'error': 'AI 对话仅允许本机页面访问'}), 403
    from chat_service import chat, confirm
    data = request.get_json(silent=True) or {}
    try:
        if request.path.endswith('/confirm'):
            if not isinstance(data.get('token'), str) or not isinstance(data.get('allow'), bool):
                raise ValueError('确认参数无效')
            result = confirm(data['token'], data['allow'])
        else:
            result = chat(data.get('messages'), data.get('auto_execute', False))
        return jsonify(result)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:
        app.logger.exception('AI chat failed')
        return jsonify({'error': '无法连接 MCP 服务或处理 AI 请求，请检查服务状态'}), 503


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
    global verified_topology, verified_topology_links, candidate_topology_links, manual_topology, manual_topology_links, restored_topology_links, topology_revision
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
    verified_topology, verified_topology_links, candidate_topology_links = None, {}, {}
    manual_topology, manual_topology_links = data, {}
    restored_topology_links = {}
    topology_revision += 1

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
    global verified_topology, verified_topology_links, candidate_topology_links, manual_topology, manual_topology_links, restored_topology_links, topology_revision
    traffic_monitor.stop()
    traffic_monitor.clear_packets()
    try:
        clear_topo_data()
        verified_topology, verified_topology_links, candidate_topology_links = None, {}, {}
        manual_topology, manual_topology_links = None, {}
        restored_topology_links = {}
        topology_revision += 1
    except OSError:
        app.logger.exception("Failed to clear topology data")
        return jsonify({"error": "无法删除拓扑数据，请检查文件权限"}), 500
    return jsonify({"success": True})


@app.route("/api/topo/graph")
def api_topo_graph():
    data = get_topo_data()
    if not data:
        return jsonify({"loaded": False})
    devices = [{"id": d["id"], "name": d["name"], "model": d["model"], "category": d.get("category", ""),
                "com_port": d.get("com_port", ""), "position": d["position"]}
               for d in data["device_inventory"].values()]
    resolved = verified_topology_links if verified_topology is data else {}
    candidates = candidate_topology_links if verified_topology is data else {}
    manual = manual_topology_links if manual_topology is data else {}
    restored = restored_topology_links if manual_topology is data else {}
    connections = [{"source_id": c["source_id"], "target_id": c["target_id"],
                    "link_count": c["link_count"],
                    "interface_pairs": [{"src_port_index": p.get("src_port_index"),
                                         "dst_port_index": p.get("dst_port_index"),
                                         "line_type": p.get("line_type", ""),
                                         "verified": resolved.get(f'{i}:{j}'),
                                         "candidate": candidates.get(f'{i}:{j}'),
                                         "restored": restored.get(f'{i}:{j}'),
                                         "manual": manual.get(f'{i}:{j}')}
                                        for j, p in enumerate(c.get("interface_pairs", []))]}
                   for i, c in enumerate(data["connections"])]
    return jsonify({"loaded": True, "revision": topology_revision, "filename": get_topo_filename(), "summary": data["summary"],
                    "devices": devices, "connections": connections})


@app.route('/api/topo/interfaces-archive', methods=['GET', 'POST'])
def api_topo_interfaces_archive():
    global manual_topology, manual_topology_links, restored_topology_links
    if not is_local_chat_request():
        return jsonify({'error': '互联信息导入导出仅允许本机页面访问'}), 403
    topology = get_topo_data()
    if not topology:
        return jsonify({'error': '请先上传拓扑文件'}), 400
    if not topology_resolve_lock.acquire(blocking=False):
        return jsonify({'error': '设备连接或接口验证正在进行，请稍后再试'}), 409
    try:
        if request.method == 'GET':
            manual = manual_topology_links if manual_topology is topology else {}
            restored = restored_topology_links if manual_topology is topology else {}
            verified = verified_topology_links if verified_topology is topology else {}
            response = jsonify(export_archive(topology, verified, manual, restored))
            response.headers['Cache-Control'] = 'no-store'
            return response
        if request.content_length is not None and request.content_length > 2 * 1024 * 1024:
            return jsonify({'error': '互联信息文件不能超过 2 MB'}), 413
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or type(payload.get('revision')) is not int or payload['revision'] != topology_revision:
            return jsonify({'error': '拓扑已变更，请刷新后重试'}), 409
        manual, restored = import_archive(topology, payload.get('archive'))
        if get_topo_data() is not topology:
            return jsonify({'error': '导入期间拓扑已变更'}), 409
        manual_topology, manual_topology_links, restored_topology_links = topology, manual, restored
        return jsonify({'success': True, 'manual_count': len(manual), 'restored_count': len(restored)})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    finally:
        topology_resolve_lock.release()


@app.route('/api/topo/manual-interfaces', methods=['POST', 'DELETE'])
def api_topo_manual_interfaces():
    global manual_topology, manual_topology_links
    if not is_local_chat_request():
        return jsonify({'error': '手动填写接口仅允许本机页面访问'}), 403
    topology = get_topo_data()
    data = request.get_json(silent=True)
    if not topology or not isinstance(data, dict):
        return jsonify({'error': '拓扑未加载或请求无效'}), 400
    if type(data.get('revision')) is not int or data['revision'] != topology_revision:
        return jsonify({'error': '拓扑已变更，请刷新后重试'}), 409
    index, pair_index = data.get('link_index'), data.get('pair_index')
    if type(index) is not int or type(pair_index) is not int or index < 0 or pair_index < 0:
        return jsonify({'error': '链路编号无效'}), 400
    connections = topology.get('connections', [])
    if index >= len(connections):
        return jsonify({'error': '链路不存在，请刷新拓扑'}), 400
    link = connections[index]
    if (pair_index >= len(link.get('interface_pairs', [])) or
            data.get('source_id') != link.get('source_id') or data.get('target_id') != link.get('target_id')):
        return jsonify({'error': '链路已变更，请刷新拓扑'}), 409
    key = f'{index}:{pair_index}'
    if manual_topology is not topology:
        manual_topology, manual_topology_links = topology, {}
    if request.method == 'DELETE':
        manual_topology_links.pop(key, None)
    else:
        source, target = data.get('source_interface'), data.get('target_interface')
        if not isinstance(source, str) or not isinstance(target, str):
            return jsonify({'error': '接口名称必须为文本'}), 400
        source, target = source.strip(), target.strip()
        if (not source and not target) or any(len(value) > 64 or any(ord(char) < 32 or ord(char) == 127 for char in value) for value in (source, target)):
            return jsonify({'error': '请填写至少一端接口名，每端最多 64 个字符且不能包含控制字符'}), 400
        manual_topology_links[key] = {'source_interface': source, 'target_interface': target}
    return jsonify({'success': True})


@app.route('/api/topo/connect', methods=['POST'])
def api_topo_connect():
    if not is_local_chat_request():
        return jsonify({'error': '一键连接仅允许本机页面访问'}), 403
    topology = get_topo_data()
    if not topology:
        return jsonify({'error': '请先上传拓扑文件'}), 400
    if not topology_resolve_lock.acquire(blocking=False):
        return jsonify({'error': '设备连接和 LLDP 检测正在进行'}), 409
    try:
        from topology_resolver import discover
        result = asyncio.run(discover(topology, connect=True, verify=False))
        if get_topo_data() is not topology:
            return jsonify({'error': '检测期间拓扑已更改，请重新执行；已建立的设备会话仍保留'}), 409
        return jsonify({'success': True, 'devices': result['devices']})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:
        app.logger.exception('Topology connection or MCP query failed')
        return jsonify({'error': '无法连接 MCP 服务或查询设备；已建立的会话仍保留'}), 503
    finally:
        topology_resolve_lock.release()


@app.route('/api/topo/verify', methods=['POST'])
def api_topo_verify():
    global verified_topology, verified_topology_links, candidate_topology_links
    if not is_local_chat_request():
        return jsonify({'error': '接口验证仅允许本机页面访问'}), 403
    topology = get_topo_data()
    if not topology:
        return jsonify({'error': '请先上传拓扑文件'}), 400
    if not topology_resolve_lock.acquire(blocking=False):
        return jsonify({'error': '设备连接或接口验证正在进行'}), 409
    verified_topology, verified_topology_links, candidate_topology_links = None, {}, {}
    with topology_progress_lock:
        topology_progress.clear()
        topology_progress.update(state='running', percent=0, message='正在修改设备名称', revision=0)

    def update_progress(percent, message):
        with topology_progress_lock:
            topology_progress.update(percent=percent, message=message)

    def update_links(links):
        global verified_topology, verified_topology_links, candidate_topology_links
        if get_topo_data() is not topology:
            return
        with topology_progress_lock:
            if verified_topology is not topology or verified_topology_links != links:
                verified_topology, verified_topology_links, candidate_topology_links = topology, links, {}
                topology_progress['revision'] += 1

    def run():
        global verified_topology, verified_topology_links, candidate_topology_links
        try:
            from topology_resolver import discover
            result = asyncio.run(discover(topology, connect=False, verify=True, progress=update_progress, on_update=update_links))
            if get_topo_data() is not topology:
                raise ValueError('验证期间拓扑已更改，请重新验证')
            verified_topology, verified_topology_links, candidate_topology_links = topology, result['verified'], result['candidates']
            with topology_progress_lock:
                topology_progress.update(state='done', percent=100, message='验证完成',
                                         devices=result['devices'], verified_count=len(result['verified']),
                                         candidate_count=len(result['candidates']))
        except Exception as exc:
            app.logger.exception('Topology verification failed')
            with topology_progress_lock:
                topology_progress.update(state='error', message=str(exc) if isinstance(exc, ValueError) else 'MCP 服务或设备查询失败')
        finally:
            topology_resolve_lock.release()
    try:
        threading.Thread(target=run, daemon=True).start()
    except Exception:
        topology_resolve_lock.release()
        with topology_progress_lock:
            topology_progress.update(state='error', message='无法启动接口验证')
        return jsonify({'error': '无法启动接口验证'}), 500
    return jsonify({'success': True}), 202


@app.route('/api/topo/verify/progress')
def api_topo_verify_progress():
    if not is_local_chat_request():
        return jsonify({'error': '仅允许本机页面访问'}), 403
    with topology_progress_lock:
        response = jsonify(dict(topology_progress))
    response.headers['Cache-Control'] = 'no-store'
    return response


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
  Learn to eNSP
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
