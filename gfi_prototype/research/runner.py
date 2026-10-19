"""Resumable frozen A–D experiment execution; results never enter product caches."""
import json
import uuid
from collections import defaultdict
from pathlib import Path

from gfi_prototype.common import atomic_json, sha256_file, sha256_text, utc_now
from gfi_prototype.research.pipeline import PIPELINE_VERSION, run_variant
from gfi_prototype.research.protocol import verify_protocol
from gfi_prototype.research.retrieval import SnapshotRetriever


def result_key(protocol, task, variant, model):
    payload = {'protocol_sha256': protocol['protocol_sha256'], 'task_id': task['task_id'],
               'issue_sha256': task['issue_sha256'], 'source_commit': task['source_commit'],
               'variant': variant, 'model': model, 'pipeline_version': PIPELINE_VERSION}
    return sha256_text(json.dumps(payload, sort_keys=True, separators=(',', ':')))


class ExperimentRunner:
    def __init__(self, root, protocol, output_dir, retriever_factory=None, session_factory=None,
                 pipeline=run_variant, verify=verify_protocol, model='deepseek-flash', progress=None):
        self.root, self.protocol, self.output_dir = Path(root).resolve(), protocol, Path(output_dir)
        self.retriever_factory = retriever_factory or (lambda root, repo: SnapshotRetriever(root, repo))
        if session_factory is None:
            raise ValueError('A session factory is required')
        self.session_factory, self.pipeline, self.verify, self.model = session_factory, pipeline, verify, model
        self.progress = progress

    def _emit_progress(self, message):
        if self.progress is not None:
            self.progress(message)

    def _path(self, task, variant):
        safe = task['task_id'].replace('/', '__').replace('#', '__')
        return self.output_dir / 'results' / safe / f'{variant}.json'

    def _cached(self, path, key):
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        return value if value.get('cache_key') == key and value.get('status') == 'complete' else None

    def _check_output_identity(self, selected, variants):
        """Preflight every target before spending tokens or replacing old records."""
        for task in selected:
            for variant in variants:
                path = self._path(task, variant)
                if not path.exists():
                    continue
                try:
                    existing = json.loads(path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    raise ValueError('Unreadable result exists; use a different --output directory') from None
                key = result_key(self.protocol, task, variant, self.model)
                if not isinstance(existing, dict) or existing.get('cache_key') != key:
                    raise ValueError('Existing results use an incompatible workflow, model or protocol; '
                                     'use a different --output directory. 历史实验结果不会被覆盖。')

    def _attempt_path(self, task, variant, run_id):
        safe = task['task_id'].replace('/', '__').replace('#', '__')
        return self.output_dir / 'attempts' / safe / variant / f'{run_id}.json'

    def _attempts(self, task, variant, key):
        folder = self._attempt_path(task, variant, 'placeholder').parent
        rows = []
        for path in sorted(folder.glob('*.json')):
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
                if value.get('cache_key') == key:
                    rows.append(value)
            except (OSError, ValueError):
                continue
        return rows

    @staticmethod
    def _sum_resources(rows):
        resources = [row.get('resources', {}) for row in rows]
        return {'model_calls': sum(row.get('model_calls', 0) for row in resources),
                'prompt_tokens': sum(row.get('prompt_tokens', 0) for row in resources),
                'completion_tokens': sum(row.get('completion_tokens', 0) for row in resources),
                'total_tokens': sum(row.get('total_tokens', 0) for row in resources),
                'elapsed_seconds': round(sum(row.get('elapsed_seconds', 0) for row in resources), 4),
                'usage_complete': all(row.get('usage_complete', False) for row in resources)}

    def run(self, variants=('A', 'B', 'C', 'D'), task_ids=None, limit=None):
        self.verify(self.root, self.protocol)
        variants = list(dict.fromkeys(variants))
        if not variants or any(value not in {'A', 'B', 'C', 'D'} for value in variants):
            raise ValueError('Variants must be a nonempty subset of A/B/C/D')
        selected = [task for task in self.protocol['tasks'] if task_ids is None or task['task_id'] in set(task_ids)]
        if task_ids is not None and {task['task_id'] for task in selected} != set(task_ids):
            raise ValueError('Unknown frozen task ID')
        if limit is not None:
            if type(limit) is not int or limit < 1:
                raise ValueError('Limit must be a positive integer')
            selected = selected[:limit]
        self._check_output_identity(selected, variants)
        run_id = utc_now().replace(':', '').replace('-', '').replace('.', '_') + '_' + uuid.uuid4().hex[:6]
        report = {'run_id': run_id, 'status': 'running', 'started_at': utc_now(), 'model': self.model,
                  'pipeline_version': PIPELINE_VERSION,
                  'protocol_sha256': self.protocol['protocol_sha256'], 'variants': variants,
                  'requested': len(selected) * len(variants), 'completed': 0, 'cache_hits': 0,
                  'failed': 0, 'errors': [], 'result_paths': []}
        manifest = self.output_dir / 'runs' / run_id / 'manifest.json'
        atomic_json(manifest, report)
        total_conditions = report['requested']
        completed_conditions = 0
        retrievers = {}
        for task in selected:
            repo = task['repo']
            retriever = retrievers.setdefault(repo, self.retriever_factory(self.root, repo))
            for variant in variants:
                path, key = self._path(task, variant), result_key(self.protocol, task, variant, self.model)
                self._emit_progress(f"[{completed_conditions + 1}/{total_conditions}] "
                                    f"开始 {task['task_id']} / {variant}")
                cached = self._cached(path, key)
                if cached:
                    report['cache_hits'] += 1
                    report['completed'] += 1
                    report['result_paths'].append(path.relative_to(self.output_dir).as_posix())
                    completed_conditions += 1
                    self._emit_progress(f"[{completed_conditions}/{total_conditions}] "
                                        f"完成 {task['task_id']} / {variant}（使用缓存）")
                    continue
                session = self.session_factory(variant)
                session.on_progress = lambda message, task_id=task['task_id'], condition=variant: \
                    self._emit_progress(f"  {task_id} / {condition}：{message}")
                try:
                    self._emit_progress(f"[{completed_conditions + 1}/{total_conditions}] "
                                        f"检索证据 {task['task_id']} / {variant}")
                    packet = retriever.initial(task['issue_number'], issue_only=variant == 'A')
                    self._emit_progress(f"证据包就绪：{len(packet.get('evidence', []))}条材料；"
                                        f"开始模型分析。")
                    value = self.pipeline(packet, retriever if variant == 'D' else None,
                                          session, variant, max_rounds=2)
                    result = {**value, 'task_id': task['task_id'], 'repo': repo,
                              'issue_number': task['issue_number'], 'run_id': run_id,
                              'cache_key': key, 'protocol_sha256': self.protocol['protocol_sha256'],
                              'model': self.model, 'created_at': utc_now()}
                    receipt = {'cache_key': key, 'run_id': run_id, 'task_id': task['task_id'],
                        'variant': variant, 'status': result['status'], 'resources': session.resources(),
                        'error_type': None, 'created_at': utc_now()}
                    atomic_json(self._attempt_path(task, variant, run_id), receipt)
                    result['cumulative_resources'] = self._sum_resources(self._attempts(task, variant, key))
                    path.parent.mkdir(parents=True, exist_ok=True)
                    atomic_json(path, result)
                    if result['status'] == 'complete':
                        report['completed'] += 1
                        report['result_paths'].append(path.relative_to(self.output_dir).as_posix())
                    else:
                        report['failed'] += 1
                        report['errors'].append({'task_id': task['task_id'], 'variant': variant,
                                                 'error_type': 'PipelineFailed'})
                    completed_conditions += 1
                    self._emit_progress(f"[{completed_conditions}/{total_conditions}] "
                                        f"{result['status']} {task['task_id']} / {variant}；"
                                        f"调用{session.resources()['model_calls']}次，"
                                        f"{session.resources()['total_tokens']} tokens，"
                                        f"{session.resources()['elapsed_seconds']:.1f}秒")
                except Exception as exc:
                    receipt = {'cache_key': key, 'run_id': run_id, 'task_id': task['task_id'],
                        'variant': variant, 'status': 'failed', 'resources': session.resources(),
                        'error_type': type(exc).__name__, 'http_status': getattr(exc, 'status_code', None),
                        'created_at': utc_now()}
                    atomic_json(self._attempt_path(task, variant, run_id), receipt)
                    report['failed'] += 1
                    report['errors'].append({'task_id': task['task_id'], 'variant': variant,
                                             'error_type': type(exc).__name__,
                                             'http_status': getattr(exc, 'status_code', None)})
                    completed_conditions += 1
                    self._emit_progress(f"[{completed_conditions}/{total_conditions}] "
                                        f"失败 {task['task_id']} / {variant}：{type(exc).__name__}")
                atomic_json(manifest, report)
        report['status'] = 'complete' if not report['failed'] else 'partial'
        report['finished_at'] = utc_now()
        atomic_json(manifest, report)
        return report


def load_results(output_dir, protocol=None):
    rows = []
    for path in sorted((Path(output_dir) / 'results').glob('*/*.json')):
        value = json.loads(path.read_text(encoding='utf-8'))
        if protocol is None or value.get('protocol_sha256') == protocol['protocol_sha256']:
            rows.append(value)
    return rows


def summarize_results(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['variant']].append(row)
    result = {'status': 'operational_only_pending_human_review', 'by_variant': {}}
    for variant, values in sorted(grouped.items()):
        resources = [row.get('cumulative_resources') or row.get('resources', {}) for row in values]
        result['by_variant'][variant] = {'results': len(values),
            'completed': sum(row.get('status') == 'complete' for row in values),
            'model_calls': sum(row.get('model_calls', 0) for row in resources),
            'total_tokens': sum(row.get('total_tokens', 0) for row in resources),
            'elapsed_seconds': round(sum(row.get('elapsed_seconds', 0) for row in resources), 4),
            'abstained_results': sum(bool((row.get('gated') or {}).get('withheld_claim_ids')) for row in values),
            'reasoned_inference_claims': sum(sum(item.get('status') == 'reasoned_inference'
                for item in (row.get('audit') or {}).get('reviews', [])) for row in values),
            'stop_reasons': dict(sorted(defaultdict(int,
                {reason: sum(row.get('stop_reason') == reason for row in values)
                 for reason in {row.get('stop_reason') for row in values}}).items()))}
    return result
