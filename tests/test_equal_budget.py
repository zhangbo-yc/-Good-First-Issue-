import copy
import json
import tempfile
import unittest
from pathlib import Path

from gfi_prototype.research import equal_budget as eq
from gfi_prototype.research.client import ResearchSession
from gfi_prototype.research.pipeline import run_variant
from tests.test_research_pipeline import ScriptedClient, sample_packet


class AddingRetriever:
    def supplement(self, packet, queries, limit=4):
        new = copy.deepcopy(packet)
        eid = f'E{len(new["evidence"])}'
        new['evidence'].append({'id': eid, 'kind': 'test', 'path': 'tests/test_widget.py', 'text': 'WidgetError'})
        return new, [eid]


class EqualBudgetTests(unittest.TestCase):
    def adaptive(self):
        return run_variant(sample_packet(), AddingRetriever(), ResearchSession(ScriptedClient(), attempts=1), 'D')

    def test_repeat_control_shares_prefix_and_matches_round_calls(self):
        d = self.adaptive()
        session = ResearchSession(ScriptedClient(), max_calls=6, attempts=1)
        original = copy.deepcopy(d)
        r = eq.run_control(d, None, session, 'R')
        self.assertEqual(d, original)
        for key in ('packet', 'analysis', 'audit', 'gated'):
            self.assertEqual(r['rounds'][0][key], d['rounds'][0][key])
        self.assertIsNone(r['rounds'][0]['retrieval'])
        self.assertEqual(len(r['rounds']), len(d['rounds']))
        self.assertEqual(r['resources']['model_calls'], d['resources']['model_calls'])
        self.assertEqual(r['packet']['evidence'], d['rounds'][0]['packet']['evidence'])

    def test_control_revision_gets_previous_answer_and_audit(self):
        d = self.adaptive()
        seen = []
        class Client(ScriptedClient):
            def complete(self, messages):
                if '证据审查器' not in messages[0]['content']:
                    seen.append(messages)
                return super().complete(messages)
        eq.run_control(d, None, ResearchSession(Client(), max_calls=6, attempts=1), 'R')
        self.assertEqual(len(seen), 1)
        self.assertTrue(any('Previous analysis' in row['content'] and 'Previous audit' in row['content']
                            for row in seen[0]))

    def test_static_control_expands_even_if_first_audit_accepts_all(self):
        d = self.adaptive()
        for row in d['rounds'][0]['audit']['reviews']:
            row['status'] = 'sufficient'
        class Static:
            calls = 0
            def static_supplement(self, packet, limit=4, seen_keys=None):
                self.calls += 1
                return AddingRetriever().supplement(packet, ['fixed issue query'], limit)
        retriever = Static()
        s = eq.run_control(d, retriever, ResearchSession(ScriptedClient(), max_calls=6, attempts=1), 'S')
        self.assertEqual(retriever.calls, 1)
        self.assertEqual(len(s['packet']['evidence']), 3)

    def test_one_round_d_creates_identical_control_without_extra_requests(self):
        d = run_variant(sample_packet(), None, ResearchSession(ScriptedClient(), attempts=1), 'C')
        d['variant'] = 'D'
        session = ResearchSession(ScriptedClient(), max_calls=6, attempts=1)
        r = eq.run_control(d, None, session, 'R')
        self.assertEqual(r['gated'], d['gated'])
        self.assertEqual(r['resources']['model_calls'], d['resources']['model_calls'])

    def test_valid_stage_receipt_is_reused_but_counted_for_logical_budget(self):
        class Client:
            model = 'deepseek-test'
            def complete(self, messages):
                return {'content': '{"answer":1}', 'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12},
                        'model': self.model, 'elapsed_seconds': .1}
        with tempfile.TemporaryDirectory() as folder:
            for _ in range(2):
                session = eq.CachedSession(Client(), Path(folder), max_calls=6)
                self.assertEqual(session.request([{'role':'user','content':'q'}], lambda x:x, 'analysis'), {'answer':1})
                self.assertEqual(session.resources()['model_calls'], 1)
            self.assertEqual(len(list(Path(folder).glob('valid/*.json'))), 1)
            self.assertEqual(session.cache_hits, 1)

    def test_normalized_judge_payload_removes_only_prediction_identity(self):
        payload = {'source': {'task_id':'a/b#1'}, 'prediction':{'prediction_id':'P1','goal':{'text':'goal'}},
                   'available_evidence_ids':['X1']}
        normalized = eq.normalized_judge_payload(payload)
        self.assertNotIn('prediction_id', normalized['prediction'])
        self.assertIn('prediction_id', payload['prediction'])
        self.assertEqual(normalized['prediction']['goal'], payload['prediction']['goal'])

    def test_fixed_rank_continuation_never_readds_previously_seen_dropped_chunk(self):
        from gfi_prototype.research.equal_budget import FixedQueryRetriever
        class Store:
            repo = 'a/b'
            sha = 'a'*40
            query = None
            def search(self, query, limit=16):
                self.query = query
                return [{'path':f'pkg/{i}.py','start_line':1,'end_line':2,'text':f'code{i}','kind':'source'}
                        for i in range(16)]
            def verify_bundle(self, packet):
                pass
        retriever = FixedQueryRetriever.__new__(FixedQueryRetriever)
        retriever.store = Store()
        retriever._ensure_index = lambda: None
        retriever._fixed_query = lambda packet: packet['title'] + '\n' + 'Fix Widget errors'
        packet = sample_packet()
        seen = {(f'pkg/{i}.py',1,2) for i in range(8)}
        new, added = retriever.static_supplement(packet, seen_keys=seen)
        self.assertEqual(len(added), 4)
        self.assertEqual([e['path'] for e in new['evidence'] if e['id'] in added],
                         [f'pkg/{i}.py' for i in range(8,12)])
        self.assertIn('Fix Widget errors',retriever.store.query)

    def test_fixed_query_uses_verified_full_original_issue(self):
        with tempfile.TemporaryDirectory() as folder:
            retriever = eq.FixedQueryRetriever.__new__(eq.FixedQueryRetriever)
            retriever.folder = Path(folder)
            retriever.store = type('Store', (), {'repo': 'a/b'})()
            packet = sample_packet()
            body = 'x' * 7000 + 'unique_tail'
            path = Path(folder) / 'issues' / f'{packet["issue_number"]}.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'repo':'a/b', 'complete':True, 'issue':{
                'number':packet['issue_number'], 'title':packet['title'], 'body':body}}), encoding='utf-8')
            from gfi_prototype.common import sha256_file
            packet['evidence'][0]['bundle_sha256'] = sha256_file(path)
            self.assertEqual(retriever._fixed_query(packet), packet['title']+'\n'+body)
            path.write_text('{}', encoding='utf-8')
            with self.assertRaises(ValueError):
                retriever._fixed_query(packet)

    def test_shared_prefix_excludes_later_d_calls_and_has_valid_stage_receipts(self):
        d = self.adaptive()
        r = eq.run_control(d,None,ResearchSession(ScriptedClient(),max_calls=6,attempts=1),'R')
        self.assertEqual(r['resources']['calls'][:2],d['resources']['calls'][:2])
        self.assertEqual(len(r['resources']['calls']),4)

    def test_provider_failure_writes_cost_receipt_but_no_success_cache(self):
        class Client:
            model='deepseek-test'
            def complete(self,messages):
                raise RuntimeError('secret provider body')
        with tempfile.TemporaryDirectory() as folder:
            session=eq.CachedSession(Client(),Path(folder),max_calls=6)
            with self.assertRaises(Exception):
                session.request([{'role':'user','content':'q'}],lambda x:x,'analysis')
            self.assertEqual(len(list(Path(folder).glob('valid/*.json'))),0)
            costs=eq.paid_attempts(Path(folder))
            self.assertEqual(costs['model_calls'],1)
            self.assertEqual(costs['failed_calls'],1)
            self.assertNotIn('secret provider body',''.join(p.read_text() for p in Path(folder).glob('attempts/*.json')))

    def test_static_control_uses_same_analysis_input_structure_as_d(self):
        d=self.adaptive()
        seen=[]
        class Client(ScriptedClient):
            def complete(self,messages):
                if '证据审查器' not in messages[0]['content']:
                    seen.append(messages)
                return super().complete(messages)
        class Static:
            def static_supplement(self,packet,limit=4,seen_keys=None):
                return AddingRetriever().supplement(packet,['fixed issue query'],limit)
        eq.run_control(d,Static(),ResearchSession(Client(),max_calls=6,attempts=1),'S')
        self.assertEqual(len(seen[0]),2)
        self.assertFalse(any('Previous analysis' in m['content'] for m in seen[0]))

    def test_three_round_control_uses_six_calls_and_preserves_final_gate(self):
        d=run_variant(sample_packet(),AddingRetriever(),ResearchSession(ScriptedClient(True),max_calls=6,attempts=1),'D')
        r=eq.run_control(d,None,ResearchSession(ScriptedClient(True),max_calls=6,attempts=1),'R')
        self.assertEqual(len(r['rounds']),3)
        self.assertEqual(r['resources']['model_calls'],6)
        self.assertEqual(r['gated']['requirements'],[])


if __name__ == '__main__':
    unittest.main()
