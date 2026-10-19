import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import run


class ScriptLauncherTests(unittest.TestCase):
    def test_dispatch_from_another_directory_restores_process_state(self):
        original_argv = sys.argv
        original_path = sys.path[:]
        original_cwd = Path.cwd()
        seen = {}
        def invoke(path, run_name):
            seen.update(path=Path(path), name=run_name, cwd=Path.cwd(), argv=sys.argv[:])
            raise SystemExit(0)
        with tempfile.TemporaryDirectory() as folder:
            os.chdir(folder)
            try:
                with patch.object(run.runpy, 'run_path', side_effect=invoke):
                    with self.assertRaises(SystemExit):
                        run.main(['collect', '--', '--help'])
                self.assertEqual(Path.cwd(), Path(folder).resolve())
            finally:
                os.chdir(original_cwd)
        self.assertEqual(seen['path'], run.ROOT / 'collect_gfi.py')
        self.assertEqual(seen['cwd'], run.ROOT)
        self.assertEqual(seen['argv'], [str(run.ROOT / 'collect_gfi.py'), '--help'])
        self.assertEqual(seen['name'], '__main__')
        self.assertIs(sys.argv, original_argv)
        self.assertEqual(sys.path, original_path)
