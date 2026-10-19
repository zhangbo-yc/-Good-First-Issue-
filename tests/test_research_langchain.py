"""Exercise LangChain transport with local runnables; never contact a provider."""
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from gfi_prototype.research.client import DeepSeekClient, ProviderFailure, ResearchSession


class LangChainClientTests(unittest.TestCase):
    def setUp(self):
        # A transport regression must never fall through to a live HTTP call.
        network = patch('httpx.HTTPTransport.handle_request',
                        side_effect=AssertionError('Network forbidden in offline tests'))
        network.start()
        self.addCleanup(network.stop)

    def test_client_constructs_chatdeepseek_with_bounded_json_settings(self):
        model = RunnableLambda(lambda messages, **kwargs: AIMessage(content='{}'))
        with patch('langchain_deepseek.ChatDeepSeek', return_value=model) as constructor:
            client = DeepSeekClient('test-credential')
            client.complete([{'role': 'user', 'content': 'hello'}])
        self.assertEqual(constructor.call_count, 1)
        settings = constructor.call_args.kwargs
        self.assertEqual(settings['model'], 'deepseek-flash')
        self.assertEqual(settings['max_retries'], 0)
        self.assertEqual(settings['max_tokens'], 5000)
        self.assertEqual(settings['temperature'], 0)
        self.assertEqual(settings['extra_body'], {'thinking': {'type': 'disabled'}})

    def test_usage_and_model_are_preserved_through_langchain(self):
        seen = []
        def invoke(messages, **kwargs):
            seen.append((messages, kwargs))
            return AIMessage(content='{"value":2}', response_metadata={'model_name': 'deepseek-flash'},
                usage_metadata={'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15})
        with patch('langchain_deepseek.ChatDeepSeek', return_value=RunnableLambda(invoke)):
            client = DeepSeekClient('test-credential')
            session = ResearchSession(client)
            self.assertEqual(session.request([{'role': 'user', 'content': 'hello'}], lambda x: x['value'], 'analysis'), 2)
        self.assertEqual(seen[0][0], [{'role': 'user', 'content': 'hello'}])
        self.assertEqual(seen[0][1]['response_format'], {'type': 'json_object'})
        self.assertEqual(session.resources()['total_tokens'], 15)
        self.assertTrue(session.resources()['usage_complete'])

    def test_missing_usage_remains_unknown_not_zero(self):
        model = RunnableLambda(lambda messages, **kwargs: AIMessage(content='{}'))
        with patch('langchain_deepseek.ChatDeepSeek', return_value=model):
            session = ResearchSession(DeepSeekClient('test-credential'))
            session.request([{'role': 'user', 'content': 'hello'}], lambda x: x, 'analysis')
        self.assertFalse(session.resources()['usage_complete'])

    def test_provider_errors_do_not_expose_secrets(self):
        def invoke(messages, **kwargs):
            exc = RuntimeError('private key and provider payload')
            exc.status_code = 429
            raise exc
        with patch('langchain_deepseek.ChatDeepSeek', return_value=RunnableLambda(invoke)):
            with self.assertRaises(ProviderFailure) as error:
                DeepSeekClient('test-credential').complete([{'role': 'user', 'content': 'hello'}])
        self.assertEqual(error.exception.status_code, 429)
        self.assertNotIn('private key', str(error.exception))

    def test_real_chatdeepseek_serializes_request_and_usage_with_mock_http(self):
        import httpx
        from langchain_deepseek import ChatDeepSeek
        requests = []
        def respond(request):
            import json
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'id': 'offline', 'object': 'chat.completion', 'created': 1,
                'model': 'deepseek-flash', 'choices': [{'index': 0, 'finish_reason': 'stop',
                    'message': {'role': 'assistant', 'content': '{"value":2}'}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            def factory(**settings):
                return ChatDeepSeek(**settings, http_client=http)
            with patch('langchain_deepseek.ChatDeepSeek', side_effect=factory):
                result = DeepSeekClient('test-credential').complete([{'role': 'user', 'content': 'hello'}])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['model'], 'deepseek-flash')
        self.assertEqual(requests[0]['response_format'], {'type': 'json_object'})
        self.assertEqual(requests[0]['thinking'], {'type': 'disabled'})
        self.assertFalse(requests[0]['stream'])
        self.assertEqual(result['usage'], {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15})
        self.assertEqual(result['content'], '{"value":2}')


if __name__ == '__main__':
    unittest.main()
