import tempfile
import unittest
from pathlib import Path
from gfi_prototype.storage import read_json, read_json_or, read_jsonl

class StorageTests(unittest.TestCase):
    def test_strict_and_optional_reads(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'record.json'
            self.assertIsNone(read_json_or(path))
            with self.assertRaises(OSError):
                read_json(path)
            path.write_text('{', encoding='utf-8')
            self.assertEqual(read_json_or(path, []), [])
            with self.assertRaises(ValueError):
                read_json(path)
            path.write_text('{"内容": 1}', encoding='utf-8')
            self.assertEqual(read_json(path), {'内容': 1})

    def test_json_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'records.jsonl'
            self.assertEqual(read_jsonl(path), [])
            path.write_text('{"id": 1}\n\n{"id": 2}\n', encoding='utf-8')
            self.assertEqual(read_jsonl(path), [{'id': 1}, {'id': 2}])
