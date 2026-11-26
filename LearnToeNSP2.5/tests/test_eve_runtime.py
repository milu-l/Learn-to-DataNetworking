import copy
import io
import json
import socket
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import app
import eve_capture
import mcp_tools
import topo_service
import topology_resolver
from eve_service import EveService
from test_eve_unl import UNL
from topology_archive import export_archive, import_archive


def topology():
    return topo_service.parse_topology_from_content(UNL)


def server_nodes():
    return {str(i): {'name': 'CE' + str(i), 'template': 'huaweice12800',
                     'url': f'telnet://192.168.52.128:{32768+i}', 'status': 2} for i in (1, 2)}


class EveRuntimeTests(unittest.TestCase):
    def service(self, kind='pnet'):
        service = EveService()
        service.config = {'base_url': 'http://192.168.52.128', 'kind': kind, 'lab_path': '/测试/lab.unl', 'authenticated': True}
        return service

    def test_pnet_cookie_login_normalizes_url_and_never_returns_cookie(self):
        service = EveService()
        with patch.object(service, '_request', return_value={}):
            result = service.configure({'base_url': 'http://192.168.52.128/legacy/topology', 'kind': 'pnet', 'cookie': 'session=secret'})
        self.assertTrue(result['authenticated'])
        self.assertEqual(result['base_url'], 'http://192.168.52.128')
        self.assertNotIn('secret', json.dumps(result))
        self.assertNotIn('cookie', result)

    def test_eve_login_and_encoded_lab_path(self):
        service = EveService()
        with patch.object(service, '_request', return_value={}) as api:
            result = service.configure({'base_url': 'http://eve.test', 'kind': 'eve', 'lab_path': '/测试/lab.unl', 'username': 'user', 'password': 'secret'})
            self.assertEqual(api.call_args_list[0].args, ('/api/auth/login', {'username': 'user', 'password': 'secret', 'html5': '-1'}))
        self.assertNotIn('password', result)
        with patch.object(service, '_request', return_value=server_nodes()) as api:
            service.bind(topology())
        self.assertEqual(api.call_args.args[0], '/api/labs/%E6%B5%8B%E8%AF%95/lab.unl/nodes')

    def test_failed_auth_keeps_previous_session(self):
        service = self.service()
        old_config, old_opener = service.config, service.opener
        with patch.object(service, '_request', side_effect=ValueError('登录失败')):
            with self.assertRaises(ValueError):
                service.configure({'base_url': 'http://other.test', 'kind': 'pnet', 'cookie': 'bad=secret'})
        self.assertIs(service.config, old_config)
        self.assertIs(service.opener, old_opener)
        self.assertEqual(service.cookie, '')

    def test_invalid_server_settings_are_rejected(self):
        service = EveService()
        for data in (None, {'base_url': 'file:///tmp/a'}, {'base_url': 'http://u:pass@host'},
                     {'base_url': 'http://host', 'lab_path': '/../a.unl'},
                     {'base_url': 'http://host', 'kind': 'pnet', 'cookie': 'abc\r\nHost: other'}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                service.configure(data)

    def test_runtime_binding_copies_topology_and_uses_api_ports(self):
        service, original = self.service(), topology()
        with patch.object(service, '_request', return_value={'nodes': server_nodes()}) as api:
            runtime = service.bind(original)
            service.bind(original)
        self.assertEqual(api.call_count, 1)
        device = runtime['device_inventory']['1']
        self.assertEqual((device['console_host'], device['com_port']), ('192.168.52.128', '32769'))
        self.assertEqual(original['device_inventory']['1']['com_port'], '')
        self.assertEqual(runtime['device_inventory']['eve-network:1']['category'], '网络')

    def test_wrong_lab_name_template_or_node_set_is_rejected(self):
        for change in ('name', 'template', 'extra', 'missing'):
            records = server_nodes()
            if change in ('name', 'template'): records['1'][change] = 'other'
            elif change == 'extra': records['3'] = {'name': 'extra'}
            else: records.pop('1')
            with self.subTest(change=change), patch.object(EveService, '_request', return_value={'nodes': records}):
                with self.assertRaises(ValueError): self.service().bind(topology())

    def test_non_telnet_or_invalid_port_never_gets_guessed_console(self):
        for url in ('vnc://192.168.52.128:5901', '', 'telnet://host:bad', 'telnet://host:0', 'telnet://[bad'):
            records = server_nodes()
            records['1']['url'] = url
            with self.subTest(url=url), patch.object(EveService, '_request', return_value={'nodes': records}):
                device = self.service().bind(topology())['device_inventory']['1']
                self.assertEqual(device['com_port'], '')
                self.assertTrue(device['console_error'])

    def test_loopback_console_host_is_resolved_to_remote_server(self):
        records = server_nodes()
        records['1']['url'] = 'telnet://127.0.0.1:32769'
        with patch.object(EveService, '_request', return_value={'nodes': records}):
            self.assertEqual(self.service().bind(topology())['device_inventory']['1']['console_host'], '192.168.52.128')

    def test_wrong_lab_name_is_rejected_even_when_nodes_match(self):
        with patch.object(EveService, '_request', return_value={'labinfo': {'name': 'other.unl'}, 'nodes': server_nodes()}):
            with self.assertRaisesRegex(ValueError, '实验名称'):
                self.service().bind(topology())

    def test_parallel_eve_links_require_declared_and_mutual_neighbor_matches(self):
        graph = topology()
        graph['connections'] = [copy.deepcopy(graph['connections'][1]) for _ in range(2)]
        graph['connections'][1]['interface_pairs'][0]['declared'] = {'source_interface': 'GE1/0/2', 'target_interface': 'GE1/0/3'}
        observations = {'1': {'system_name': 'CE1', 'neighbors': [
            {'local': 'GE1/0/0', 'neighbor': 'CE2', 'remote': 'GE1/0/1'},
            {'local': 'GE1/0/2', 'neighbor': 'CE2', 'remote': 'GE1/0/3'}]},
            '2': {'system_name': 'CE2', 'neighbors': [
            {'local': 'GE1/0/1', 'neighbor': 'CE1', 'remote': 'GE1/0/0'},
            {'local': 'GE1/0/3', 'neighbor': 'CE1', 'remote': 'GE1/0/2'}]}}
        verified = topology_resolver.verified_links(graph, observations)
        self.assertEqual(set(verified), {'0:0', '1:0'})
        observations['2']['neighbors'][1]['remote'] = 'GE1/0/9'
        self.assertEqual(set(topology_resolver.verified_links(graph, observations)), {'0:0'})

    def test_interface_archive_roundtrip_for_eve(self):
        graph = topology()
        archive = export_archive(graph, {'1:0': {'source_interface': 'GE1/0/0', 'target_interface': 'GE1/0/1'}}, {}, {})
        manual, restored = import_archive(graph, json.loads(json.dumps(archive)))
        self.assertEqual(restored['1:0']['source_interface'], 'GE1/0/0')
        self.assertEqual(manual, {})

    def test_ai_uses_remote_endpoint_instead_of_local_session_with_same_port(self):
        graph = {'devices': [{'id': '1', 'name': 'CE1', 'model': 'huawei', 'console_host': '192.168.52.128', 'com_port': '32769'}],
                 'connections': [{'source_id': '1', 'target_id': '1', 'interface_pairs': [{}]}]}
        sessions = [{'ip': '127.0.0.1', 'port': 32769, 'session_id': 'wrong'},
                    {'ip': '192.168.52.128', 'port': 32769, 'session_id': 'correct'}]
        endpoint = mcp_tools.interconnection_snapshot(graph, sessions)['links'][0]['source']
        self.assertEqual((endpoint['telnet_ip'], endpoint['session_id']), ('192.168.52.128', 'correct'))

    def test_eve_config_and_capture_config_are_local_only(self):
        client = app.app.test_client()
        for route in ('/api/eve/config', '/api/eve/capture/config'):
            response = client.post(route, json={}, environ_overrides={'REMOTE_ADDR': '192.0.2.1'})
            self.assertEqual(response.status_code, 403)
            response = client.post(route, json={}, headers={'Origin': 'https://other.test'})
            self.assertEqual(response.status_code, 403)


class RemoteDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_uses_server_endpoint_and_non_huawei_verify_sends_no_commands(self):
        graph = topology()
        graph['device_inventory']['1']['model'] = 'vios'
        graph['device_inventory']['2']['model'] = 'vios'
        runtime = copy.deepcopy(graph)
        for d in runtime['device_inventory'].values():
            if d['category'] != '网络': d.update(console_host='192.168.52.128', com_port=str(32768 + int(d['id'])))
        sessions, calls = {}, []

        class FakeClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def call_tool(self, name, args, **kw):
                calls.append((name, args))
                if name != 'connect_device': raise AssertionError('unexpected Huawei command')
                sessions[(args['ip'], args['port'])] = {'ip': args['ip'], 'session_id': str(args['port'])}
                return SimpleNamespace(content=[SimpleNamespace(text='连接成功')])

        with patch('eve_service.eve_service.bind', return_value=runtime), patch.object(topology_resolver, 'Client', FakeClient), \
                patch.object(topology_resolver.session_manager, 'get_session_by_port', side_effect=lambda ip, port: sessions.get((ip, port))):
            result = await topology_resolver.discover(graph, connect=True, verify=True)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(args['ip'] == '192.168.52.128' for _, args in calls))
        self.assertEqual(result['verified'], {})
        self.assertEqual(sum('非华为' in d['status'] for d in result['devices']), 2)


class BufferChannel:
    def __init__(self, data):
        self.buffer = io.BytesIO(data)
        self.closed = False

    def recv(self, length): return self.buffer.read(min(length, 7))
    def close(self): self.closed = True


def pcap(endian='<', nanos=False, frame=b'\xff' * 14, linktype=1):
    magic = b'\x4d\x3c\xb2\xa1' if nanos else b'\xd4\xc3\xb2\xa1'
    if endian == '>': magic = magic[::-1]
    return (magic + struct.pack(endian + 'HHiiII', 2, 4, 0, 0, 65535, linktype)
            + struct.pack(endian + 'IIII', 1700000000, 500000000 if nanos else 500000, len(frame), len(frame)) + frame)


class RemoteCaptureTests(unittest.TestCase):
    def test_pcap_all_byteorders_and_timestamp_resolutions_with_partial_reads(self):
        for endian in ('<', '>'):
            for nanos in (False, True):
                packet = next(eve_capture.pcap_packets(BufferChannel(pcap(endian, nanos))))
                self.assertEqual(packet, (1700000000.5, 14, b'\xff' * 14))

    def test_non_ethernet_invalid_and_truncated_pcap_are_rejected(self):
        for content in (pcap(linktype=113), b'invalid pcap stream'.ljust(24, b' ')):
            with self.assertRaises(ValueError): next(eve_capture.pcap_packets(BufferChannel(content)))
        with self.assertRaises(EOFError): next(eve_capture.pcap_packets(BufferChannel(pcap()[:-1])))
        content = pcap()[:24] + struct.pack('<IIII', 1, 1, 1000000, 1000000)
        with self.assertRaises(ValueError): next(eve_capture.pcap_packets(BufferChannel(content)))

    def test_timeout_during_partial_header_preserves_received_bytes(self):
        channel = BufferChannel(b'abcdef')
        original = channel.recv
        calls = 0
        def receive(size):
            nonlocal calls
            calls += 1
            if calls == 2: raise socket.timeout()
            return original(min(size, 2))
        channel.recv = receive
        self.assertEqual(eve_capture.read_exact(channel, 6), b'abcdef')

    def test_ssh_configuration_never_returns_password(self):
        ssh = eve_capture.EveSSH()
        with patch.object(ssh, 'interfaces', return_value=[{'id': 'vnet0', 'name': 'vnet0'}]):
            ssh.configure({'host': '192.168.52.128', 'username': 'root', 'password': 'secret'})
        self.assertNotIn('password', ssh.settings())
        self.assertNotIn('secret', json.dumps(ssh.settings()))

    def test_ssh_fingerprint_policy_rejects_unknown_hosts(self):
        import paramiko
        ssh = eve_capture.EveSSH()
        ssh.config = {'host': 'eve.test', 'port': 22, 'username': 'user', 'password': 'secret', 'fingerprint': ''}
        fake = MagicMock()
        def connect(**kwargs):
            policy = fake.set_missing_host_key_policy.call_args.args[0]
            policy.missing_host_key(fake, 'eve.test', SimpleNamespace(asbytes=lambda: b'key'))
        fake.connect.side_effect = connect
        with patch.object(paramiko, 'SSHClient', return_value=fake), self.assertRaisesRegex(ValueError, 'SHA256:'):
            ssh.connect()
        fake.close.assert_called_once()

    def test_duplicate_link_or_interface_mapping_is_rejected(self):
        monitor = eve_capture.EveTrafficMonitor()
        for mappings in ([{'link_id': '1:0', 'interface': 'vnet0'}] * 2,
                         [{'link_id': '1:0', 'interface': 'vnet0'}, {'link_id': '0:0', 'interface': 'vnet0'}],
                         [{'link_id': '9:0', 'interface': 'vnet0'}]):
            with patch.object(eve_capture.eve_ssh, 'interfaces', return_value=[{'id': 'vnet0'}]), self.assertRaises(ValueError):
                monitor.start(mappings, topology())

    def test_start_multiple_remote_captures_and_stop_closes_every_channel(self):
        monitor = eve_capture.EveTrafficMonitor()
        client = MagicMock()
        channels = [MagicMock(), MagicMock()]
        for channel in channels:
            channel.closed = False
            channel.exit_status_ready.return_value = False
        client.get_transport.return_value.open_session.side_effect = channels
        mappings = [{'link_id': '0:0', 'interface': 'vnet0'}, {'link_id': '1:0', 'interface': 'vnet1'}]
        with patch.object(eve_capture.eve_ssh, 'interfaces', return_value=[{'id': 'vnet0'}, {'id': 'vnet1'}]), \
                patch.object(eve_capture.eve_ssh, 'connect', return_value=client), patch.object(eve_capture.threading, 'Thread'):
            self.assertEqual(monitor.start(mappings, topology()), 2)
            self.assertTrue(monitor.status()['running'])
            self.assertEqual(set(monitor.rates), {'0:0', '1:0'})
            self.assertIn('tcpdump', channels[0].exec_command.call_args.args[0])
            monitor.stop()
        for channel in channels: channel.close.assert_called_once()
        client.close.assert_called_once()
        self.assertFalse(monitor.status()['running'])

    def test_partial_start_failure_closes_open_channels(self):
        monitor = eve_capture.EveTrafficMonitor()
        client, channel = MagicMock(), MagicMock()
        client.get_transport.return_value.open_session.side_effect = [channel, OSError('connection lost')]
        mappings = [{'link_id': '0:0', 'interface': 'vnet0'}, {'link_id': '1:0', 'interface': 'vnet1'}]
        with patch.object(eve_capture.eve_ssh, 'interfaces', return_value=[{'id': 'vnet0'}, {'id': 'vnet1'}]), \
                patch.object(eve_capture.eve_ssh, 'connect', return_value=client), self.assertRaises(ValueError):
            monitor.start(mappings, topology())
        channel.close.assert_called_once()
        client.close.assert_called_once()
        self.assertIsNone(monitor.process)

    def test_remote_packet_count_and_analysis_do_not_claim_direction(self):
        monitor = eve_capture.EveTrafficMonitor()
        group = MagicMock()
        channel = BufferChannel(pcap())
        monitor.process, monitor.generation = group, 1
        monitor.rates = {'1:0': {'pps': 0, 'bps': 0, 'endpoints': 0}, '0:0': {'pps': 0, 'bps': 0, 'endpoints': 0}}
        monitor._read_remote(group, channel, 1, '1:0', 'vnet0')
        packet = monitor.packet_snapshot()[0]
        self.assertEqual((packet['capture_source'], packet['capture_interface']), ('eve-ssh', 'vnet0'))
        self.assertIsNone(packet['sender_id'])
        self.assertEqual(packet['frame_length'], 14)
        self.assertNotIn('outer_source', packet)
        self.assertIn('0:0', monitor.rates)
        self.assertNotIn('1:0', monitor.rates)

    def test_api_dispatches_eve_capture_and_rejects_stale_mapping(self):
        client, graph = app.app.test_client(), topology()
        with patch.object(app, 'get_topo_data', return_value=graph), patch.object(app.eve_traffic_monitor, 'start', return_value=1) as start:
            stale = client.post('/api/traffic/start', json={'revision': app.topology_revision - 1, 'mappings': []})
            self.assertEqual(stale.status_code, 400)
            mappings = [{'link_id': '1:0', 'interface': 'vnet0'}]
            response = client.post('/api/traffic/start', json={'revision': app.topology_revision, 'mappings': mappings})
            self.assertEqual(response.status_code, 200)
            start.assert_called_once_with(mappings, graph)


if __name__ == '__main__':
    unittest.main()
