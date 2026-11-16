import unittest
import threading
from types import SimpleNamespace
from unittest.mock import patch

import app
import topology_resolver as resolver
import telnet_service
import lldp_service


INTERFACES = '''Interface                   PHY   Protocol InUti OutUti
GigabitEthernet0/0/0        up    up          0%     0%
GigabitEthernet0/0/1        up    up          0%     0%
'''


def observation(name, peer, local='GE0/0/0', remote='GE0/0/1'):
    return {'system_name': name, 'interfaces': resolver.parse_interfaces(INTERFACES),
            'neighbors': resolver.parse_neighbors(
                'Local Intf   Neighbor Dev  Neighbor Intf  Exptime(s)\n'
                f'{local} {peer} {remote} 120\n')}


def topology():
    return {'device_inventory': {'a': {'id': 'a', 'name': 'AR1'}, 'b': {'id': 'b', 'name': 'AR2'}},
            'connections': [{'source_id': 'a', 'target_id': 'b',
                             'interface_pairs': [{'src_port_index': 0, 'dst_port_index': 1}]}]}


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.observed = {'a': observation('AR1', 'AR2'),
                         'b': observation('AR2', 'AR1', 'GE0/0/1', 'GE0/0/0')}

    def test_mutual_unique_lldp_and_interface_alias(self):
        result = resolver.verified_links(topology(), self.observed)
        self.assertEqual(result['0:0']['source_interface'], 'GE0/0/0')
        self.assertEqual(result['0:0']['target_interface'], 'GE0/0/1')

    def test_ambiguous_names_missing_identity_and_parallel_links_are_not_verified(self):
        self.observed['b']['system_name'] = 'AR1'
        self.assertEqual(resolver.verified_links(topology(), self.observed), {})
        self.observed['b']['system_name'] = None
        self.assertEqual(resolver.verified_links(topology(), self.observed), {})
        self.observed['b']['system_name'] = 'AR2'
        graph = topology()
        graph['connections'][0]['interface_pairs'].append({'src_port_index': 1, 'dst_port_index': 0})
        self.assertEqual(resolver.verified_links(graph, self.observed), {})

    def test_nonreciprocal_or_unknown_interface_not_verified(self):
        self.observed['b']['neighbors'][0]['remote'] = 'GE0/0/1'
        self.assertEqual(resolver.verified_links(topology(), self.observed), {})
        self.observed['b'] = observation('AR2', 'AR1', '-', 'GE0/0/0')
        self.observed['a']['neighbors'][0]['remote'] = '-'
        self.assertEqual(resolver.verified_links(topology(), self.observed), {})

    def test_unrelated_missing_identity_does_not_block_verified_link(self):
        graph = topology()
        graph['device_inventory']['c'] = {'id': 'c', 'name': 'AR3'}
        self.assertEqual(resolver.verified_links(graph, self.observed)['0:0']['source_interface'], 'GE0/0/0')
        self.observed['b']['neighbors'][0]['remote'] = 'GE0/0/9'
        self.assertEqual(resolver.verified_links(graph, self.observed), {})

    def test_duplicate_topology_name_or_wrong_observed_name_is_not_verified(self):
        graph = topology()
        graph['device_inventory']['c'] = {'id': 'c', 'name': 'AR1'}
        self.assertEqual(resolver.verified_links(graph, self.observed), {})
        graph['device_inventory']['c']['name'] = 'AR3'
        graph['device_inventory']['a']['name'] = 'Different'
        self.assertEqual(resolver.verified_links(graph, self.observed), {})

    def test_skipped_ap_and_server_do_not_block_other_verified_links(self):
        graph = topology()
        graph['device_inventory']['ap'] = {'id': 'ap', 'model': 'AP2050'}
        graph['device_inventory']['server'] = {'id': 'server', 'model': 'Server'}
        self.assertIn('0:0', resolver.verified_links(graph, self.observed))
        self.assertIsNone(resolver.skipped_device_type({'model': 'AC6005'}))

    def test_incomplete_or_unsupported_outputs_rejected(self):
        command = 'display lldp neighbor brief'
        self.assertEqual(resolver.command_body(command + '\n---- More ----\n<Huawei>', command), '')
        self.assertEqual(resolver.command_body(command + '\nError: no LLDP\n<Huawei>', command), '')
        self.assertEqual(resolver.command_body(command + '\nLocal Intf', command), '')
        self.assertEqual(resolver.parse_neighbors('some unrelated output'), [])
        self.assertTrue(resolver.command_body(command + '\nno neighbors\n<Huawei>\n', command))

    def test_neighbor_brief_vrp_output(self):
        body = ('Local Intf   Neighbor Dev             Neighbor Intf             Exptime\n'
                'GE0/0/0      ISP                      GE0/0/0                   92\n'
                'GE0/0/1      Core_AR3                 GE0/0/0                   91\n')
        self.assertEqual(resolver.parse_neighbors(body), [
            {'local': 'GE0/0/0', 'neighbor': 'ISP', 'remote': 'GE0/0/0'},
            {'local': 'GE0/0/1', 'neighbor': 'Core_AR3', 'remote': 'GE0/0/0'}])

    def test_endpoint_exposes_only_current_topology_verification(self):
        graph = topology()
        for device_id, device in graph['device_inventory'].items():
            device.update(model='AR2240', position={'x': 0, 'y': 0})
        graph['summary'] = {}
        graph['connections'][0]['link_count'] = 1
        app.verified_topology = graph
        app.verified_topology_links = resolver.verified_links(graph, self.observed)
        try:
            with patch.object(app, 'get_topo_data', return_value=graph), patch.object(app, 'get_topo_filename', return_value='x.topo'):
                pair = app.app.test_client().get('/api/topo/graph').json['connections'][0]['interface_pairs'][0]
                self.assertEqual(pair['verified']['source_interface'], 'GE0/0/0')
            other = topology()
            for device_id, device in other['device_inventory'].items():
                device.update(name=device_id, model='AR2240', position={'x': 0, 'y': 0})
            other['summary'] = {}
            other['connections'][0]['link_count'] = 1
            with patch.object(app, 'get_topo_data', return_value=other), patch.object(app, 'get_topo_filename', return_value='y.topo'):
                pair = app.app.test_client().get('/api/topo/graph').json['connections'][0]['interface_pairs'][0]
                self.assertIsNone(pair['verified'])
        finally:
            app.verified_topology, app.verified_topology_links = None, {}


class ManualInterfacesTests(unittest.TestCase):
    def test_add_display_and_clear_manual_interfaces(self):
        graph = topology()
        for device in graph['device_inventory'].values():
            device.update(model='AR2240', position={'x': 0, 'y': 0})
        graph['summary'] = {}
        graph['connections'][0]['link_count'] = 1
        payload = {'revision': app.topology_revision, 'link_index': 0, 'pair_index': 0, 'source_id': 'a', 'target_id': 'b',
                   'source_interface': 'GE0/0/0', 'target_interface': ''}
        client = app.app.test_client()
        try:
            with patch.object(app, 'get_topo_data', return_value=graph), patch.object(app, 'get_topo_filename', return_value='x.topo'):
                self.assertEqual(client.post('/api/topo/manual-interfaces', json=payload).status_code, 200)
                pair = client.get('/api/topo/graph').json['connections'][0]['interface_pairs'][0]
                self.assertEqual(pair['manual'], {'source_interface': 'GE0/0/0', 'target_interface': ''})
                self.assertIsNone(pair['verified'])
                self.assertEqual(client.post('/api/topo/manual-interfaces', json={**payload, 'target_id': 'wrong'}).status_code, 409)
                self.assertEqual(client.post('/api/topo/manual-interfaces', json={**payload, 'revision': app.topology_revision - 1}).status_code, 409)
                self.assertEqual(client.post('/api/topo/manual-interfaces', json={**payload, 'source_interface': '\n', 'target_interface': ''}).status_code, 400)
                self.assertEqual(client.delete('/api/topo/manual-interfaces', json=payload).status_code, 200)
                self.assertIsNone(client.get('/api/topo/graph').json['connections'][0]['interface_pairs'][0]['manual'])
        finally:
            app.manual_topology, app.manual_topology_links = None, {}


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_pc_skipped_and_rename_failure_only_skips_that_device(self):
        graph = topology()
        graph['device_inventory']['a']['com_port'] = 2000
        graph['device_inventory']['b']['com_port'] = 2001
        graph['device_inventory']['pc'] = {'id': 'pc', 'name': 'PC1', 'model': 'PC'}
        graph['device_inventory']['ap'] = {'id': 'ap', 'name': 'AP1', 'model': 'AP2050', 'com_port': 2000}
        graph['device_inventory']['server'] = {'id': 'server', 'name': 'Server1', 'model': 'Server', 'com_port': 2001}
        calls = []
        class FakeClient:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def call_tool(self, name, args, **kwargs):
                calls.append((name, args['port']))
                if name == 'configure_topology_device':
                    self_test.assertEqual(len([c for c in calls if c[0] == 'rename_topology_device']), 2)
                    text = '{"ok":false,"message":"模拟接口配置失败"}'
                elif name == 'rename_topology_device':
                    text = '{"ok":false,"message":"回显不完整"}' if args['port'] == 2001 else '{"ok":true}'
                else:
                    raise AssertionError('unexpected tool')
                return SimpleNamespace(content=[SimpleNamespace(text=text)])
        self_test = self
        with patch.object(resolver, 'Client', FakeClient), patch.object(resolver.asyncio, 'sleep') as wait, patch.object(
                resolver.session_manager, 'get_session_by_port', side_effect=lambda ip, port: {'session_id': str(port), 'ip': ip}):
            result = await resolver.discover(graph, connect=False, verify=True)
        wait.assert_awaited_once_with(5)
        self.assertEqual([port for name, port in calls if name == 'configure_topology_device'], [2000])
        statuses = {device['id']: device['status'] for device in result['devices']}
        self.assertIn('回显不完整', statuses['b'])
        self.assertIn('模拟接口配置失败', statuses['a'])
        self.assertIn('PC', statuses['pc'])
        self.assertIn('AP 设备已跳过', statuses['ap'])
        self.assertIn('Server 设备已跳过', statuses['server'])

    async def test_connect_only_and_verify_only_are_separate(self):
        graph = topology()
        graph['device_inventory']['a']['com_port'] = 2000
        graph['device_inventory']['b']['com_port'] = 2001
        sessions, calls, progress = {}, [], []

        class FakeClient:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def call_tool(self, name, args, **kwargs):
                calls.append(name)
                if name != 'connect_device':
                    raise AssertionError('connect-only must not configure or query devices')
                sessions[args['port']] = {'session_id': str(args['port']), 'ip': args['ip']}
                return SimpleNamespace(content=[SimpleNamespace(text='连接成功')])

        with patch.object(resolver, 'Client', FakeClient), patch.object(
                resolver.session_manager, 'get_session_by_port', side_effect=lambda ip, port: sessions.get(port)):
            result = await resolver.discover(graph, connect=True, verify=False, progress=lambda n, s: progress.append(n))
            self.assertEqual(calls, ['connect_device', 'connect_device'])
            self.assertEqual(len(result['devices']), 2)
            self.assertEqual(progress, [37, 75])
            sessions.clear()
            calls.clear()
            result = await resolver.discover(graph, connect=False, verify=True)
            self.assertEqual(calls, [])
            self.assertTrue(all('未连接' in device['status'] for device in result['devices']))

    async def test_neighbor_discovery_retries_after_global_wait(self):
        graph = topology()
        for device, port in zip(graph['device_inventory'].values(), (2000, 2001)):
            device['com_port'] = port
        counts = {2000: 0, 2001: 0}

        class FakeClient:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def call_tool(self, name, args, **kwargs):
                port = args['port']
                if name in ('rename_topology_device', 'configure_topology_device'):
                    text = '{"ok":true}'
                else:
                    command = args['command']
                    if command == 'display interface brief': body = INTERFACES
                    elif command == 'display lldp local-information': body = 'System name : ' + ('AR1' if port == 2000 else 'AR2')
                    else:
                        counts[port] += 1
                        body = 'Local Intf Neighbor Dev Neighbor Intf Exptime(s)\n'
                        if counts[port] > 1:
                            body += 'GE0/0/0 AR2 GE0/0/1 120' if port == 2000 else 'GE0/0/1 AR1 GE0/0/0 120'
                    text = command + '\n' + body + '\n<Huawei>'
                return SimpleNamespace(content=[SimpleNamespace(text=text)])

        waits = []
        async def skip_wait(delay):
            waits.append(delay)
        with patch.object(resolver, 'Client', FakeClient), patch.object(resolver.asyncio, 'sleep', side_effect=skip_wait), \
                patch.object(resolver.session_manager, 'get_session_by_port', side_effect=lambda ip, port: {'session_id': str(port), 'ip': ip}):
            result = await resolver.discover(graph)
        self.assertIn('0:0', result['verified'])
        self.assertEqual(waits, [5, 30])
        self.assertEqual(counts, {2000: 2, 2001: 2})

    async def test_mcp_connections_and_queries_reuse_existing_session(self):
        graph = topology()
        graph['device_inventory']['a'].update(name='AR1', com_port='02000')
        graph['device_inventory']['b'].update(name='AR2', com_port='2001')
        sessions = {2000: {'session_id': 'old-session'}}
        calls = []
        updates = []

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def call_tool(self, name, args, **kwargs):
                calls.append((name, args))
                port = args['port']
                if name == 'connect_device':
                    sessions[port] = {'session_id': 'new-session'}
                    text = '连接成功！会话 ID: new-session'
                elif name == 'configure_topology_device' and port == 2000:
                    text = '{"ok": false, "query_safe": true, "message": "部分接口失败"}'
                elif name in ('rename_topology_device', 'configure_topology_device'):
                    text = '{"ok": true, "message": "已开启"}'
                else:
                    command = args['command']
                    if command == 'display interface brief':
                        body = INTERFACES
                    elif command == 'display lldp local-information':
                        body = 'System name : ' + ('AR1' if port == 2000 else 'AR2')
                    else:
                        body = ('Local Intf Neighbor Dev Neighbor Intf Exptime(s)\n' +
                                ('GE0/0/0 AR2 GE0/0/1 120' if port == 2000 else 'GE0/0/1 AR1 GE0/0/0 120'))
                    text = command + '\n' + body + '\n<Huawei>\n'
                return SimpleNamespace(content=[SimpleNamespace(text=text)])

        with patch.object(resolver, 'Client', FakeClient), patch.object(resolver.asyncio, 'sleep') as wait, patch.object(
                resolver.session_manager, 'get_session_by_port', side_effect=lambda ip, port: sessions.get(port)):
            result = await resolver.discover(graph, on_update=lambda links: updates.append(dict(links)))
        wait.assert_awaited_once_with(5)
        self.assertEqual([name for name, _ in calls].count('connect_device'), 1)
        self.assertEqual([name for name, _ in calls].count('configure_topology_device'), 2)
        names_in_order = [name for name, _ in calls]
        self.assertLess(max(i for i, name in enumerate(names_in_order) if name == 'rename_topology_device'),
                        min(i for i, name in enumerate(names_in_order) if name == 'configure_topology_device'))
        self.assertEqual([name for name, _ in calls].count('send_command'), 2)
        self.assertTrue(all(args['command'] == 'display lldp neighbor brief' for name, args in calls if name == 'send_command'))
        self.assertEqual(result['verified']['0:0']['source_interface'], 'GE0/0/0')
        self.assertTrue(any('0:0' in update for update in updates))
        self.assertTrue(any('部分接口失败' in device['status'] for device in result['devices']))
        self.assertEqual({device['session_id'] for device in result['devices']}, {'old-session', 'new-session'})

    async def test_duplicate_ports_are_skipped(self):
        graph = topology()
        graph['device_inventory']['a'].update(name='AR1', com_port='02000')
        graph['device_inventory']['b'].update(name='AR2', com_port='2000')

        class FakeClient:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def call_tool(self, *args, **kwargs): raise AssertionError('duplicate ports must not call tools')

        with patch.object(resolver, 'Client', FakeClient):
            result = await resolver.discover(graph)
        self.assertEqual(result['verified'], {})
        self.assertTrue(all('重复' in device['status'] for device in result['devices']))


class TelnetConcurrencyTests(unittest.TestCase):
    def test_complete_command_waits_for_echo_and_final_prompt(self):
        writes = []
        session = {'client': SimpleNamespace(write=writes.append), 'status': 'connected', 'command_lock': threading.RLock()}
        chunks = iter(['<old>\n', 'display version\r\n', 'VRP software version\r\n', '<Core_AR1>'])
        with patch.object(telnet_service.session_manager, 'get_telnet_session', return_value=session), \
                patch.object(telnet_service.session_manager, 'pop_output', side_effect=lambda sid: next(chunks)), \
                patch.object(telnet_service.session_manager, 'append_output') as restore, \
                patch.object(telnet_service.time, 'sleep'):
            output = telnet_service.send_command_complete('s', 'display version')
        self.assertEqual(writes, [b'display version\n'])
        self.assertIn('VRP software version', output)
        self.assertIn('<Core_AR1>', output)
        restore.assert_called_once_with('s', '<old>\n')

    def test_complete_command_does_not_accept_old_prompt_alone(self):
        session = {'client': SimpleNamespace(write=lambda data: None), 'status': 'connected', 'command_lock': threading.RLock()}
        with patch.object(telnet_service.session_manager, 'get_telnet_session', return_value=session), \
                patch.object(telnet_service.session_manager, 'pop_output', side_effect=['<old>', '<Core_AR1>', '']), \
                patch.object(telnet_service.session_manager, 'append_output'), \
                patch.object(telnet_service.time, 'sleep'), \
                patch.object(telnet_service.time, 'monotonic', side_effect=[0, 0.1, 0.2, 2]):
            output = telnet_service.send_command_complete('s', 'display version', timeout=1)
        self.assertIn('结果不完整', output)

    def test_web_poll_does_not_steal_command_output(self):
        entered, release = threading.Event(), threading.Event()
        session = {'client': SimpleNamespace(write=lambda data: entered.set()),
                   'status': 'connected', 'command_lock': threading.Lock()}
        outputs = []
        with patch.object(telnet_service.session_manager, 'get_telnet_session', return_value=session), \
                patch.object(telnet_service.session_manager, 'pop_output', return_value='command output') as pop, \
                patch.object(telnet_service.time, 'sleep', side_effect=lambda delay: release.wait(2)):
            worker = threading.Thread(target=lambda: outputs.append(telnet_service.send_command('s', 'display interface brief')))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(telnet_service.read_output('s'), ('', True))
                pop.assert_not_called()
            finally:
                release.set()
                worker.join(2)
            self.assertEqual(outputs, ['command output'])


class LldpEnableTests(unittest.TestCase):
    def run_workflow(self, outputs):
        session = {'session_id': 's', 'command_lock': threading.RLock()}
        with patch.object(lldp_service.session_manager, 'get_session_by_port', return_value=session), \
                patch.object(lldp_service, 'send_command', side_effect=outputs) as send:
            result = lldp_service.enable_lldp('127.0.0.1', 2000)
        return result, [call.args[1] for call in send.call_args_list]

    def test_already_enabled_does_not_configure(self):
        check = lldp_service.LLDP_CHECK
        result, commands = self.run_workflow([check + '\nlldp enable\n<Huawei>'])
        self.assertTrue(result['ok'])
        self.assertEqual(commands, [check])

    def test_enable_and_verify(self):
        check = lldp_service.LLDP_CHECK
        result, commands = self.run_workflow([
            check + '\n<Huawei>',
            'system-view\n[Huawei]', 'lldp enable\n[Huawei]', 'return\n<Huawei>',
            check + '\nlldp enable\n<Huawei>'])
        self.assertTrue(result['ok'])
        self.assertEqual(commands, [check, 'system-view', 'lldp enable', 'return', check])

    def test_unknown_and_failed_configuration_do_not_report_success(self):
        check = lldp_service.LLDP_CHECK
        result, commands = self.run_workflow([check + '\npartial output'])
        self.assertFalse(result['ok'])
        self.assertEqual(len(commands), 1)
        result, commands = self.run_workflow([
            check + '\n<Huawei>',
            'system-view\n[Huawei]', 'lldp enable\nError: failed\n[Huawei]', 'return\n<Huawei>'])
        self.assertFalse(result['ok'])
        self.assertEqual(commands[-1], 'return')

    def test_enable_must_be_confirmed(self):
        check = lldp_service.LLDP_CHECK
        result, _ = self.run_workflow([
            check + '\n<Huawei>',
            'system-view\n[Huawei]', 'lldp enable\n[Huawei]', 'return\n<Huawei>',
            check + '\n<Huawei>'])
        self.assertFalse(result['ok'])

    def test_configure_enables_global_lldp_without_interface_query(self):
        session = {'session_id': 's', 'command_lock': threading.RLock()}
        with patch.object(lldp_service.session_manager, 'get_session_by_port', return_value=session), \
                patch.object(lldp_service, 'enable_lldp', return_value={'ok': True, 'message': '已开启'}), \
                patch.object(lldp_service, 'send_command') as send:
            result = lldp_service.configure_topology_device('127.0.0.1', 2000, 'AR1')
        self.assertTrue(result['ok'], result)
        self.assertIn('未检查接口级', result['message'])
        send.assert_not_called()

    def test_name_is_set_before_lldp_even_if_lldp_fails(self):
        session = {'session_id': 's', 'command_lock': threading.RLock()}
        commands = []
        outputs = iter(['\n<Huawei>', 'screen-length 0 temporary\n<Huawei>', 'system-view\n[Huawei]',
                        'sysname AR1\n[AR1]', 'return\n<AR1>'])
        def send(sid, command, **kwargs):
            commands.append(command)
            if command == 'screen-length 0 temporary':
                self.assertEqual(kwargs, {'wait_seconds': 0.5})
            return next(outputs)
        with patch.object(lldp_service.session_manager, 'get_session_by_port', return_value=session), \
                patch.object(lldp_service, 'send_command', side_effect=send):
            result = lldp_service.rename_topology_device('127.0.0.1', 2000, 'AR1')
        self.assertTrue(result['ok'])
        self.assertEqual(commands, ['', 'screen-length 0 temporary', 'system-view', 'sysname AR1', 'return'])

    def test_rename_ignores_pagination_command_output(self):
        session = {'session_id': 's', 'command_lock': threading.RLock()}
        with patch.object(lldp_service.session_manager, 'get_session_by_port', return_value=session), \
                patch.object(lldp_service, 'send_command', side_effect=['<Huawei>', '', 'system-view\n[Huawei]', 'sysname AR1\n[AR1]', 'return\n<AR1>']) as send:
            result = lldp_service.rename_topology_device('127.0.0.1', 2000, 'AR1')
        self.assertTrue(result['ok'])
        self.assertEqual([call.args[1] for call in send.call_args_list], ['', 'screen-length 0 temporary', 'system-view', 'sysname AR1', 'return'])
        self.assertEqual(send.call_args_list[1].kwargs, {'wait_seconds': 0.5})

    def test_reject_unsafe_device_name_before_commands(self):
        with patch.object(lldp_service, 'send_command') as send:
            result = lldp_service.rename_topology_device('127.0.0.1', 2000, 'AR1\nsave')
        self.assertFalse(result['ok'])
        send.assert_not_called()

    def test_rename_stops_if_empty_line_returns_no_prompt(self):
        session = {'session_id': 's', 'command_lock': threading.RLock()}
        with patch.object(lldp_service.session_manager, 'get_session_by_port', return_value=session), \
                patch.object(lldp_service, 'send_command', return_value='') as send:
            result = lldp_service.rename_topology_device('127.0.0.1', 2000, 'AR1')
        self.assertFalse(result['ok'])
        self.assertIn('未收到设备提示符', result['message'])
        send.assert_called_once_with('s', '')


if __name__ == '__main__':
    unittest.main()
