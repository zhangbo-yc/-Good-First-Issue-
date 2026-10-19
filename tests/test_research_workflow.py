"""Offline behavioral checks for the LangGraph research workflow."""
import copy
import unittest

from gfi_prototype.research import pipeline
from gfi_prototype.research.client import ResearchSession
from tests.test_research_pipeline import ScriptedClient, sample_packet


class AddingRetriever:
    def supplement(self, packet, queries, limit=4):
        enriched = copy.deepcopy(packet)
        evidence_id = f'E{len(enriched["evidence"])}'
        enriched['evidence'].append({'id': evidence_id, 'kind': 'source',
                                    'path': 'pkg/extra.py', 'text': 'WidgetError'})
        return enriched, [evidence_id]


class WorkflowTests(unittest.TestCase):
    def test_graph_exposes_actual_nodes_and_conditional_loop(self):
        self.assertTrue(callable(getattr(pipeline, 'build_workflow', None)),
                        'The workflow must expose a real compiled LangGraph')
        graph = pipeline.build_workflow(AddingRetriever(), ResearchSession(ScriptedClient()))
        topology = graph.get_graph()
        self.assertTrue({'analyze', 'audit', 'gate', 'supplement', 'finalize'} <= set(topology.nodes))
        edges = {(edge.source, edge.target) for edge in topology.edges}
        self.assertIn(('gate', 'supplement'), edges)
        self.assertIn(('supplement', 'analyze'), edges)

    def test_d_records_real_execution_order_and_progress(self):
        progress = []
        result = pipeline.run_variant(sample_packet(), AddingRetriever(),
            ResearchSession(ScriptedClient(), on_progress=progress.append), 'D')
        self.assertEqual(result.get('workflow', {}).get('engine'), 'langgraph')
        self.assertEqual(result['workflow']['node_trace'],
                         ['analyze', 'audit', 'gate', 'supplement', 'analyze', 'audit', 'gate', 'finalize'])
        self.assertTrue(any('supplement' in row for row in progress))

    def test_ab_bypass_audit_gate_and_supplement(self):
        for variant in 'AB':
            with self.subTest(variant=variant):
                result = pipeline.run_variant(sample_packet(), None, ResearchSession(ScriptedClient()), variant)
                self.assertEqual(result.get('workflow', {}).get('node_trace'), ['analyze', 'finalize'])
                self.assertEqual(result['stop_reason'], 'single_pass')
                self.assertEqual(result['resources']['model_calls'], 1)

    def test_failed_second_audit_keeps_last_complete_round_and_its_packet(self):
        class Client(ScriptedClient):
            def complete(self, messages):
                if '证据审查器' in messages[0]['content'] and self.audits == 1:
                    raise RuntimeError('secret provider text')
                return super().complete(messages)
        result = pipeline.run_variant(sample_packet(), AddingRetriever(), ResearchSession(Client()), 'D')
        self.assertEqual(result['stop_reason'], 'model_or_validation_error')
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(result['rounds']), 1)
        self.assertEqual(len(result['packet']['evidence']), 2)
        self.assertEqual(result['gated']['requirements'], [])
        self.assertEqual(result['resources']['model_calls'], 4)
        self.assertNotIn('secret provider text', str(result))

    def test_empty_query_stops_without_retrieval(self):
        class Client(ScriptedClient):
            def complete(self, messages):
                import json
                result = super().complete(messages)
                if '证据审查器' in messages[0]['content']:
                    value = json.loads(result['content'])
                    for row in value['reviews']:
                        row['retrieval_query'] = ''
                    result['content'] = json.dumps(value)
                return result
        result = pipeline.run_variant(sample_packet(), None, ResearchSession(Client()), 'D')
        self.assertEqual(result['stop_reason'], 'no_targeted_query')
        self.assertEqual(result['resources']['model_calls'], 2)

    def test_budget_stops_before_supplement_and_zero_rounds_is_valid(self):
        result = pipeline.run_variant(sample_packet(), None,
                                      ResearchSession(ScriptedClient(), max_calls=3), 'D')
        self.assertEqual(result['stop_reason'], 'budget_exhausted')
        self.assertEqual(result['resources']['model_calls'], 2)
        result = pipeline.run_variant(sample_packet(), None, ResearchSession(ScriptedClient()), 'D', max_rounds=0)
        self.assertEqual(result['stop_reason'], 'supplement_round_limit')
        self.assertEqual(len(result['rounds']), 1)

    def test_retrieval_failure_is_sanitized_and_input_is_not_mutated(self):
        class BadRetriever:
            def supplement(self, packet, queries, limit=4):
                raise RuntimeError('secret retrieval text')
        packet = sample_packet()
        before = copy.deepcopy(packet)
        result = pipeline.run_variant(packet, BadRetriever(), ResearchSession(ScriptedClient()), 'D')
        self.assertEqual(result['stop_reason'], 'retrieval_error')
        self.assertEqual(packet, before)
        self.assertNotIn('secret retrieval text', str(result))


if __name__ == '__main__':
    unittest.main()
