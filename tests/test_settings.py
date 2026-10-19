import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gfi_prototype.settings import load_project_env


class SettingsTests(unittest.TestCase):
    def test_dotenv_loads_without_overriding_existing_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('# local config\nDEEPSEEK_API_KEY="test-key"\nUNUSED_VALUE=no\n', encoding='utf-8')
            with patch.dict(os.environ, {}, clear=True):
                load_project_env(root)
                self.assertEqual(os.environ['DEEPSEEK_API_KEY'], 'test-key')
                self.assertNotIn('UNUSED_VALUE', os.environ)
            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'external'}, clear=True):
                load_project_env(root)
                self.assertEqual(os.environ['DEEPSEEK_API_KEY'], 'external')

    def test_missing_dotenv_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            load_project_env(Path(directory))


if __name__ == '__main__':
    unittest.main()
