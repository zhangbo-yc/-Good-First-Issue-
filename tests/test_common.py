import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gfi_prototype.common import atomic_json


class AtomicJsonTests(unittest.TestCase):
    def test_transient_windows_replace_lock_is_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'cache.json'
            target.write_text('{"old":true}', encoding='utf-8')
            replace = Path.replace
            attempts = []
            def sometimes_locked(source, destination):
                attempts.append(source)
                if len(attempts) == 1:
                    self.assertEqual(json.loads(target.read_text()), {'old': True})
                    raise PermissionError('Transient file sharing lock')
                return replace(source, destination)
            with patch.object(Path, 'replace', autospec=True, side_effect=sometimes_locked):
                atomic_json(target, {'new': True})
            self.assertEqual(json.loads(target.read_text()), {'new': True})
            self.assertEqual(len(attempts), 2)

    def test_failed_atomic_update_preserves_previous_cache_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'cache.json'
            target.write_text('{"old":true}', encoding='utf-8')
            with patch.object(Path, 'replace', side_effect=PermissionError('locked')):
                with self.assertRaises(PermissionError):
                    atomic_json(target, {'new': True})
            self.assertEqual(json.loads(target.read_text()), {'old': True})
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
