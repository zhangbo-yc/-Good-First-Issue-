import json
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class QaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT / 'gfi_prototype/qa.py').exists():
            raise unittest.SkipTest('QA module is missing')

    def setUp(self):
        from gfi_prototype import qa
        self.qa = qa
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / 'example__project'
        self.folder.mkdir()
        sha = 'a' * 40
        archive = self.folder / f'source-{sha}.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('repo/pkg/widget.py',
                       'class Widget:\n    def paint(self):\n        return "painted"\n')
            z.writestr('repo/tests/test_widget.py',
                       'def test_widget_paint():\n    assert Widget().paint() == "painted"\n')
        from gfi_prototype.common import sha256_file
        (self.folder / 'source_snapshot.json').write_text(
            json.dumps({'repo': 'example/project', 'commit_sha': sha,
                        'archive': archive.name, 'archive_sha256': sha256_file(archive)}), encoding='utf-8')
        (self.folder / 'issues').mkdir()
        (self.folder / 'issues/1.json').write_text(json.dumps({
            'repo': 'example/project', 'complete': True, 'fetched_at': '2026-10-16T00:00:00Z',
            'issue': {'number': 1, 'title': 'Widget paint', 'body': 'Paint the widget faster.',
                      'state': 'open', 'html_url': 'https://github.com/example/project/issues/1',
                      'labels': [{'name': 'good first issue'}]}, 'comments': []}), encoding='utf-8')

    def context(self):
        return self.qa.gather_context(self.folder, 'widget paint')

    def test_gather_context_finds_matching_source(self):
        context = self.context()
        self.assertEqual(context['repo'], 'example/project')
        self.assertTrue(any('widget.py' in ref['path'] for ref in context['refs']))
        self.assertTrue(context['refs'][0]['url'].startswith('https://github.com/example/project/blob/'))

    def test_compose_answer_includes_citations_and_notice(self):
        answer = self.qa.compose_answer('example/project', 1, 'widget paint 在哪里？', self.context())
        self.assertIn('widget.py', answer)
        self.assertIn('不联网', answer)
        self.assertIn('不代表维护者结论', answer)

    def test_empty_question_is_rejected(self):
        with self.assertRaises(ValueError):
            self.qa.compose_answer('example/project', 1, '   ', self.context())

    def test_offline_stream_reassembles_full_answer(self):
        text = ''.join(self.qa.answer_stream('example/project', 1, 'widget paint', self.context()))
        self.assertIn('widget.py', text)
        self.assertIn('不代表维护者结论', text)

    def test_model_stream_is_forwarded(self):
        class FakeModel:
            def stream(self, prompt):
                yield from (type('Token', (), {'content': piece})() for piece in ['甲', '乙', '丙'])

        pieces = list(self.qa.answer_stream('example/project', 1, 'q', self.context(), model=FakeModel()))
        self.assertEqual(pieces, ['甲', '乙', '丙'])

    def test_chinese_question_uses_current_issue_for_retrieval(self):
        context = self.qa.gather_context(self.folder, '这个问题从哪里开始？', issue_number=1)
        self.assertEqual(context['issue']['number'], 1)
        self.assertIn('Paint the widget', context['issue']['body'])
        self.assertTrue(context['refs'])

    def test_prompt_contains_issue_analysis_and_bounded_history(self):
        profile = {'repo': 'example/project', 'issue_number': 1, 'goal': 'Improve widget speed'}
        context = self.qa.gather_context(self.folder, '怎么验证？', issue_number=1, profile=profile)
        prompt = self.qa.build_prompt('怎么验证？', context,
                                      history=[{'role': 'user', 'text': 'previous question'}])
        self.assertIn('Paint the widget faster.', prompt)
        self.assertIn('Improve widget speed', prompt)
        self.assertIn('previous question', prompt)
        self.assertIn('怎么验证', prompt)

    def test_task_mismatch_rejected_before_retrieval(self):
        with self.assertRaises(ValueError):
            self.qa.gather_context(self.folder, 'widget', issue_number=1,
                                   profile={'repo': 'example/project', 'issue_number': 2})

    def test_offline_answer_explains_recommendations_from_current_task(self):
        context = self.qa.gather_context(self.folder, '为什么推荐下一个任务？', issue_number=1,
            recommendations=[{'task_id': 'example/project#2', 'title': 'Other widget',
                              'shared_technologies': [{'name': 'Python'}],
                              'additional_technologies': [{'name': 'NumPy'}]}])
        answer = self.qa.compose_answer('example/project', 1, '为什么推荐下一个任务？', context)
        self.assertIn('Python', answer)
        self.assertIn('NumPy', answer)
        self.assertIn('example/project#2', answer)

    def test_history_passes_to_model_stream(self):
        prompts = []
        class FakeModel:
            def stream(self, messages):
                prompts.append(str(messages))
                yield type('Token', (), {'content': '回答'})()
        result = ''.join(self.qa.answer_stream('example/project', 1, 'question', self.context(),
                         model=FakeModel(), history=[{'role': 'user', 'text': 'Earlier widget'}]))
        self.assertEqual(result, '回答')
        self.assertIn('Earlier widget', prompts[0])

    def test_displayed_model_analysis_keeps_its_own_comment_evidence(self):
        artifact = {'kind': 'model', 'manifest': {'model': 'deepseek-flash'},
                    'analysis': {'related_files': [{'path': 'pkg/widget.py', 'evidence_ids': ['E1']}],
                                 'blockers': [{'text': 'Check compatibility', 'evidence_ids': ['C2']}]},
                    'packet': {'repo': 'example/project', 'issue_number': 1, 'source_commit': 'a' * 40,
                               'evidence': [{'id': 'C2', 'kind': 'comment',
                                             'text': 'Maintainer suggests removing redundant override'},
                                            {'id': 'E1', 'path': 'pkg/widget.py', 'text': 'class Widget:'}]}}
        context = self.qa.gather_context(self.folder, '为什么删掉？', issue_number=1,
                                         displayed_artifact=artifact)
        prompt = self.qa.build_prompt('依据 C2 是什么？', context)
        self.assertIn('Maintainer suggests removing redundant override', prompt)
        self.assertIn('Check compatibility', prompt)
        self.assertIn('related_files', prompt)

    def test_displayed_model_evidence_from_other_task_is_rejected(self):
        with self.assertRaises(ValueError):
            self.qa.gather_context(self.folder, 'widget', issue_number=1,
                displayed_artifact={'kind': 'model', 'packet': {'repo': 'other/repo',
                    'issue_number': 2, 'source_commit': 'a' * 40}, 'analysis': {}})


if __name__ == '__main__':
    unittest.main()
