import unittest
from unittest.mock import patch

import collect_ray


class RayCollectorTests(unittest.TestCase):
    def test_ray_entry_routes_only_ray_and_collects_all_states(self):
        with patch.object(collect_ray.core, 'main', return_value=0) as core_main:
            self.assertEqual(collect_ray.main(['--prompt-tokens', '1']), 0)
        call = core_main.call_args
        self.assertEqual(call.args[0], ['--prompt-tokens', '1'])
        self.assertEqual(call.kwargs['projects'], ({'repo': 'ray-project/ray'},))
        self.assertEqual(call.kwargs['default_state'], 'all')
        self.assertIs(call.kwargs['project_runner'], collect_ray.collect_and_audit)
        self.assertEqual(call.kwargs['summary_name'], 'collection_summary_ray.json')


if __name__ == '__main__':
    unittest.main()
