"""Remote Ethernet capture for EVE/PNET; mappings are explicitly chosen by the user."""
import base64
import atexit
import hashlib
import json
import re
import shlex
import socket
import struct
import threading

from traffic_service import TrafficMonitor, decode_inner_packet


class EveSSH:
    def __init__(self):
        self.lock = threading.RLock()
        self.config = None

    def settings(self):
        with self.lock:
            return {k: v for k, v in (self.config or {}).items() if k != 'password'}

    def configure(self, data):
        if not isinstance(data, dict):
            raise ValueError('SSH 设置格式无效')
        host, user = data.get('host', ''), data.get('username', '')
        password, fingerprint = data.get('password', ''), data.get('fingerprint', '')
        port = data.get('port', 22)
        if (not all(isinstance(v, str) for v in (host, user, password, fingerprint))
                or not re.fullmatch(r'[A-Za-z0-9_.:\-]+', host) or not user.strip()
                or any(ord(c) < 32 for c in user) or type(port) is not int or not 1 <= port <= 65535):
            raise ValueError('SSH 地址、端口或用户名无效')
        fingerprint = fingerprint.strip()
        if fingerprint and not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}', fingerprint):
            raise ValueError('SSH 主机指纹应为 SHA256:... 格式')
        config = dict(host=host, port=port, username=user.strip(), password=password, fingerprint=fingerprint)
        with self.lock:
            interfaces = self.interfaces(config)
            self.config = config
        return interfaces

    def connect(self, config=None):
        try:
            import paramiko
        except ImportError:
            raise ValueError('远程抓包需要 paramiko，请安装 requirements.txt 中的依赖') from None
        with self.lock:
            config = dict(config or self.config or {})
        if not config:
            raise ValueError('请先设置 EVE/PNET 远程抓包 SSH')
        fingerprint = config.pop('fingerprint', '')

        class PinnedHost(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                actual = 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
                if not fingerprint or fingerprint != actual:
                    raise ValueError('SSH 主机未受信任；请在服务器核对主机指纹后填写：' + actual)

        client = paramiko.SSHClient()
        try:
            client.load_system_host_keys()
            client.set_missing_host_key_policy(PinnedHost())
            client.connect(**config, timeout=10, banner_timeout=10, auth_timeout=10,
                           look_for_keys=not bool(config['password']), allow_agent=not bool(config['password']))
            return client
        except ValueError:
            client.close()
            raise
        except Exception:
            client.close()
            raise ValueError('SSH 连接失败，请检查地址、凭据和已保存的主机密钥') from None

    def interfaces(self, config=None):
        client = self.connect(config)
        try:
            _, stdout, stderr = client.exec_command('ip -j link show', timeout=10)
            raw = stdout.read(1024 * 1024)
            if stdout.channel.recv_exit_status() != 0:
                raise ValueError('无法读取服务器网卡，请确认服务器安装 iproute2')
            records = json.loads(raw)
            return [{'id': row['ifname'], 'name': row['ifname'] + ' · ' + str(row.get('operstate', ''))}
                    for row in records if isinstance(row, dict) and isinstance(row.get('ifname'), str)
                    and row.get('link_type') == 'ether']
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError('读取服务器网卡失败，请检查 SSH 和 ip -j link 输出') from None
        finally:
            client.close()


def read_exact(channel, size):
    data = bytearray()
    while len(data) < size:
        try:
            chunk = channel.recv(size - len(data))
        except socket.timeout:
            if channel.closed:
                raise EOFError
            continue
        if not chunk:
            raise EOFError
        data.extend(chunk)
    return bytes(data)


def pcap_packets(channel):
    """Read classic pcap, including byte-order and nanosecond variants."""
    header = read_exact(channel, 24)
    formats = {b'\xd4\xc3\xb2\xa1': ('<', 1e6), b'\xa1\xb2\xc3\xd4': ('>', 1e6),
               b'\x4d\x3c\xb2\xa1': ('<', 1e9), b'\xa1\xb2\x3c\x4d': ('>', 1e9)}
    if header[:4] not in formats:
        raise ValueError('远程输出不是 pcap，请检查 tcpdump 和抓包权限')
    endian, divisor = formats[header[:4]]
    major, minor, _, _, snaplen, linktype = struct.unpack(endian + 'HHiiII', header[4:])
    if (major, minor) != (2, 4) or linktype != 1 or not 1 <= snaplen <= 1024 * 1024:
        raise ValueError('只支持 Ethernet pcap，请选择设备的以太网 TAP/网桥接口')
    while True:
        seconds, fraction, captured, original = struct.unpack(endian + 'IIII', read_exact(channel, 16))
        if captured > snaplen or original < captured or fraction >= divisor:
            raise ValueError('远程 pcap 记录长度或时间戳无效')
        yield seconds + fraction / divisor, original, read_exact(channel, captured)


class CaptureGroup:
    def __init__(self, client):
        self.client, self.channels, self.closed = client, [], False

    def poll(self):
        transport = self.client.get_transport()
        if self.closed or not transport or not transport.is_active():
            return 0
        return None if any(not c.closed and not c.exit_status_ready() for c in self.channels) else 0

    def terminate(self):
        self.closed = True
        for channel in self.channels:
            channel.close()
        self.client.close()

    def wait(self, timeout=None):
        return 0


class EveTrafficMonitor(TrafficMonitor):
    def start(self, mappings, topology):
        if not topology or topology.get('format') != 'eve-ng':
            raise ValueError('请先上传 EVE-NG / PNETLab 拓扑')
        if not isinstance(mappings, list) or not mappings or len(mappings) > 32:
            raise ValueError('请指定 1 至 32 条链路的远程网卡映射')
        available = {row['id'] for row in eve_ssh.interfaces()}
        valid_links = {f'{i}:{j}' for i, link in enumerate(topology['connections']) for j, _ in enumerate(link['interface_pairs'])}
        selected, used_interfaces = {}, set()
        for item in mappings:
            if not isinstance(item, dict):
                raise ValueError('抓包映射格式无效')
            key, interface = item.get('link_id'), item.get('interface')
            if not isinstance(key, str) or not isinstance(interface, str) or key not in valid_links or interface not in available:
                raise ValueError('抓包链路或服务器网卡无效，请刷新后重试')
            if key in selected or interface in used_interfaces:
                raise ValueError('同一链路或网卡只能映射一次，以免重复计数')
            selected[key] = interface
            used_interfaces.add(interface)
        with self.lock:
            if self.process and self.process.poll() is None:
                raise ValueError('远程监控已在运行')
            group = CaptureGroup(eve_ssh.connect())
            try:
                for interface in selected.values():
                    channel = group.client.get_transport().open_session(timeout=10)
                    group.channels.append(channel)
                    channel.settimeout(1)
                    channel.exec_command('exec tcpdump -U -n -s 65535 -w - -i ' + shlex.quote(interface))
            except Exception:
                group.terminate()
                raise ValueError('无法启动远程 tcpdump，请检查 SSH 和抓包权限') from None
            self.generation += 1
            generation = self.generation
            self.process, self.counts = group, {}
            self.packets.clear()
            self.rates = {key: {'pps': 0, 'bps': 0, 'endpoints': 0} for key in selected}
            self.message = f'远程监控中：覆盖 {len(selected)}/{len(valid_links)} 条链路；方向未知，映射由用户指定'
        for (key, interface), channel in zip(selected.items(), group.channels):
            threading.Thread(target=self._read_remote, args=(group, channel, generation, key, interface), daemon=True).start()
        threading.Thread(target=self._sample, args=(group, generation), daemon=True).start()
        return len(selected)

    def _read_remote(self, group, channel, generation, key, interface):
        error = ''
        try:
            for timestamp, length, frame in pcap_packets(channel):
                with self.lock:
                    if generation != self.generation:
                        break
                    count = self.counts.setdefault(key, [0, 0])
                    count[0] += 1
                    count[1] += length
                    self.packet_id += 1
                    packet = decode_inner_packet(frame.hex())
                    packet.update(id=self.packet_id, link_id=key, timestamp=str(timestamp), frame_length=length,
                                  capture_source='eve-ssh', capture_interface=interface, sender_id=None)
                    packet['analysis'].append('服务器网卡抓包；链路来自用户映射，无法仅凭该报文确认拓扑方向')
                    self.packets.append(packet)
        except EOFError:
            error = '抓包流已结束，请检查 tcpdump 是否安装、网卡和抓包权限'
        except (ValueError, OSError) as exc:
            error = str(exc)
        except Exception:
            error = 'SSH 抓包连接已中断'
        finally:
            channel.close()
            with self.lock:
                if generation == self.generation:
                    self.rates.pop(key, None)
                    self.counts.pop(key, None)
                    self.message = f'远程链路 {key} 已停止：{error}' if error else f'远程链路 {key} 已停止'
                    if not self.rates:
                        group.terminate()
                        self.process = None


eve_ssh = EveSSH()
eve_traffic_monitor = EveTrafficMonitor()
atexit.register(eve_traffic_monitor.stop)
