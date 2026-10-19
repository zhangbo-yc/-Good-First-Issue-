#!/usr/bin/env python3
"""Generate the full Chinese catalog through DeepSeek; cache and resume safely."""
import argparse
import json
import os
from pathlib import Path

from gfi_prototype.newcomer_content import DeepSeekContentClient, build_content, load_records
from gfi_prototype.settings import load_project_env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--workers', type=int, choices=range(1, 9), default=4)
    parser.add_argument('--batch-size', type=int, choices=range(1, 9), default=6)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    root = args.root.resolve()
    load_project_env(root)
    key = os.environ.get('DEEPSEEK_API_KEY', '').strip()
    if not key:
        raise SystemExit('Configure DEEPSEEK_API_KEY in .env before generating descriptions.')
    records = load_records(root)
    if args.limit is not None:
        records = records[:args.limit]
    client = DeepSeekContentClient(key, os.environ.get('DEEPSEEK_MODEL', 'deepseek-flash'))
    print(json.dumps({'provider': 'DeepSeek', 'issues': len(records), 'workers': args.workers}), flush=True)
    report = build_content(root, records, client, workers=args.workers, batch_size=args.batch_size,
        progress=lambda row: print(json.dumps(row), flush=True))
    print(json.dumps({key: report[key] for key in ('status', 'requested', 'cache_hits', 'completed', 'failed')}), flush=True)
    return 0 if report['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
