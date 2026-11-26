import io
import unittest
from unittest.mock import patch

import app
import topo_service
import mcp_tools


UNL = '''<lab name="lab" version="1"><topology><nodes>
<node id="1" name="CE1" template="huaweice12800" left="10" top="20"><interface id="2" name="GE1/0/0" network_id="2"/></node>
<node id="2" name="CE2" template="huaweice12800" left="30" top="40"><interface id="2" name="GE1/0/1" network_id="2"/><interface id="3" name="GE1/0/2" network_id="1"/></node>
</nodes><networks><network id="1" type="pnet1" name="外网" left="50" top="60" visibility="1"/><network id="2" type="bridge" visibility="0"/></networks></topology></lab>'''


class EveTopologyTests(unittest.TestCase):
    def test_unl_upload_and_graph(self):
        client = app.app.test_client()
        try:
            with patch.object(app.traffic_monitor, 'stop'), patch.object(app.traffic_monitor, 'clear_packets'):
                response = client.post('/api/topo/upload', data={'file': (io.BytesIO(UNL.encode()), 'SNMP.unl')}, content_type='multipart/form-data')
                self.assertEqual(response.status_code, 200, response.json)
                graph = client.get('/api/topo/graph').json
                self.assertEqual(graph['format'], 'eve-ng')
                self.assertEqual(len(graph['devices']), 3)
                self.assertEqual(len(graph['connections']), 2)
                pair = graph['connections'][1]['interface_pairs'][0]
                self.assertEqual(pair['declared'], {'source_interface': 'GE1/0/0', 'target_interface': 'GE1/0/1'})
                self.assertIsNone(pair['verified'])
                self.assertEqual(client.post('/api/topo/connect').status_code, 400)
                snapshot = mcp_tools.interconnection_snapshot(graph, [])
                endpoint = snapshot['links'][1]['source']
                self.assertIn('未经验证', endpoint['interface_source'])
                self.assertIsNone(endpoint['telnet_port'])
        finally:
            topo_service.clear_topo_data()

    def test_shared_bridge_is_a_network_node_not_fake_direct_links(self):
        content = UNL.replace('</nodes>', '<node id="3" name="CE3"><interface id="1" name="eth1" network_id="2"/></node></nodes>')
        data = topo_service.parse_topology_from_content(content)
        self.assertIn('eve-network:2', data['device_inventory'])
        bridge_edges = [link for link in data['connections'] if link['target_id'] == 'eve-network:2']
        self.assertEqual(len(bridge_edges), 3)

    def test_unknown_xml_rejected(self):
        with self.assertRaises(RuntimeError):
            topo_service.parse_topology_from_content('<unrelated/>')

    def test_unconnected_interface_and_visible_unattached_network(self):
        content = UNL.replace('</node>', '<interface id="8" name="GE1/0/8" network_id="0"/></node>', 1)
        content = content.replace('</networks>', '<network id="9" name="external" type="pnet9"/></networks>')
        data = topo_service.parse_topology_from_content(content)
        self.assertIn('eve-network:9', data['device_inventory'])
        self.assertEqual(data['device_inventory']['1']['port_name_map'][8], 'GE1/0/8')
        self.assertEqual(len(data['connections']), 2)


if __name__ == '__main__':
    unittest.main()
