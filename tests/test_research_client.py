import json
import unittest

from gfi_prototype.research.client import BudgetExceeded, ResearchSession


class ClientBudgetTests(unittest.TestCase):
    def client(self, responses):
        class Client:
            model = 'deepseek-test'
            def complete(self, messages):
                content = responses.pop(0)
                return {'content': content, 'model': self.model, 'usage': {'prompt_tokens': 10,
                        'completion_tokens': 5, 'total_tokens': 15}, 'elapsed_seconds': 0.1}
        return Client()

    def test_validation_retry_counts_both_calls_and_usage(self):
        session = ResearchSession(self.client(['not json', '{"value":2}']), max_calls=2)
        value = session.request([{'role': 'user', 'content': 'question'}], lambda x: x['value'], 'analysis')
        self.assertEqual(value, 2)
        self.assertEqual(session.resources()['model_calls'], 2)
        self.assertEqual(session.resources()['total_tokens'], 30)
        self.assertEqual([x['status'] for x in session.records], ['validation_failed', 'valid'])

    def test_call_and_context_limits_stop_before_another_provider_request(self):
        responses = ['{}', '{}']
        session = ResearchSession(self.client(responses), max_calls=1)
        session.request([{'role': 'user', 'content': 'q'}], lambda x: x, 'audit')
        with self.assertRaises(BudgetExceeded):
            session.request([{'role': 'user', 'content': 'q'}], lambda x: x, 'audit')
        self.assertEqual(len(responses), 1)
        session = ResearchSession(self.client(responses), max_context_chars=2)
        with self.assertRaises(BudgetExceeded):
            session.request([{'role': 'user', 'content': 'too long'}], lambda x: x, 'analysis')
        self.assertEqual(len(responses), 1)

    def test_provider_failure_is_recorded_without_sensitive_error_text(self):
        class BadClient:
            model = 'deepseek-test'
            def complete(self, messages):
                raise RuntimeError('secret-provider-credential')
        session = ResearchSession(BadClient())
        with self.assertRaises(RuntimeError):
            session.request([{'role': 'user', 'content': 'q'}], lambda x: x, 'analysis')
        self.assertEqual(session.resources()['model_calls'], 1)
        self.assertNotIn('secret-provider-credential', json.dumps(session.resources()))
        self.assertFalse(session.resources()['usage_complete'])


if __name__ == '__main__':
    unittest.main()
