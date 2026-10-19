#!/usr/bin/env python3
"""Run bounded v2 model analyses over a fixed task list, with cache hits and failure records."""
import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from gfi_prototype import analysis_cache
from gfi_prototype.analysis_v2 import PROMPT_VERSION, SCHEMA_VERSION, analyze_packet_v2, write_report_v2
from gfi_prototype.common import atomic_json, read_token, sha256_file, sha256_text, utc_now
from gfi_prototype.retrieval import EvidenceStore
from gfi_prototype.settings import load_project_env

REPOSITORIES = ('sympy/sympy', 'napari/napari', 'dask/dask', 'ManimCommunity/manim', 'ray-project/ray')
BASE_URL = 'https://api.deepseek.com'


def _validate_tasks(tasks):
    seen = set()
    for task in tasks:
        repo, issue = task.get('repo'), task.get('issue')
        if repo not in REPOSITORIES:
            raise ValueError(f'Unknown repository {repo!r}; batch tasks are limited to the five collected projects')
        if type(issue) is not int or issue <= 0:
            raise ValueError('Batch task issues must be positive integers')
        if (repo, issue) in seen:
            raise ValueError('Duplicate batch task')
        seen.add((repo, issue))


def _entry_matches_key(entry, key):
    return all(entry.get(field) == key[field]
               for field in ('issue_content_sha256', 'source_commit', 'model', 'prompt_version', 'schema_version'))


def _rules_hash(root):
    rules = json.loads((root / 'technology_rules.json').read_text(encoding='utf-8'))['rules']
    return sha256_text(json.dumps(rules, sort_keys=True, ensure_ascii=False))


def run_batch(root, tasks, model_factory, *, model='deepseek-flash', max_attempts=2, dry_run=False,
              limit=None, secret=None):
    _validate_tasks(tasks)
    rows = []
    for task in tasks[:limit]:
        repo, number = task['repo'], task['issue']
        folder = root / 'data' / repo.replace('/', '__')
        task_id = f'{repo}#{number}'
        row = {'repo': repo, 'issue': number, 'status': 'failed', 'run_dir': None,
               'attempts': 0, 'error_type': None}
        try:
            key = analysis_cache.compute_analysis_key(folder, number, model, PROMPT_VERSION, SCHEMA_VERSION)
            entries = analysis_cache.read_analysis_cache(folder)
            entry = entries.get(task_id)
            if entry and _entry_matches_key(entry, key):
                row.update(status='cache_hit', run_dir=entry.get('run_dir'))
                rows.append(row)
                continue
            if dry_run:
                row['status'] = 'cache_miss'
                rows.append(row)
                continue
            store = EvidenceStore(folder)
            store.build_index()
            packet = store.evidence_bundle(number)
            if secret and secret in json.dumps(packet):
                raise ValueError('Credential detected in evidence packet; refusing model call')
            callable_model = model_factory()
            run_id = (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + str(number)
                      + '_batch_' + uuid.uuid4().hex[:6])
            output = folder / 'prototype_runs' / run_id
            output.mkdir(parents=True, exist_ok=False)
            result = analyze_packet_v2(packet, callable_model, max_attempts)
            atomic_json(output / 'evidence_bundle.json', packet)
            atomic_json(output / 'analysis.json', result)
            write_report_v2(output, packet, result)
            manifest = {'started_at': utc_now(), 'finished_at': utc_now(), 'repo': repo,
                        'issue_number': number, 'status': 'analysis_ready_pending_human_review',
                        'schema_version': SCHEMA_VERSION, 'prompt_version': PROMPT_VERSION, 'model': model,
                        'source_commit': key['source_commit'], 'issue_content_sha256': key['issue_content_sha256'],
                        'live_model_calls_started': result['attempts'],
                        'semantic_review_status': 'pending_human_review', 'is_ground_truth': False,
                        'current_availability_verified': False, 'output_directory': str(output)}
            atomic_json(output / 'run_manifest.json', manifest)
            entry = {'run_dir': output.relative_to(folder).as_posix(), 'schema_version': SCHEMA_VERSION,
                     'prompt_version': result.get('prompt_version', PROMPT_VERSION),
                     'model': model, 'issue_content_sha256': key['issue_content_sha256'],
                     'source_commit': key['source_commit'], 'retrieval_version': key['retrieval_version'],
                     'analysis_sha256': sha256_file(output / 'analysis.json'),
                     'rules_sha256': _rules_hash(root), 'profile_ready': True}
            entries[task_id] = entry
            analysis_cache.write_analysis_cache(folder, entries)
            row.update(status='completed', run_dir=run_id, attempts=result['attempts'])
        except Exception as exc:
            row['error_type'] = type(exc).__name__
            row['http_status'] = getattr(exc, 'status_code', None)
        rows.append(row)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-file', default='batch_tasks.json')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--prompt-key', action='store_true')
    parser.add_argument('--model', default='deepseek-flash')
    parser.add_argument('--max-attempts', type=int, choices=[1, 2], default=2)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--limit', type=int, default=None)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    load_project_env(root)
    tasks = json.loads((root / args.task_file).read_text(encoding='utf-8'))
    secret = None
    if not args.dry_run:
        key = read_token('DeepSeek API key (hidden): ') if args.prompt_key else os.environ.get('DEEPSEEK_API_KEY', '').strip()
        if not key:
            raise ValueError('Use --prompt-key or set DEEPSEEK_API_KEY')
        if any(ord(char) < 33 or ord(char) > 126 for char in key):
            raise ValueError('API key contains invalid characters')
        secret = key
    from langchain_core.runnables import RunnableLambda
    from langchain_deepseek import ChatDeepSeek
    from pydantic import SecretStr

    def factory():
        model_instance = ChatDeepSeek(model=args.model, api_key=SecretStr(secret or ''), base_url=BASE_URL,
                                     temperature=0, max_tokens=4096, max_retries=0, timeout=90,
                                     extra_body={'thinking': {'type': 'disabled'}}).bind(
            response_format={'type': 'json_object'})
        return RunnableLambda(lambda prompt: model_instance.invoke(prompt))

    rows = run_batch(root, tasks, factory, model=args.model, max_attempts=args.max_attempts,
                     dry_run=args.dry_run, limit=args.limit, secret=secret)
    run_dir = root / 'data' / 'batch_runs' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / 'summary.jsonl').write_text(
        ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
    print(json.dumps({'summary': str(run_dir / 'summary.jsonl'),
                      'rows': [row['status'] for row in rows]}, ensure_ascii=True), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
