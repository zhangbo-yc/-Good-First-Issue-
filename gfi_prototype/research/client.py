"""DeepSeek-only research transport and per-case resource accounting."""
import json
import threading
import time

from gfi_prototype.common import sha256_text, utc_now


class BudgetExceeded(RuntimeError):
    pass


class ProviderFailure(RuntimeError):
    def __init__(self, status_code=None):
        super().__init__('DeepSeek request failed')
        self.status_code = status_code


class DeepSeekClient:
    def __init__(self, key, model='deepseek-flash', max_output_tokens=5000):
        if not key or not model.startswith('deepseek-'):
            raise ValueError('Research batch generation requires a DeepSeek model and credential')
        self.secret, self.model, self.max_output_tokens = key, model, max_output_tokens
        from langchain_deepseek import ChatDeepSeek
        from pydantic import SecretStr

        # ResearchSession owns the retry budget; do not add hidden SDK retries.
        self.runnable = ChatDeepSeek(model=model, api_key=SecretStr(key),
            base_url='https://api.deepseek.com', temperature=0, max_tokens=max_output_tokens,
            max_retries=0, timeout=100, streaming=False,
            extra_body={'thinking': {'type': 'disabled'}}).bind(response_format={'type': 'json_object'})

    def complete(self, messages):
        from langsmith import tracing_context

        started = time.monotonic()
        try:
            with tracing_context(enabled=False):
                response = self.runnable.invoke(messages, config={'callbacks': []})
            metadata = response.response_metadata or {}
            usage = response.usage_metadata
            # Preserve missing usage, rather than treating unknown billing as zero.
            if usage is not None:
                usage = {'prompt_tokens': usage['input_tokens'], 'completion_tokens': usage['output_tokens'],
                         'total_tokens': usage['total_tokens']}
            else:
                usage = metadata.get('token_usage') or None
            return {'content': response.content, 'model': metadata.get('model_name') or metadata.get('model') or self.model,
                    'usage': usage, 'elapsed_seconds': round(time.monotonic() - started, 4)}
        except Exception as exc:
            raise ProviderFailure(getattr(exc, 'status_code', None)) from None


class ResearchSession:
    """Bound calls/context; report actual tokens, including invalid model replies.

    The token stop is based on provider-reported usage after each request, not a
    falsely exact pre-call tokenizer. No further calls follow unknown usage.
    """
    def __init__(self, client, max_calls=9, max_context_chars=60000,
                 max_reported_tokens=100000, attempts=2, on_progress=None):
        if min(max_calls, max_context_chars, max_reported_tokens, attempts) < 1 or attempts > 2:
            raise ValueError('Invalid research budget')
        self.client = client
        self.max_calls, self.max_context_chars = max_calls, max_context_chars
        self.max_reported_tokens, self.attempts = max_reported_tokens, attempts
        self.records = []
        self.on_progress = on_progress

    def _emit_progress(self, message):
        if self.on_progress is not None:
            self.on_progress(message)

    def _check_budget(self, messages):
        resources = self.resources()
        if len(self.records) >= self.max_calls:
            raise BudgetExceeded('model_call_budget')
        if resources['total_tokens'] >= self.max_reported_tokens:
            raise BudgetExceeded('reported_token_budget')
        if not resources['usage_complete']:
            raise BudgetExceeded('unknown_provider_usage')
        text = '\n'.join(message['content'] for message in messages)
        if len(text) > self.max_context_chars:
            raise BudgetExceeded('context_character_budget')
        secret = getattr(self.client, 'secret', None)
        if secret and secret in text:
            raise ValueError('Credential detected in model input')

    def request(self, messages, validator, purpose):
        current = list(messages)
        for attempt in range(self.attempts):
            self._check_budget(current)
            record = {'purpose': purpose, 'attempt': attempt + 1, 'started_at': utc_now(),
                'request_sha256': sha256_text(json.dumps(current, ensure_ascii=False, sort_keys=True)),
                'status': 'provider_failed', 'usage': None, 'error_type': None,
                'model': getattr(self.client, 'model', 'unknown')}
            self.records.append(record)
            started = time.monotonic()
            self._emit_progress(f"DeepSeek请求开始：{purpose}，第{attempt + 1}/{self.attempts}次尝试，"
                                f"模型={record['model']}，当前累计调用={len(self.records)}。")
            stop_heartbeat = threading.Event()
            heartbeat = None
            if self.on_progress is not None:
                def report_waiting():
                    while not stop_heartbeat.wait(15):
                        elapsed = time.monotonic() - started
                        self._emit_progress(f"DeepSeek仍在处理：{purpose}，已等待{elapsed:.0f}秒。")
                heartbeat = threading.Thread(target=report_waiting, daemon=True)
                heartbeat.start()
            try:
                completion = self.client.complete(current)
            except Exception as exc:
                stop_heartbeat.set()
                if heartbeat is not None:
                    heartbeat.join(timeout=0.2)
                record.update(error_type=type(exc).__name__, http_status=getattr(exc, 'status_code', None),
                              elapsed_seconds=round(time.monotonic() - started, 4))
                self._emit_progress(f"DeepSeek请求失败：{type(exc).__name__}，"
                                    f"耗时{record['elapsed_seconds']:.1f}秒。")
                raise ProviderFailure(getattr(exc, 'status_code', None)) from None
            stop_heartbeat.set()
            if heartbeat is not None:
                heartbeat.join(timeout=0.2)
            record.update(usage=completion.get('usage'), model=completion.get('model', record['model']),
                          elapsed_seconds=completion.get('elapsed_seconds', time.monotonic() - started))
            try:
                result = validator(json.loads(completion['content']))
                record['status'] = 'valid'
                usage = record.get('usage') or {}
                self._emit_progress(f"DeepSeek请求完成：{record['elapsed_seconds']:.1f}秒，"
                                    f"{usage.get('total_tokens', '未知')} tokens。")
                return result
            except (ValueError, TypeError, KeyError) as exc:
                record.update(status='validation_failed', error_type=type(exc).__name__)
                self._emit_progress(f"响应校验失败：{type(exc).__name__}；"
                                    f"{'准备重试。' if attempt + 1 < self.attempts else '已达到重试上限。'}")
                if attempt + 1 == self.attempts:
                    raise ValueError(f'{purpose} failed bounded JSON/schema/citation validation') from None
                current = list(messages) + [{'role': 'user', 'content':
                    '上一响应未通过JSON、schema或引用校验。请严格遵守给定schema与ID集合；文件项只引用路径完全匹配的源码证据，'
                    '不引用Issue或评论来证明文件路径；审查只能使用该结论原本引用的证据，所有claim恰好一次。'}]

    def resources(self):
        prompt = sum((row.get('usage') or {}).get('prompt_tokens', 0) for row in self.records)
        output = sum((row.get('usage') or {}).get('completion_tokens', 0) for row in self.records)
        return {'model_calls': len(self.records), 'prompt_tokens': prompt, 'completion_tokens': output,
                'total_tokens': sum((row.get('usage') or {}).get('total_tokens',
                    (row.get('usage') or {}).get('prompt_tokens', 0) +
                    (row.get('usage') or {}).get('completion_tokens', 0)) for row in self.records),
                'usage_complete': all(isinstance(row.get('usage'), dict) and
                                      'total_tokens' in row['usage'] for row in self.records),
                'elapsed_seconds': round(sum(row.get('elapsed_seconds', 0) for row in self.records), 4),
                'calls': list(self.records), 'budget': {'max_calls': self.max_calls,
                    'max_context_chars': self.max_context_chars, 'reported_token_stop': self.max_reported_tokens}}
