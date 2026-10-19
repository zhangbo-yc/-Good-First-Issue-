#!/usr/bin/env python3
"""Upgrade the 50 existing example guides to evidence-backed DeepSeek v2 analyses."""
import argparse
import json
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from batch_analyze import run_batch
from gfi_prototype import artifacts
from gfi_prototype.common import atomic_json, utc_now
from gfi_prototype.settings import load_project_env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, choices=range(1, 6), default=3)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    load_project_env(root)
    key = os.environ.get('DEEPSEEK_API_KEY', '').strip()
    if not key:
        raise SystemExit('Configure DEEPSEEK_API_KEY in .env first.')
    _, profiles, _, _ = artifacts.latest_demo_data(root)
    grouped = defaultdict(list)
    for profile in profiles[:args.limit]:
        grouped[profile['repo']].append({'repo': profile['repo'], 'issue': profile['issue_number']})
    from langchain_deepseek import ChatDeepSeek
    from langchain_core.runnables import RunnableLambda
    from pydantic import SecretStr
    model_name = os.environ.get('DEEPSEEK_MODEL', 'deepseek-flash')

    def factory():
        model = ChatDeepSeek(model=model_name, api_key=SecretStr(key), base_url='https://api.deepseek.com',
                            temperature=0, max_tokens=6000, max_retries=0, timeout=100,
                            extra_body={'thinking': {'type': 'disabled'}}).bind(response_format={'type': 'json_object'})
        return RunnableLambda(lambda prompt: model.invoke(prompt))

    def one_project(tasks):
        rows = []
        for task in tasks:
            row = run_batch(root, [task], factory, model=model_name, max_attempts=2, secret=key)[0]
            print(json.dumps({'repo': task['repo'], 'issue': task['issue'], 'status': row['status'],
                              'error_type': row.get('error_type')}), flush=True)
            rows.append(row)
        return rows

    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(one_project, tasks) for tasks in grouped.values()]
        for future in as_completed(futures):
            rows.extend(future.result())
    report = {'provider': 'DeepSeek', 'model': model_name, 'finished_at': utc_now(),
              'completed': sum(row['status'] in {'completed', 'cache_hit'} for row in rows),
              'total': len(rows), 'rows': rows}
    atomic_json(root / 'data/newcomer_deep_guides_report.json', report)
    print(json.dumps({'completed': report['completed'], 'total': report['total']}), flush=True)
    return 0 if report['completed'] == report['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
