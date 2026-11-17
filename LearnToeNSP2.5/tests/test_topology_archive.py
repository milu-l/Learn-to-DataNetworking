import json
import unittest
from unittest.mock import patch

import app
from topology_archive import export_archive, import_archive


def graph():
    return {'device_inventory': {'a': {'id': 'a', 'name': 'AR1', 'model': 'AR', 'position': {}},
                                 'b': {'id': 'b', 'name': 'AR2', 'model': 'AR', 'position': {}}},
            'summary': {}, 'connections': [{'source_id': 'a', 'target_id': 'b', 'link_count': 1,
                                           'interface_pairs': [{'src_port_index': 0, 'dst_port_index': 1}]}]}


class ArchiveTests(unittest.TestCase):
    def test_round_trip_with_reversed_link_and_manual_override(self):
        topology = graph()
        verified = {'0:0': {'source_interface': 'GE0/0/0', 'target_interface': 'GE0/0/1'}}
        manual = {'0:0': {'source_interface': 'GE0/0/2', 'target_interface': ''}}
        archive = json.loads(json.dumps(export_archive(topology, verified, manual, {})))
        reverse = graph()
        reverse['connections'][0].update(source_id='b', target_id='a', interface_pairs=[{'src_port_index': 1, 'dst_port_index': 0}])
        m, v = import_archive(reverse, archive)
        self.assertEqual(m['0:0'], {'source_interface': '', 'target_interface': 'GE0/0/2'})
        self.assertEqual(v['0:0'], {'source_interface': 'GE0/0/1', 'target_interface': 'GE0/0/0'})

    def test_reject_other_topology_and_malformed_records(self):
        topology = graph()
        archive = json.loads(json.dumps(export_archive(topology, {'0:0': {'source_interface': 'GE0/0/0', 'target_interface': 'GE0/0/1'}}, {}, {})))
        other = graph()
        other['connections'][0]['interface_pairs'][0]['src_port_index'] = 9
        with self.assertRaisesRegex(ValueError, '不匹配'):
            import_archive(other, archive)
        archive['links'][0]['previously_verified'] = ['ok\ncommand', 'GE0/0/1']
        with self.assertRaises(ValueError):
            import_archive(topology, archive)

    def test_api_restores_as_historical_and_preserves_manual_on_verification(self):
        topology = graph()
        archive = json.loads(json.dumps(export_archive(topology, {'0:0': {'source_interface': 'GE0/0/0', 'target_interface': 'GE0/0/1'}}, {}, {})))
        client = app.app.test_client()
        try:
            with patch.object(app, 'get_topo_data', return_value=topology), patch.object(app, 'get_topo_filename', return_value='lab.topo'):
                response = client.post('/api/topo/interfaces-archive', json={'revision': app.topology_revision, 'archive': archive})
                self.assertEqual(response.status_code, 200)
                pair = client.get('/api/topo/graph').json['connections'][0]['interface_pairs'][0]
                self.assertEqual(pair['restored']['source_interface'], 'GE0/0/0')
                self.assertIsNone(pair['verified'])
                self.assertEqual(client.post('/api/topo/interfaces-archive', json={'revision': app.topology_revision - 1, 'archive': archive}).status_code, 409)
        finally:
            app.manual_topology, app.manual_topology_links, app.restored_topology_links = None, {}, {}


if __name__ == '__main__':
    unittest.main()
