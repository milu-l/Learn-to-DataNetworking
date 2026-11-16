import unittest

from traffic_service import capture_lookup


class TrafficCoverageTests(unittest.TestCase):
    def test_partial_mapping_reports_one_sided_and_uncovered_links(self):
        links = [
            {'source_id': 'router1', 'target_id': 'router2', 'interface_pairs': [{'src_port_index': 0, 'dst_port_index': 1}]},
            {'source_id': 'router1', 'target_id': 'switch', 'interface_pairs': [{'src_port_index': 2, 'dst_port_index': 0}]},
            {'source_id': 'switch', 'target_id': 'pc', 'interface_pairs': [{'src_port_index': 1, 'dst_port_index': 0}]},
        ]
        lookup, coverage, total = capture_lookup(links, {'ROUTER1': 'MAC1', 'ROUTER2': 'MAC2'})
        self.assertEqual(total, 3)
        self.assertEqual(coverage, {'0:0': 2, '1:0': 1})
        self.assertEqual(lookup[('MAC1', 2)], ('1:0', 'router1'))
        self.assertEqual(len(lookup), 3)

    def test_conflicting_endpoints_are_rejected(self):
        links = [
            {'source_id': 'r', 'target_id': 'a', 'interface_pairs': [{'src_port_index': 0}]},
            {'source_id': 'r', 'target_id': 'b', 'interface_pairs': [{'src_port_index': 0}]},
        ]
        with self.assertRaises(ValueError):
            capture_lookup(links, {'R': 'MAC'})

    def test_invalid_port_indices_are_not_mapped(self):
        links = [{'source_id': 'r', 'target_id': 'b',
                  'interface_pairs': [{'src_port_index': None, 'dst_port_index': 100}]}]
        lookup, coverage, total = capture_lookup(links, {'R': 'MAC', 'B': 'MAC2'})
        self.assertEqual((lookup, coverage, total), ({}, {}, 1))


if __name__ == '__main__':
    unittest.main()
