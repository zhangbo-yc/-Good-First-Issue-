"""Resume the frozen experiment with bounded recovery and validation diagnostics.

Run directly in PyCharm. The original script, prompts, validators and frozen
design are unchanged. Paid failures remain in receipts and outside the strict
no-failure subset. This operational wrapper does not repair or relax answers.
"""
import json
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4

import run_equal_budget_experiment as base
from gfi_prototype.common import atomic_json, sha256_file, utc_now
from gfi_prototype.research.equal_budget import CachedSession


class DiagnosticSession(CachedSession):
    """Keep failed response details locally without changing requests/validation."""
    def request(self, messages, validator, purpose):
        original = self.client
        response = {}
        reasons = []

        class Observer:
            def __getattr__(self, name):
                return getattr(original, name)

            def complete(self, messages):
                result = original.complete(messages)
                response.update(result)
                return result

        def checked(value):
            try:
                return validator(value)
            except (ValueError, TypeError, KeyError) as exc:
                if hasattr(exc, 'errors'):
                    # Exclude Pydantic input values and arbitrary exception context.
                    reasons.extend({'location':row.get('loc'), 'type':row.get('type'),
                                    'message':row.get('msg')} for row in exc.errors())
                else:
                    reasons.append({'type':type(exc).__name__, 'message':str(exc)[:4000]})
                raise

        self.client = Observer()
        try:
            return super().request(messages, checked, purpose)
        except ValueError:
            if response:
                if not reasons:
                    try:
                        json.loads(response.get('content', ''))
                    except (ValueError, TypeError) as exc:
                        reasons.append({'type':type(exc).__name__, 'message':str(exc)[:4000]})
                diagnostic = {'purpose':purpose, 'created_at':utc_now(), 'detail':reasons,
                    'response':response.get('content'),
                    'request_sha256':self.records[-1].get('request_sha256') if self.records else None}
                serialized = json.dumps(diagnostic, ensure_ascii=False)
                secret = getattr(original, 'secret', None)
                if secret:
                    serialized = serialized.replace(secret, '[REDACTED]')
                path = self.folder/'diagnostics'/f'{uuid4().hex}.json'
                atomic_json(path, json.loads(serialized))
                safe_reasons = json.loads(serialized)['detail']
                self._emit_progress(f'具体校验原因：{json.dumps(safe_reasons, ensure_ascii=False)[:1800]}')
                self._emit_progress(f'诊断已保存：{path}')
            raise
        finally:
            self.client = original


def analyze_resilient(output, protocol, config, key, task_ids, max_attempts=3, run_one=None):
    """Retry only incomplete conditions; move on after a bounded number of tries."""
    run_one = run_one or base.run_analysis
    failed = []
    completed = []
    for index, task_id in enumerate(task_ids, 1):
        print(f'\n[总进度 {index}/{len(task_ids)}] {task_id}', flush=True)
        for attempt in range(1, max_attempts + 1):
            try:
                run_one(output, protocol, config, key, [task_id])
                completed.append(task_id)
                break
            except RuntimeError as exc:
                # Only recover the explicit incomplete-result signal. Retrieval,
                # configuration, integrity and programming errors must surface.
                if str(exc) != 'Condition incomplete; resume from validated stage receipts':
                    raise
                if attempt == max_attempts:
                    failed.append(task_id)
                    print(f'暂存失败题：{task_id}；本轮已尝试{max_attempts}次，继续下一题。', flush=True)
                else:
                    print(f'自动续跑 {attempt + 1}/{max_attempts}：复用成功节点；新增失败调用仍计费留档。', flush=True)
        atomic_json(Path(output)/'recovery_progress.json', {
            'completed_task_ids':completed, 'failed_task_ids':failed,
            'selected_task_ids':list(task_ids), 'updated_at':utc_now()})
    return failed


def main(argv=None):
    original_analysis = base.run_analysis

    def run_batch(output, protocol, config, key, task_ids):
        code_hash = sha256_file(Path(__file__))
        atomic_json(output/'recovery_runs'/f'{uuid4().hex}.json', {
            'wrapper_sha256':code_hash, 'base_config_sha256':config['config_sha256'],
            'started_at':utc_now(), 'max_task_attempts_per_run':3,
            'policy':'unchanged_messages_validators; bounded_resume; failures_retained; skip_incomplete_review'})
        failed = analyze_resilient(output, protocol, config, key, task_ids, run_one=original_analysis)
        if failed:
            print(f'本轮有{len(failed)}题未完成，其他题已继续处理。清单见recovery_progress.json。'
                  '再次运行本脚本补跑；全部39题完成前不启动盲评。', flush=True)
            raise RuntimeError('Incomplete tasks remain; see recovery_progress.json')

    with patch.object(base, 'CachedSession', DiagnosticSession), patch.object(base, 'run_analysis', run_batch):
        return base.main(argv)


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
