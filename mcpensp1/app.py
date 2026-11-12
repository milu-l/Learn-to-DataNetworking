from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
import socket
import time
import os
import json
import base64
import threading
import xml.etree.ElementTree as ET
import subprocess
import shutil
import re
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SECRET_KEY'] = 'ensp-mcp'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

devices = {}
device_names = {}
topology_data = {}
topology_filename = None
topology_lock = threading.Lock()
capture_lock = threading.Lock()
capture_process = None
capture_generation = 0
capture_counts = {}

class TelnetConnection:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sock = None

    def connect(self):
        self.sock = socket.socket()
        self.sock.settimeout(1)
        self.sock.connect((self.host, self.port))
        self.sock.send(b'\r\n')
        time.sleep(0.1)
        return True

    def send_cmd(self, cmd):
        self.sock.send(f'{cmd}\r\n'.encode())
        time.sleep(1.5)
        return self.sock.recv(8192).decode('gbk', errors='ignore')

    def close(self):
        if self.sock:
            self.sock.close()


def scan_ports(start=2000, end=2050):
    found = []
    for port in range(start, end + 1):
        try:
            s = socket.socket()
            s.settimeout(0.5)
            if s.connect_ex(('127.0.0.1', port)) == 0:
                found.append({'port': port, 'path': f'127.0.0.1:{port}'})
            s.close()
        except:
            pass
    return found


def scan_devices(start: int = 2000, end: int = 2050):
    result = scan_ports(start, end)
    for device in result:
        device['name'] = device_names.get(device['path'], device['path'])
    return result


def connect_device(port: int):
    path = f'127.0.0.1:{port}'
    if path in devices:
        return {'success': False, 'error': 'Device already connected'}
    try:
        conn = TelnetConnection('127.0.0.1', port)
        conn.connect()
        devices[path] = conn
        name = device_names.get(path, path)
        return {'success': True, 'port': port, 'path': path, 'name': name}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def send_command(path: str, command: str):
    if path not in devices:
        return {'success': False, 'error': 'Device not connected'}
    try:
        result = devices[path].send_cmd(command)
        return {'success': True, 'path': path, 'output': result}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def disconnect_device(path: str):
    if path not in devices:
        return {'success': False, 'error': 'Device not connected'}
    try:
        devices[path].close()
        del devices[path]
        return {'success': True, 'path': path}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def get_connected_devices():
    connected = []
    for path in devices:
        port = int(path.split(':')[1])
        connected.append({
            'port': port,
            'path': path,
            'name': device_names.get(path, path)
        })
    return connected


def rename_device(path: str, name: str):
    device_names[path] = name
    return {'success': True, 'path': path, 'name': name}


def get_topology():
    return topology_data


def save_topology(data, filename=None):
    global topology_data, topology_filename
    if capture_process is not None:
        stop_capture()
    with topology_lock:
        topology_data = data
        topology_filename = filename
    return {'success': True, 'topology': data}


def topology_event():
    with topology_lock:
        event = dict(topology_data) if isinstance(topology_data, dict) else {}
        if topology_filename:
            event['filename'] = topology_filename
        return event


def parse_ensp_topology(content, filename):
    if content.startswith((b'\xff\xfe', b'\xfe\xff')):
        xml_text = content.decode('utf-16')
    else:
        try:
            xml_text = content.decode('utf-8-sig')
        except UnicodeDecodeError:
            xml_text = content.decode('gb18030')
    if '<!DOCTYPE' in xml_text.upper():
        raise ValueError('DOCTYPE is not supported')
    root = ET.fromstring(xml_text)
    if root.tag != 'topo':
        raise ValueError('Not an eNSP topology file')
    parsed_devices = []
    for device in root.findall('./devices/dev'):
        item = dict(device.attrib)
        item['slots'] = [dict(slot.attrib, interfaces=[dict(interface.attrib)
                         for interface in slot.findall('interface')])
                         for slot in device.findall('slot')]
        parsed_devices.append(item)
    parsed_lines = []
    for line in root.findall('./lines/line'):
        item = dict(line.attrib)
        item['interfacePairs'] = [dict(pair.attrib) for pair in line.findall('interfacePair')]
        parsed_lines.append(item)
    return {'filename': filename, 'format': 'ensp-topo', 'version': root.get('version'),
            'devices': parsed_devices, 'lines': parsed_lines,
            'content_base64': base64.b64encode(content).decode('ascii')}


def tshark_path():
    path = shutil.which('tshark')
    if path:
        return path
    path = os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'), 'Wireshark', 'tshark.exe')
    return path if os.path.isfile(path) else None


def capture_interfaces():
    executable = tshark_path()
    if not executable:
        raise ValueError('未找到 Wireshark 的 tshark 程序')
    result = subprocess.run([executable, '-D'], capture_output=True, timeout=10)
    if result.returncode:
        raise ValueError('无法列出抓包网卡，请检查 Npcap')
    lines = result.stdout.decode('utf-8', errors='replace').splitlines()
    return [{'id': line.split('.', 1)[0], 'name': line.split('.', 1)[1].strip()}
            for line in lines if '.' in line and line.split('.', 1)[0].isdigit()]


def discover_capture_devices(device_ids):
    """Resolve eNSP device IDs from VirtualBox's registered VM configuration."""
    vbox_home = os.environ.get('VBOX_USER_HOME') or os.path.join(os.path.expanduser('~'), '.VirtualBox')
    registry = os.path.join(vbox_home, 'VirtualBox.xml')
    try:
        root = ET.parse(registry).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f'无法读取 VirtualBox 虚拟机列表：{exc}') from exc
    wanted = {str(device_id).upper() for device_id in device_ids}
    mapping = {}
    for entry in root.findall('.//{*}MachineEntry'):
        path = entry.get('src', '')
        name = os.path.splitext(os.path.basename(path))[0]
        match = re.fullmatch(r'AR_Clone_([0-9a-fA-F-]{36})', name, re.IGNORECASE)
        if not match or match.group(1).upper() not in wanted:
            continue
        device_id = match.group(1).upper()
        if not os.path.isabs(path):
            path = os.path.join(vbox_home, path)
        try:
            vm = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            continue
        machine = vm.find('.//{*}Machine')
        if machine is None or machine.get('name', '').upper() != name.upper():
            continue
        adapter = machine.find('./{*}Hardware/{*}Network/{*}Adapter[@slot="0"]')
        if adapter is None or adapter.get('enabled', '').lower() != 'true':
            continue
        if adapter.find('./{*}HostOnlyInterface') is None:
            continue
        mac = adapter.get('MACAddress', '').replace(':', '').replace('-', '').upper()
        if re.fullmatch(r'[0-9A-F]{12}', mac):
            mapping[device_id] = mac
    return mapping


def capture_reader(process, generation, lookup):
    global capture_process
    try:
        for row in process.stdout:
            parts = row.strip().split('\t')
            if len(parts) != 3:
                continue
            mac, port, length = parts
            try:
                key = (mac.replace(':', '').upper(), int(port) - 10000)
                length = int(length)
            except ValueError:
                continue
            with capture_lock:
                if generation != capture_generation:
                    break
                for index in lookup.get(key, ()):
                    current = capture_counts.setdefault(index, [0, 0])
                    current[0] += 1
                    current[1] += length
    finally:
        process.stdout.close()
        with capture_lock:
            if generation == capture_generation:
                capture_process = None
                socketio.emit('capture_status', {'running': False, 'message': '抓包已停止'})


def capture_publisher(process, generation, mapped_links):
    last_time = time.monotonic()
    while process.poll() is None:
        time.sleep(1)
        with capture_lock:
            if generation != capture_generation:
                break
            now = time.monotonic()
            elapsed = now - last_time
            last_time = now
            rates = {str(index): {'pps': capture_counts.get(index, [0, 0])[0] / elapsed,
                                  'bps': capture_counts.get(index, [0, 0])[1] * 8 / elapsed}
                     for index in mapped_links}
            capture_counts.clear()
        socketio.emit('capture_rates', rates)


@app.route('/api/capture/interfaces')
def api_capture_interfaces():
    try:
        return jsonify({'interfaces': capture_interfaces()})
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/capture/start', methods=['POST'])
def api_capture_start():
    global capture_process, capture_generation
    data = request.get_json(silent=True) or {}
    interface = str(data.get('interface', ''))
    try:
        if interface not in {item['id'] for item in capture_interfaces()}:
            raise ValueError('请选择有效的抓包网卡')
        with topology_lock:
            lines = topology_data.get('lines', []) if isinstance(topology_data, dict) else []
        device_ids = {str(line.get(key, '')).upper() for line in lines
                      for key in ('srcDeviceID', 'destDeviceID')}
        if not device_ids:
            raise ValueError('当前拓扑没有可监控的连线，请先上传 eNSP 拓扑')
        mapping = discover_capture_devices(device_ids)
        missing = device_ids - mapping.keys()
        if missing:
            raise ValueError('无法自动识别以下设备的虚拟网卡：' + ', '.join(sorted(missing)) +
                             '。请先启动这些 eNSP 设备并检查 VirtualBox 配置读取权限。')
        lookup = {}
        for index, line in enumerate(lines):
            for pair in line.get('interfacePairs', []):
                for device_key, port_key in (('srcDeviceID', 'srcIndex'), ('destDeviceID', 'tarIndex')):
                    mac = mapping.get(str(line.get(device_key, '')).upper())
                    port = pair.get(port_key)
                    if mac and str(port).isdigit():
                        lookup.setdefault((mac, int(port)), set()).add(index)
        if not lookup:
            raise ValueError('没有可监控的拓扑连线，请先上传拓扑并检查设备 MAC 映射')
        with capture_lock:
            if capture_process is not None and capture_process.poll() is None:
                return jsonify({'success': False, 'error': '监控已经运行'}), 409
            capture_generation += 1
            generation = capture_generation
            capture_counts.clear()
            process = subprocess.Popen(
                [tshark_path(), '-l', '-n', '-i', interface, '-f', 'udp portrange 10000-10099',
                 '-Y', 'udp.length > 12', '-T', 'fields', '-e', 'eth.src', '-e', 'udp.srcport', '-e', 'frame.len'],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            capture_process = process
        threading.Thread(target=capture_reader, args=(process, generation, lookup), daemon=True).start()
        threading.Thread(target=capture_publisher, args=(process, generation, set().union(*lookup.values())), daemon=True).start()
        socketio.emit('capture_status', {'running': True, 'message': '监控中（封装流量估算）'})
        return jsonify({'success': True, 'mapped_links': len(set().union(*lookup.values()))})
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400


@app.route('/api/capture/stop', methods=['POST'])
def api_capture_stop():
    stop_capture()
    return jsonify({'success': True})


def stop_capture():
    global capture_process, capture_generation
    with capture_lock:
        process = capture_process
        capture_process = None
        capture_generation += 1
        capture_counts.clear()
    if process and process.poll() is None:
        process.terminate()
    socketio.emit('capture_status', {'running': False, 'message': '监控已停止'})


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/devices/scan')
def api_scan():
    start = request.args.get('start', 2000, type=int)
    end = request.args.get('end', 2050, type=int)
    result = scan_devices(start, end)
    return jsonify(result)


@app.route('/api/devices/connect', methods=['POST'])
def api_connect():
    data = request.get_json()
    port = data.get('port')
    result = connect_device(port)
    if result['success']:
        socketio.emit('device_connected', {'port': port, 'path': result['path'], 'name': result['name']})
    else:
        socketio.emit('device_error', {'port': port, 'error': result['error']})
    return jsonify(result)


@app.route('/api/devices/command', methods=['POST'])
def api_command():
    data = request.get_json()
    path = data.get('path')
    command = data.get('command')
    result = send_command(path, command)
    if result['success']:
        socketio.emit('device_output', {'path': path, 'output': result['output']})
    else:
        socketio.emit('device_error', {'path': path, 'error': result['error']})
    return jsonify(result)


@app.route('/api/devices/disconnect', methods=['POST'])
def api_disconnect():
    data = request.get_json()
    path = data.get('path')
    result = disconnect_device(path)
    if result['success']:
        socketio.emit('device_disconnected', {'path': path})
    return jsonify(result)


@app.route('/api/devices')
def api_get_devices():
    result = get_connected_devices()
    return jsonify(result)


@app.route('/api/devices/rename', methods=['POST'])
def api_rename():
    data = request.get_json()
    path = data.get('path')
    name = data.get('name')
    result = rename_device(path, name)
    socketio.emit('device_renamed', {'path': path, 'name': name})
    return jsonify(result)


@app.route('/api/topology', methods=['GET'])
def api_get_topology():
    return jsonify(get_topology())


@app.route('/api/topology', methods=['POST'])
def api_save_topology():
    data = request.get_json()
    result = save_topology(data)
    socketio.emit('topology_updated', topology_event())
    return jsonify(result)


@app.route('/api/topology/file', methods=['POST'])
def api_upload_topology_file():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file provided'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'}), 400
    extension = os.path.splitext(file.filename)[1].lower()
    if extension not in ('.topo', '.json', '.txt'):
        return jsonify({'success': False, 'error': 'Only TOPO, JSON and TXT files are supported'}), 400
    filename = secure_filename(file.filename)
    if os.path.splitext(filename)[1].lower() != extension:
        filename = 'topology' + extension
    try:
        raw_content = file.read()
        if extension == '.topo':
            data = parse_ensp_topology(raw_content, filename)
        else:
            content = raw_content.decode('utf-8-sig')
            data = json.loads(content) if extension == '.json' else {'filename': filename, 'content': content}
    except (UnicodeDecodeError, ValueError, ET.ParseError) as exc:
        return jsonify({'success': False, 'error': f'Invalid file content: {exc}'}), 400
    result = save_topology(data, filename)
    socketio.emit('topology_updated', topology_event())
    return jsonify(result)


@socketio.on('connect')
def on_socket_connect():
    if topology_filename:
        emit('topology_updated', topology_event())
    with capture_lock:
        running = capture_process is not None and capture_process.poll() is None
    emit('capture_status', {'running': running,
                            'message': '监控中（封装流量估算）' if running else '未监控'})


@socketio.on('scan')
def on_scan(data):
    start = data.get('start', 2000)
    end = data.get('end', 2050)
    result = scan_devices(start, end)
    emit('scan_result', result)


@socketio.on('get_connected_devices')
def on_get_connected_devices():
    connected = get_connected_devices()
    emit('connected_devices_list', connected)


@socketio.on('connect_device')
def on_connect(data):
    port = data.get('port')
    result = connect_device(port)
    if result['success']:
        emit('device_connected', {'port': port, 'path': result['path'], 'name': result['name']})
    else:
        emit('device_error', {'port': port, 'error': result['error']})


@socketio.on('send_command')
def on_send(data):
    path = data.get('path')
    cmd = data.get('command')
    result = send_command(path, cmd)
    if result['success']:
        emit('device_output', {'path': path, 'output': result['output']})
    else:
        emit('device_error', {'path': path, 'error': result['error']})


@socketio.on('disconnect_device')
def on_disconnect_device(data):
    path = data.get('path')
    result = disconnect_device(path)
    if result['success']:
        emit('device_disconnected', {'path': path})


@socketio.on('rename_device')
def on_rename_device(data):
    path = data.get('path')
    new_name = data.get('name', path)
    result = rename_device(path, new_name)
    emit('device_renamed', {'path': path, 'name': new_name})


if __name__ == '__main__':
    print('eNSP Server starting on http://127.0.0.1:5000')
    socketio.run(app, host='0.0.0.0', port=5000)
