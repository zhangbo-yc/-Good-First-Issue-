import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gfi_prototype.common import sha256_text
from gfi_prototype.portal import Portal


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'data/a__b'
        self.folder.mkdir(parents=True)
        (self.folder / 'processed').mkdir()
        (self.folder / 'repository.json').write_text(json.dumps({'full_name': 'a/b', 'name': 'B',
            'description': 'Example', 'language': 'Python', 'stargazers_count': 123,
            'html_url': 'https://github.com/a/b'}), encoding='utf-8')
        raw, triage, summaries = [], [], {}
        for number, kind in [(1, 'task'), (2, 'task'), (3, 'umbrella')]:
            issue = {'number': number, 'title': f'Widget {number}', 'body': f'Fix widget {number}',
                     'state': 'closed', 'html_url': f'https://github.com/a/b/issues/{number}',
                     'labels': [{'name': 'bug'}], 'comments': 45}
            raw.append({'repo': 'a/b', 'complete': True, 'issue': issue, 'comments': [{'body': 'private secondary details'}]})
            triage.append({'repo': 'a/b', 'number': number, 'record_type': kind, 'task_types': ['bugfix']})
            summaries[f'a/b#{number}'] = {'title_zh': f'修复部件的显示问题{number}',
                'description': f'这是中文任务说明{number}，修复部件显示结果与预期不一致的问题。',
                'body_sha256': sha256_text(issue['body']), 'title_sha256': sha256_text(issue['title']),
                'origin': 'deepseek_flash_newcomer_summary'}
        for path, rows in [('issues.jsonl', raw), ('processed/triage.jsonl', triage)]:
            (self.folder / path).write_text(''.join(json.dumps(x) + '\n' for x in rows), encoding='utf-8')
        (self.folder / 'newcomer_summaries.json').write_text(json.dumps(summaries), encoding='utf-8')
        self.portal = Portal(self.root)

    def test_catalog_covers_all_records_and_separates_guides(self):
        result = self.portal.catalog()
        self.assertEqual(result['totals']['records'], 3)
        self.assertEqual(result['totals']['tasks'], 2)
        self.assertEqual(result['totals']['guides'], 1)
        self.assertEqual(result['totals']['chinese_summaries'], 3)
        self.assertTrue(result['projects'][0]['previews'])

    def test_closed_snapshot_tasks_are_presented_as_open_without_raw_fields(self):
        result = self.portal.issues('a/b')
        self.assertEqual(result['total'], 2)
        for row in result['items']:
            self.assertEqual(row['state'], 'open')
            self.assertNotIn('state_at_snapshot', row)
            self.assertNotIn('comments', row)
            self.assertIn('中文任务说明', row['description'])

    def test_chinese_search_pagination_and_guide_filter(self):
        result = self.portal.issues('a/b', query='显示问题', page_size=1)
        self.assertEqual(result['total'], 2)
        self.assertEqual(len(result['items']), 1)
        second = self.portal.issues('a/b', query='显示问题', page=2, page_size=1)
        self.assertNotEqual(second['items'][0]['task_id'], result['items'][0]['task_id'])
        self.assertEqual(self.portal.issues('a/b', kind='guide')['total'], 1)

    def test_detail_excludes_raw_body_comments_and_closed_state(self):
        detail = self.portal.issue('a/b', 1)
        self.assertEqual(detail['state'], 'open')
        text = json.dumps(detail, ensure_ascii=False)
        self.assertNotIn('Fix widget', text)
        self.assertNotIn('private secondary details', text)
        self.assertNotIn('comments', text)
        self.assertFalse(detail['has_analysis'])
        self.assertEqual(detail['recommendations'], [])
        self.assertIn('https://github.com/a/b/search?', detail['source_search_url'])

    def test_stale_chinese_summary_is_not_used(self):
        path = self.folder / 'newcomer_summaries.json'
        data = json.loads(path.read_text())
        data['a/b#1']['body_sha256'] = 'outdated'
        path.write_text(json.dumps(data))
        result = self.portal.issue('a/b', 1)
        self.assertFalse(result['localized'])
        self.assertNotIn('中文任务说明1', result['description'])

    def test_non_deepseek_batch_content_is_not_published(self):
        path = self.folder / 'newcomer_summaries.json'
        data = json.loads(path.read_text())
        data['a/b#1']['origin'] = 'codex_ai_chinese_summary'
        path.write_text(json.dumps(data))
        self.assertFalse(self.portal.issue('a/b', 1)['localized'])

    def test_missing_issue_and_invalid_pagination(self):
        with self.assertRaises(KeyError):
            self.portal.issue('a/b', 999)
        with self.assertRaises(ValueError):
            self.portal.issues('a/b', page=0)
        with self.assertRaises(ValueError):
            self.portal.issues('a/b', page_size=500)

    def test_guide_text_uses_readable_sources_and_omits_discussion_metadata(self):
        self.portal.profiles['a/b#1'] = {'goal': '修复部件行为', 'technology_tags': []}
        artifact = {'kind': 'model', 'analysis': {'task_goal': {'text': '修复部件行为'},
            'suitable_background': '熟悉Python类与测试；当前PR认领状态未知。',
            'newcomer_stage': {'stage': '具备开发基础', 'rationale': '需要阅读类的实现（I1、C2）。',
                              'entry_conditions': ['能阅读测试'], 'evidence_ids': ['I1']},
            'getting_started': ['先读I1，再对照E2了解实现。'],
            'unknowns': ['当前PR认领状态未知，评论尚待核实。', '是否需要增加空输入测试？'],
            'related_files': [{'path': 'pkg/widget.py', 'evidence_ids': ['E2']}]},
            'packet': {'evidence': [{'id': 'E2', 'path': 'pkg/widget.py',
                'url': 'https://github.com/a/b/blob/abc/pkg/widget.py#L1-L10'}]}}
        with patch('gfi_prototype.portal.artifacts.analysis_artifact', return_value=artifact):
            detail = self.portal.issue('a/b', 1)
        text = json.dumps(detail, ensure_ascii=False)
        for internal in ('I1', 'C2', 'E2', 'evidence_ids', 'PR认领', '评论'):
            self.assertNotIn(internal, text)
        self.assertIn('widget.py', detail['steps'][0])
        self.assertEqual(detail['unknowns'], ['是否需要增加空输入测试？'])


class RealPortalCoverageTests(unittest.TestCase):
    def test_all_five_projects_are_available_including_historical_tasks(self):
        root = Path(__file__).resolve().parents[1]
        if not (root / 'data/ray-project__ray/issues.jsonl').exists():
            self.skipTest('Local full dataset is unavailable')
        portal = Portal(root)
        result = portal.catalog()
        self.assertEqual(len(result['projects']), 5)
        self.assertEqual(result['totals']['records'], 967)
        self.assertEqual(result['totals']['tasks'], 945)
        self.assertEqual(result['totals']['guides'], 22)
        self.assertEqual(result['totals']['analyses'], 50)
        self.assertEqual(portal.issues('ray-project/ray')['total'], 511)


if __name__ == '__main__':
    unittest.main()
