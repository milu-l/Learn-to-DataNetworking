import io
import json
import unittest
from unittest.mock import patch

import chat_service
import mcp_tools


def graph(manual='GE0/0/0'):
    return {'loaded': True, 'filename': 'lab.topo', 'revision': 3,
            'devices': [{'id': 'a', 'name': 'AR1', 'model': 'AR', 'com_port': '2000'},
                        {'id': 'b', 'name': 'AR2', 'model': 'AR', 'com_port': '2001'}],
            'connections': [{'source_id': 'a', 'target_id': 'b', 'interface_pairs': [
                {'line_type': 'Copper', 'manual': {'source_interface': manual, 'target_interface': ''},
                 'verified': {'source_interface': 'GigabitEthernet0/0/0', 'target_interface': 'GE0/0/1'}}]}]}


class FakeMCP:
    def __init__(self): self.functions = {}
    def tool(self):
        def register(fn):
            self.functions[fn.__name__] = fn
            return fn
        return register


class InterconnectionContextTests(unittest.TestCase):
    def test_manual_and_verified_sources_and_session_are_distinct(self):
        data = mcp_tools.interconnection_snapshot(graph(), [{'ip': '127.0.0.1', 'port': 2000, 'session_id': 's'}], 'ar1')
        source, target = data['links'][0]['source'], data['links'][0]['target']
        self.assertEqual(source['interface'], 'GE0/0/0')
        self.assertEqual(source['interface_source'], '手动填写，未经 LLDP 验证')
        self.assertEqual(source['lldp_verified_interface'], 'GigabitEthernet0/0/0')
        self.assertFalse(source['interface_conflict'])
        self.assertEqual(source['session_id'], 's')
        self.assertEqual(target['interface_source'], '双向 LLDP 已验证')
        changed = mcp_tools.interconnection_snapshot(graph('GE0/0/9'), [], 'AR2')
        self.assertTrue(changed['links'][0]['source']['interface_conflict'])
        self.assertEqual(mcp_tools.interconnection_snapshot(graph(), [], 'other')['links'], [])

    def test_tool_reads_latest_graph_each_time_and_handles_missing_topology(self):
        server = FakeMCP()
        mcp_tools.register_tools(server)
        tool = server.functions['get_topology_links']
        responses = [graph('GE0/0/0'), graph('GE0/0/9'), {'loaded': False}]
        with patch.object(mcp_tools, 'urlopen', side_effect=lambda *args, **kwargs: io.BytesIO(json.dumps(responses.pop(0)).encode())), \
                patch.object(mcp_tools.session_manager, 'list_sessions', return_value=[]):
            first = json.loads(tool('AR1'))
            second = json.loads(tool('AR1'))
            self.assertEqual(first['links'][0]['source']['interface'], 'GE0/0/0')
            self.assertEqual(second['links'][0]['source']['interface'], 'GE0/0/9')
            self.assertIn('尚未上传', tool())
        self.assertIn('get_topology_links', chat_service.READ_ONLY)

    def test_tool_paginates_and_validates_offset(self):
        server = FakeMCP()
        mcp_tools.register_tools(server)
        topology = graph()
        topology['connections'] *= 12
        with patch.object(mcp_tools, 'urlopen', side_effect=lambda *args, **kwargs: io.BytesIO(json.dumps(topology).encode())), \
                patch.object(mcp_tools.session_manager, 'list_sessions', return_value=[]):
            tool = server.functions['get_topology_links']
            page = json.loads(tool())
            self.assertEqual((page['total'], len(page['links']), page['next_offset']), (12, 10, 10))
            last = json.loads(tool(offset=10))
            self.assertEqual((len(last['links']), last['next_offset']), (2, None))
            self.assertIn('分页参数无效', tool(offset=-1))

    def test_restored_interface_is_identified_as_historical(self):
        topology = graph()
        pair = topology['connections'][0]['interface_pairs'][0]
        pair['manual'] = None
        pair['verified'] = None
        pair['restored'] = {'source_interface': 'GE0/0/0', 'target_interface': 'GE0/0/1'}
        endpoint = mcp_tools.interconnection_snapshot(topology, [])['links'][0]['source']
        self.assertEqual(endpoint['interface'], 'GE0/0/0')
        self.assertIn('当前未重新验证', endpoint['interface_source'])
        self.assertIsNone(endpoint['lldp_verified_interface'])


if __name__ == '__main__':
    unittest.main()
