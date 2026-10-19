import json
import tempfile
import unittest
from pathlib import Path

from gfi_prototype.newcomer_content import build_content, validate_items


class NewcomerContentTests(unittest.TestCase):
    def rows(self):
        return [{'task_id': f'a/b#{i}', 'repo': 'a/b', 'number': i, 'title': f'Fix widget {i}',
                 'body': f'The widget produces wrong output {i}.', 'kind': 'task'} for i in (1, 2)]

    def localized(self, records):
        return [{'task_id': row['task_id'], 'title_zh': '修复部件输出结果不一致的问题',
                 'description': '这个任务关注部件输出结果与预期不一致的问题。需要结合提供的使用示例检查当前行为，明确输出应满足的条件，再调整对应实现并检查相关测试。'} for row in records]

    def test_validation_requires_exact_identity_chinese_and_open_assumption(self):
        rows = self.rows()
        self.assertEqual(len(validate_items(self.localized(rows), rows)), 2)
        bad = self.localized(rows)
        bad[0]['task_id'] = 'other/repo#3'
        with self.assertRaises(ValueError):
            validate_items(bad, rows)
        bad = self.localized(rows)
        bad[0]['description'] += '请查看 PR #123 的评论。'
        with self.assertRaises(ValueError):
            validate_items(bad, rows)
        bad = self.localized(rows)
        bad[0]['description'] = 'This is an English description that should not be shown.'
        with self.assertRaises(ValueError):
            validate_items(bad, rows)
        bad = self.localized(rows)
        bad[0]['description'] += '这个问题已经修复。'
        with self.assertRaises(ValueError):
            validate_items(bad, rows)

    def test_incremental_cache_and_resume_use_only_deepseek_results(self):
        calls = []
        test = self
        class Client:
            def generate(self, records):
                calls.append(records)
                return {'items': test.localized(records), 'model': 'deepseek-flash',
                        'usage': {'prompt_tokens': 30, 'completion_tokens': 20}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = build_content(root, self.rows(), Client(), workers=1, batch_size=1)
            self.assertEqual(result['completed'], 2)
            self.assertEqual(result['failed'], [])
            entries = json.loads((root / 'data/a__b/newcomer_summaries.json').read_text(encoding='utf-8'))
            self.assertTrue(all(entry['origin'] == 'deepseek_flash_newcomer_summary' for entry in entries.values()))
            calls.clear()
            result = build_content(root, self.rows(), Client(), workers=1)
            self.assertEqual(result['cache_hits'], 2)
            self.assertEqual(calls, [])
            entries['a/b#1']['description'] += '请查看 PR #123。'
            (root / 'data/a__b/newcomer_summaries.json').write_text(json.dumps(entries), encoding='utf-8')
            build_content(root, self.rows(), Client(), workers=1)
            self.assertEqual(len(calls), 1)

    def test_failed_batch_does_not_erase_successful_content(self):
        test = self
        class Client:
            def generate(self, records):
                if records[0]['number'] == 2:
                    raise RuntimeError('secret must not appear in manifest')
                return {'items': test.localized(records), 'model': 'deepseek-flash', 'usage': {}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = build_content(root, self.rows(), Client(), workers=1, batch_size=1)
            self.assertEqual(result['completed'], 1)
            self.assertEqual(result['failed'][0]['error_type'], 'RuntimeError')
            self.assertNotIn('secret must not appear', json.dumps(result))
            entries = json.loads((root / 'data/a__b/newcomer_summaries.json').read_text(encoding='utf-8'))
            self.assertIn('a/b#1', entries)


if __name__ == '__main__':
    unittest.main()
