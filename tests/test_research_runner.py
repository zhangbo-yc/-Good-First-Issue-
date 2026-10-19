import json
import tempfile
import unittest
from pathlib import Path

from gfi_prototype.research.runner import ExperimentRunner, summarize_results


class FakeRetriever:
    def initial(self, number, issue_only=False):
        evidence = [{'id': 'I1', 'kind': 'issue', 'text': 'Fix widget', 'url': 'https://github.com/a/b/issues/1'}]
        if not issue_only:
            evidence.append({'id': 'E1', 'kind': 'source', 'path': 'pkg/widget.py', 'text': 'class Widget: pass'})
        return {'repo': 'a/b', 'issue_number': number, 'title': 'Fix widget', 'source_commit': 'a' * 40,
                'evidence': evidence, 'source_evidence_found': 0 if issue_only else 1}

    def supplement(self, packet, queries, limit=4):
        return packet, []


class FakeSession:
    def __init__(self):
        self.records = []

    def resources(self):
        return {'model_calls': len(self.records), 'total_tokens': len(self.records) * 5,
                'prompt_tokens': 0, 'completion_tokens': 0, 'elapsed_seconds': 0.1,
                'usage_complete': True, 'calls': [], 'budget': {}}


class CostSession(FakeSession):
    def resources(self):
        return {'model_calls': 2, 'total_tokens': 11, 'prompt_tokens': 6, 'completion_tokens': 5,
                'elapsed_seconds': .2, 'usage_complete': True, 'calls': [], 'budget': {}}


class RunnerTests(unittest.TestCase):
    def protocol(self):
        return {'protocol_sha256': 'frozen', 'tasks': [{'task_id': 'a/b#1', 'repo': 'a/b',
            'issue_number': 1, 'issue_sha256': 'raw', 'source_commit': 'a' * 40}],
            'candidate_pool': [], 'seed': 1}

    def test_runner_resumes_success_and_never_overwrites_result(self):
        calls = []
        def run(packet, retriever, session, variant, max_rounds=2):
            calls.append(variant)
            return {'variant': variant, 'status': 'complete', 'analysis': {'task_goal': {'text': 'goal'}},
                    'gated': None, 'audit': None, 'packet': packet, 'rounds': [], 'errors': [],
                    'stop_reason': 'single_pass', 'resources': {'model_calls': 1, 'total_tokens': 5}}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            runner = ExperimentRunner(Path('.'), self.protocol(), output,
                retriever_factory=lambda root, repo: FakeRetriever(), session_factory=lambda variant: FakeSession(),
                pipeline=run, verify=lambda root, protocol: None, model='deepseek-test')
            report = runner.run(['A', 'B'])
            self.assertEqual(report['completed'], 2)
            self.assertEqual(calls, ['A', 'B'])
            first = (output / 'results/a__b__1/A.json').read_bytes()
            calls.clear()
            report = runner.run(['A', 'B'])
            self.assertEqual(report['cache_hits'], 2)
            self.assertEqual(calls, [])
            self.assertEqual((output / 'results/a__b__1/A.json').read_bytes(), first)

    def test_foreign_workflow_results_are_rejected_before_any_model_session(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            path = output / 'results/a__b__1/D.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'status': 'complete', 'cache_key': 'legacy-cache-key',
                                        'pipeline_version': 'evidence_adaptive_v2'}))
            original = path.read_bytes()
            calls = []
            runner = ExperimentRunner(Path('.'), self.protocol(), output,
                retriever_factory=lambda root, repo: FakeRetriever(),
                session_factory=lambda variant: calls.append(variant) or FakeSession(),
                verify=lambda root, protocol: None, model='deepseek-test')
            # Check the whole requested run before processing even its first case.
            with self.assertRaisesRegex(ValueError, 'different|incompatible'):
                runner.run(['A', 'D'])
            self.assertEqual(calls, [])
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse((output / 'results/a__b__1/A.json').exists())

    def test_failed_result_is_recorded_without_raw_error_and_can_resume(self):
        state = {'fail': True}
        def run(*args, **kwargs):
            if state['fail']:
                raise RuntimeError('secret provider output')
            return {'variant': 'A', 'status': 'complete', 'analysis': {}, 'packet': args[0],
                    'gated': None, 'audit': None, 'rounds': [], 'errors': [], 'stop_reason': 'single_pass',
                    'resources': {'model_calls': 1, 'total_tokens': 1}}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            runner = ExperimentRunner(Path('.'), self.protocol(), output,
                retriever_factory=lambda root, repo: FakeRetriever(), session_factory=lambda variant: CostSession(),
                pipeline=run, verify=lambda root, protocol: None, model='deepseek-test')
            report = runner.run(['A'])
            self.assertEqual(report['failed'], 1)
            self.assertNotIn('secret provider output', json.dumps(report))
            attempts = list((output / 'attempts').glob('**/*.json'))
            self.assertEqual(len(attempts), 1)
            self.assertEqual(json.loads(attempts[0].read_text())['resources']['model_calls'], 2)
            state['fail'] = False
            report = runner.run(['A'])
            self.assertEqual(report['completed'], 1)
            result = json.loads((output / 'results/a__b__1/A.json').read_text())
            self.assertEqual(result['cumulative_resources']['model_calls'], 4)

    def test_summary_reports_resources_and_abstention_without_claiming_accuracy(self):
        rows = [{'task_id': 'a/b#1', 'variant': 'A', 'status': 'complete', 'resources': {'model_calls': 1,
                 'total_tokens': 10, 'elapsed_seconds': 1}, 'gated': None},
                {'task_id': 'a/b#1', 'variant': 'C', 'status': 'complete', 'resources': {'model_calls': 2,
                 'total_tokens': 20, 'elapsed_seconds': 2}, 'gated': {'requirements': [], 'withheld_claim_ids': ['x']}}]
        summary = summarize_results(rows)
        self.assertEqual(summary['by_variant']['C']['abstained_results'], 1)
        self.assertNotIn('accuracy', json.dumps(summary).lower())
        self.assertEqual(summary['status'], 'operational_only_pending_human_review')


if __name__ == '__main__':
    unittest.main()
