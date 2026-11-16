"""Estimate eNSP link traffic from its VirtualBox host-only UDP transport."""
import os
import re
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
import socket
import struct
import hashlib
from collections import deque


def decode_inner_packet(hex_data):
    result = {'protocol': '未知', 'source': '', 'destination': '', 'detail': '', 'analysis': [], 'warnings': []}
    try:
        frame = bytes.fromhex(hex_data.replace(':', ''))
        if len(frame) < 14:
            result['warnings'].append('以太网帧不完整，无法解析')
            return result
        result['inner_length'] = len(frame)
        result['source_mac'] = ':'.join(f'{b:02x}' for b in frame[6:12])
        result['destination_mac'] = ':'.join(f'{b:02x}' for b in frame[:6])
        ethertype = struct.unpack_from('!H', frame, 12)[0]
        offset = 14
        if ethertype in (0x8100, 0x88a8) and len(frame) >= 18:
            result['vlan'] = struct.unpack_from('!H', frame, 14)[0] & 0xfff
            result['analysis'].append(f"802.1Q VLAN {result['vlan']}")
            ethertype = struct.unpack_from('!H', frame, 16)[0]
            offset = 18
        if ethertype == 0x0806 and len(frame) >= offset + 28:
            result.update(protocol='ARP', source=socket.inet_ntoa(frame[offset+14:offset+18]),
                          destination=socket.inet_ntoa(frame[offset+24:offset+28]),
                          detail='操作码 ' + str(struct.unpack_from('!H', frame, offset+6)[0]))
            opcode = struct.unpack_from('!H', frame, offset+6)[0]
            result['analysis'].append({1: 'ARP 请求：查询目标 IPv4 地址的 MAC', 2: 'ARP 响应：通告 IPv4 与 MAC 的对应关系'}.get(opcode, f'ARP 操作码 {opcode}'))
        elif ethertype == 0x0800 and len(frame) >= offset + 20 and frame[offset] >> 4 == 4:
            ihl = (frame[offset] & 15) * 4
            if ihl < 20 or len(frame) < offset + ihl:
                result['warnings'].append('IPv4 首部长度无效或数据被截断')
                return result
            total_length = struct.unpack_from('!H', frame, offset+2)[0]
            if total_length < ihl:
                result['warnings'].append('IPv4 总长度小于首部长度')
                return result
            captured_end = min(len(frame), offset + total_length)
            if offset + total_length > len(frame):
                result['warnings'].append('IPv4 数据包被截断，协议分析可能不完整')
            proto = frame[offset+9]
            result.update(source=socket.inet_ntoa(frame[offset+12:offset+16]),
                          destination=socket.inet_ntoa(frame[offset+16:offset+20]),
                          protocol={1:'ICMP', 6:'TCP', 17:'UDP', 89:'OSPF'}.get(proto, f'IPv4/{proto}'))
            result['ttl'] = frame[offset+8]
            result['ip_length'] = total_length
            result['ip_id'] = struct.unpack_from('!H', frame, offset+4)[0]
            frag = struct.unpack_from('!H', frame, offset+6)[0]
            fragment_offset = frag & 0x1fff
            payload = offset + ihl
            result['analysis'].append(f"IPv4 TTL {result['ttl']}，总长度 {total_length} 字节，标识 {result['ip_id']}")
            if fragment_offset:
                result['detail'] = 'IPv4 分片'
                result['analysis'].append(f'非首个 IPv4 分片，偏移 {fragment_offset * 8} 字节；不能解析传输层首部')
            elif proto in (6, 17) and captured_end >= payload + 4:
                src, dst = struct.unpack_from('!HH', frame, payload)
                result['source_port'], result['destination_port'] = src, dst
                result['detail'] = f'{src} → {dst}'
                result['analysis'].append(f'{result["protocol"]} 端口：{src} → {dst}')
                if proto == 6 and captured_end >= payload + 20:
                    flags = frame[payload+13]
                    names = [name for bit, name in ((0x01,'FIN'), (0x02,'SYN'), (0x04,'RST'), (0x08,'PSH'), (0x10,'ACK'), (0x20,'URG'), (0x40,'ECE'), (0x80,'CWR')) if flags & bit]
                    result['tcp_flags'] = names
                    result['analysis'].append('TCP 标志：' + (', '.join(names) or '无'))
                    if flags & 0x04:
                        result['analysis'].append('RST 表示连接被重置；需结合上下文判断原因')
                elif proto == 17 and captured_end >= payload + 8:
                    udp_length = struct.unpack_from('!H', frame, payload+4)[0]
                    result['analysis'].append(f'UDP 长度 {udp_length} 字节')
                    if udp_length < 8:
                        result['warnings'].append('UDP 长度小于首部长度')
                    elif not (frag & 0x2000) and payload + udp_length > offset + total_length:
                        result['warnings'].append('UDP 长度超过 IPv4 数据长度')
            elif proto == 1 and captured_end >= payload + 2:
                result['detail'] = f'类型 {frame[payload]}，代码 {frame[payload+1]}'
                icmp_type = frame[payload]
                result['analysis'].append({0:'ICMP 回显应答', 8:'ICMP 回显请求', 3:'ICMP 目标不可达', 11:'ICMP 超时'}.get(icmp_type, f'ICMP 类型 {icmp_type}') + f'，代码 {frame[payload+1]}')
            elif proto == 89 and captured_end >= payload + 2:
                result['detail'] = f'OSPF v{frame[payload]}，类型 {frame[payload+1]}'
                ospf_type = {1:'Hello', 2:'数据库描述', 3:'链路状态请求', 4:'链路状态更新', 5:'链路状态确认'}.get(frame[payload+1], '未知类型')
                result['analysis'].append(f'OSPF v{frame[payload]} {ospf_type}报文')
                if captured_end >= payload + 16:
                    result['analysis'].append('Router ID ' + socket.inet_ntoa(frame[payload+4:payload+8]) + '，Area ID ' + socket.inet_ntoa(frame[payload+8:payload+12]))
            elif proto in (1, 6, 17, 89):
                result['warnings'].append('传输层首部不完整或不可用')
            if frag & 0x2000:
                result['analysis'].append('IPv4 更多分片标志已设置')
        elif ethertype == 0x86dd and len(frame) >= offset + 40:
            result.update(protocol='IPv6', source=socket.inet_ntop(socket.AF_INET6, frame[offset+8:offset+24]),
                          destination=socket.inet_ntop(socket.AF_INET6, frame[offset+24:offset+40]),
                          detail=f'下一首部 {frame[offset+6]}')
            result['analysis'].append(f'IPv6 下一首部 {frame[offset+6]}，跳数限制 {frame[offset+7]}；暂不解析扩展首部')
        else:
            result['protocol'] = f'EtherType 0x{ethertype:04X}'
            result['analysis'].append('暂未支持该以太网协议的详细解析')
    except (ValueError, struct.error, OSError):
        result['warnings'].append('数据格式无效或解析不完整')
    return result


def tshark_path():
    path = shutil.which('tshark') or os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'), 'Wireshark', 'tshark.exe')
    if not os.path.isfile(path):
        raise ValueError('未找到 tshark，请安装 Wireshark 和 Npcap')
    return path


def list_interfaces():
    result = subprocess.run([tshark_path(), '-D'], capture_output=True, timeout=10,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('无法获取抓包网卡，请检查 Npcap')
    lines = result.stdout.decode('utf-8', errors='replace').splitlines()
    return [{'id': line.split('.', 1)[0], 'name': line.split('.', 1)[1].strip()}
            for line in lines if '.' in line and line.split('.', 1)[0].isdigit()]


def discover_devices(device_ids):
    vbox_home = os.environ.get('VBOX_USER_HOME') or os.path.join(os.path.expanduser('~'), '.VirtualBox')
    try:
        root = ET.parse(os.path.join(vbox_home, 'VirtualBox.xml')).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError('无法读取 VirtualBox 注册列表，请检查权限') from exc
    wanted = {str(i).upper() for i in device_ids}
    mapping = {}
    for entry in root.findall('.//{*}MachineEntry'):
        path = entry.get('src', '')
        name = os.path.splitext(os.path.basename(path))[0]
        match = re.fullmatch(r'AR_Clone_([0-9a-fA-F-]{36})', name, re.I)
        if not match or match.group(1).upper() not in wanted:
            continue
        if not os.path.isabs(path):
            path = os.path.join(vbox_home, path)
        try:
            vm = ET.parse(path).getroot().find('.//{*}Machine')
            if vm is None or vm.get('name', '').upper() != name.upper():
                continue
            adapter = vm.find('./{*}Hardware/{*}Network/{*}Adapter[@slot="0"]')
            if adapter is None or adapter.get('enabled', '').lower() != 'true' or adapter.find('./{*}HostOnlyInterface') is None:
                continue
            mac = adapter.get('MACAddress', '').replace(':', '').replace('-', '').upper()
            if re.fullmatch(r'[0-9A-F]{12}', mac):
                mapping[match.group(1).upper()] = mac
        except (OSError, ET.ParseError):
            continue
    return mapping


class TrafficMonitor:
    def __init__(self):
        self.lock = threading.RLock()
        self.process = None
        self.generation = 0
        self.rates = {}
        self.counts = {}
        self.message = '未监控'
        self.packets = deque(maxlen=200)
        self.packet_id = 0

    def start(self, interface, topology):
        if interface not in {item['id'] for item in list_interfaces()}:
            raise ValueError('请选择有效的抓包网卡')
        links = topology.get('connections', []) if topology else []
        if not links:
            raise ValueError('请先上传包含连线的拓扑')
        ids = {str(link[key]).upper() for link in links for key in ('source_id', 'target_id')}
        mapping = discover_devices(ids)
        missing = ids - mapping.keys()
        if missing:
            raise ValueError('无法识别设备虚拟网卡：' + ', '.join(sorted(missing)) + '。请启动 eNSP 设备并检查 VirtualBox 配置权限。')
        lookup = {}
        for index, link in enumerate(links):
            for pair_index, pair in enumerate(link.get('interface_pairs', [])):
                link_id = f'{index}:{pair_index}'
                for device_key, port_key in (('source_id', 'src_port_index'), ('target_id', 'dst_port_index')):
                    key = (mapping[str(link[device_key]).upper()], int(pair[port_key]))
                    if key in lookup and lookup[key][0] != link_id:
                        raise ValueError('拓扑中一个设备接口对应多条连线，无法准确监控')
                    lookup[key] = (link_id, str(link[device_key]))
        if not lookup:
            raise ValueError('拓扑没有可识别的接口连线')
        with self.lock:
            if self.process and self.process.poll() is None:
                raise ValueError('监控已在运行')
            self.generation += 1
            generation = self.generation
            self.counts = {}
            self.packets.clear()
            self.rates = {link_id: {'pps': 0, 'bps': 0} for link_id, _ in lookup.values()}
            process = subprocess.Popen([tshark_path(), '-l', '-n', '-i', interface, '-f', 'udp portrange 10000-10099',
                '-Y', 'udp.length > 12', '-T', 'fields', '-e', 'eth.src', '-e', 'udp.srcport', '-e', 'frame.len',
                '-e', 'frame.time_epoch', '-e', 'ip.src', '-e', 'ip.dst', '-e', 'udp.dstport', '-e', 'data.data'],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.process = process
            self.message = '监控中（封装流量估算）'
        threading.Thread(target=self._read, args=(process, generation, lookup), daemon=True).start()
        threading.Thread(target=self._sample, args=(process, generation), daemon=True).start()
        return len(self.rates)

    def _read(self, process, generation, lookup):
        try:
            for row in process.stdout:
                parts = row.rstrip('\r\n').split('\t')
                if len(parts) != 8:
                    continue
                try:
                    endpoint = lookup.get((parts[0].replace(':', '').upper(), int(parts[1]) - 10000))
                    length = int(parts[2])
                except ValueError:
                    continue
                with self.lock:
                    if generation != self.generation:
                        break
                    if endpoint is not None:
                        link_id, sender_id = endpoint
                        count = self.counts.setdefault(link_id, [0, 0])
                        count[0] += 1
                        count[1] += length
                        self.packet_id += 1
                        packet = decode_inner_packet(parts[7])
                        packet.update(id=self.packet_id, link_id=link_id, timestamp=parts[3],
                                      frame_length=length, outer_source=parts[4], outer_destination=parts[5],
                                      outer_source_port=int(parts[1]), outer_destination_port=parts[6],
                                      sender_id=sender_id)
                        try:
                            frame = bytes.fromhex(parts[7].replace(':', ''))
                            # IPv4 header checksum and TTL change at each router; retain the IP ID and payload.
                            offset = 18 if len(frame) >= 18 and frame[12:14] in (b'\x81\x00', b'\x88\xa8') else 14
                            ip = bytearray(frame[offset:])
                            if len(ip) >= 20 and ip[0] >> 4 == 4:
                                total = int.from_bytes(ip[2:4], 'big')
                                if 20 <= total <= len(ip):
                                    ip = ip[:total]
                                    ip[8] = 0
                                    ip[10:12] = b'\0\0'
                                    packet['trace_key'] = hashlib.sha256(ip).hexdigest()
                        except ValueError:
                            pass
                        self.packets.append(packet)
        finally:
            process.stdout.close()
            with self.lock:
                if generation == self.generation:
                    self.process = None
                    self.rates = {}
                    self.message = '抓包已停止，请检查网卡和 Npcap 权限'

    def _sample(self, process, generation):
        last = time.monotonic()
        while process.poll() is None:
            time.sleep(1)
            with self.lock:
                if generation != self.generation:
                    return
                now = time.monotonic()
                elapsed = now - last
                last = now
                for link_id in self.rates:
                    count = self.counts.get(link_id, [0, 0])
                    self.rates[link_id] = {'pps': count[0] / elapsed, 'bps': count[1] * 8 / elapsed}
                self.counts.clear()

    def stop(self):
        with self.lock:
            process = self.process
            self.process = None
            self.generation += 1
            self.rates = {}
            self.counts = {}
            self.message = '监控已停止'
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def status(self):
        with self.lock:
            return {'running': self.process is not None and self.process.poll() is None,
                    'message': self.message, 'rates': dict(self.rates)}

    def packet_snapshot(self):
        with self.lock:
            return list(self.packets)

    def clear_packets(self):
        with self.lock:
            self.packets.clear()


traffic_monitor = TrafficMonitor()
