import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import research


class ResearchCliTests(unittest.TestCase):
    def test_env_model_is_loaded_before_cache_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('DEEPSEEK_API_KEY=test\nDEEPSEEK_MODEL=deepseek-custom\n')
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(research.configured_model(root), 'deepseek-custom')

    def test_review_paths_change_when_result_inventory_changes(self):
        output = Path('experiment')
        first = research.review_paths(output, [{'task_id': 'a/b#1', 'variant': 'A', 'cache_key': 'one'}], [])
        second = research.review_paths(output, [{'task_id': 'a/b#1', 'variant': 'A', 'cache_key': 'one'},
                                               {'task_id': 'a/b#1', 'variant': 'B', 'cache_key': 'two'}], [])
        self.assertNotEqual(first['analysis'], second['analysis'])
        self.assertTrue(first['analysis'].name.startswith('review_'))


if __name__ == '__main__':
    unittest.main()
