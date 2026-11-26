"""Authenticated EVE-NG/PNETLab discovery. Credentials remain in memory."""
import copy
import http.cookiejar
import json
import posixpath
import threading
import time
from urllib.parse import urlsplit, quote
from urllib.request import Request, build_opener, ProxyHandler, HTTPCookieProcessor, HTTPRedirectHandler
from urllib.error import HTTPError, URLError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class EveService:
    def __init__(self):
        self.lock = threading.RLock()
        self.config = {'base_url': '', 'kind': 'eve', 'lab_path': '', 'username': ''}
        self.cookie = ''
        self.opener = self._opener()
        self.cached_nodes = None
        self.cache_time = 0
        self.cache_lab = None

    def _opener(self):
        return build_opener(ProxyHandler({}), NoRedirect(), HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def settings(self):
        with self.lock:
            return {**self.config, 'authenticated': self.config.get('authenticated', False)}

    def _request(self, path, payload=None):
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if self.cookie:
            headers['Cookie'] = self.cookie
        data = json.dumps(payload).encode() if payload is not None else None
        try:
            with self.opener.open(Request(self.config['base_url'] + path, data=data, headers=headers), timeout=12) as response:
                result = json.load(response)
        except HTTPError as exc:
            code = exc.code
            exc.close()
            raise ValueError(f'EVE/PNET API 返回 HTTP {code}；请检查登录会话、类型和实验路径') from None
        except (OSError, URLError, ValueError):
            raise ValueError('无法读取 EVE/PNET API，请检查服务器地址和登录会话') from None
        if not isinstance(result, dict) or result.get('status') in ('error', 'fail', 'unauthorized') or result.get('result') is False:
            raise ValueError('EVE/PNET API 请求失败，请检查会话和实验是否已打开')
        return result.get('data', result)

    def configure(self, data):
        if not isinstance(data, dict):
            raise ValueError('服务器设置格式无效')
        base = str(data.get('base_url', '')).strip().rstrip('/')
        parsed = urlsplit(base)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('请输入有效的 EVE/PNET 服务器地址')
        parsed.port
        if parsed.path not in ('', '/', '/legacy/topology'):
            raise ValueError('请填写服务器根地址，例如 http://192.168.52.128')
        base = parsed.scheme + '://' + parsed.netloc
        kind = data.get('kind', 'eve')
        if kind not in ('eve', 'pnet'):
            raise ValueError('服务器类型无效')
        lab_path = str(data.get('lab_path', '')).strip()
        if kind == 'eve' and (not lab_path.startswith('/') or not lab_path.endswith('.unl') or '..' in lab_path.split('/')):
            raise ValueError('EVE-NG 实验路径应为 /目录/实验.unl')
        for key in ('username', 'password', 'cookie'):
            if not isinstance(data.get(key, ''), str) or any(ord(c) < 32 for c in data.get(key, '')):
                raise ValueError('登录信息格式无效')
        with self.lock:
            old = (self.config, self.cookie, self.opener)
            self.config = {'base_url': base, 'kind': kind, 'lab_path': lab_path, 'username': data.get('username', '')}
            self.opener = self._opener()
            self.cookie = data.get('cookie', '').strip() if kind == 'pnet' else ''
            try:
                if kind == 'eve':
                    self._request('/api/auth/login', {'username': self.config['username'], 'password': data.get('password', ''), 'html5': '-1'})
                elif not self.cookie:
                    raise ValueError('PNETLab 请填写已登录并打开实验的浏览器 Cookie')
                self._request('/api/auth')
                self.config['authenticated'] = True
            except Exception:
                self.config, self.cookie, self.opener = old
                raise
            self.cached_nodes = None
        return self.settings()

    def bind(self, topology):
        if topology.get('format') != 'eve-ng':
            return topology
        with self.lock:
            if not self.config.get('authenticated'):
                raise ValueError('请先在拓扑分析中设置 EVE-NG / PNETLab 服务器')
            # A short cache avoids repeated API requests during UI/AI graph reads.
            if self.cached_nodes is not None and self.cache_lab == topology.get('lab_name') and time.monotonic() - self.cache_time < 3:
                nodes = self.cached_nodes
            else:
                if self.config['kind'] == 'pnet':
                    payload = self._request('/api/labs/session/topology')
                    lab = payload.get('labinfo', {}) if isinstance(payload, dict) else {}
                    lab_name = lab.get('name') if isinstance(lab, dict) else None
                    if lab_name is not None and not isinstance(lab_name, str):
                        raise ValueError('服务器实验名称格式无效')
                    if lab_name and topology.get('lab_name') and posixpath.splitext(posixpath.basename(lab_name))[0] != topology['lab_name']:
                        raise ValueError('服务器当前实验名称与上传拓扑不匹配')
                    nodes = payload.get('nodes', {}) if isinstance(payload, dict) else {}
                else:
                    path = '/api/labs' + quote(self.config['lab_path'], safe='/')
                    nodes = self._request(path + '/nodes')
                self.cached_nodes, self.cache_time = nodes, time.monotonic()
                self.cache_lab = topology.get('lab_name')
            if not isinstance(nodes, (dict, list)):
                raise ValueError('服务器未返回有效的节点清单')
            records = {str(n.get('id', key)): n for key, n in (nodes.items() if isinstance(nodes, dict) else enumerate(nodes)) if isinstance(n, dict)}
            expected = {key for key, d in topology['device_inventory'].items() if d.get('category') != '网络'}
            if set(records) != expected:
                raise ValueError('服务器实验的节点清单与上传拓扑不匹配，请打开相同实验')
            result = copy.deepcopy(topology)
            for node_id, device in result['device_inventory'].items():
                if device.get('category') == '网络':
                    continue
                record = records.get(node_id)
                if not record or record.get('name') != device['name']:
                    raise ValueError(f'服务器实验与上传拓扑不匹配：{device["name"]}')
                template = record.get('template')
                if template and template != device['model']:
                    raise ValueError(f'设备型号不匹配：{device["name"]}')
                if record.get('uuid') and device.get('uuid') and record['uuid'] != device['uuid']:
                    raise ValueError(f'设备 UUID 不匹配：{device["name"]}')
                url = str(record.get('url', ''))
                try:
                    console = urlsplit(url)
                except ValueError:
                    console = urlsplit('')
                device['com_port'] = ''
                device['console_host'] = ''
                device['runtime_status'] = record.get('status')
                if console.scheme == 'telnet' and console.hostname and not console.username and not console.password:
                    try:
                        port = console.port
                    except ValueError:
                        port = None
                    if port and 1 <= port <= 65535:
                        address = console.hostname
                        if address in ('0.0.0.0', '127.0.0.1', 'localhost', '::', '::1'):
                            address = urlsplit(self.config['base_url']).hostname
                        device.update(console_host=address, com_port=str(port))
                device['console_error'] = '' if device['com_port'] else '服务器未提供 Telnet 控制台（请检查设备状态和控制台类型）'
            result['runtime_server'] = self.config['base_url']
            return result


eve_service = EveService()
