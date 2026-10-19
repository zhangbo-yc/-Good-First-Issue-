#!/usr/bin/env python3
"""Collect Dask/Manim source and all current GFI-labelled issues, then audit labels."""
import collections
import csv
import json
import re
import sys
from pathlib import Path

import collect_gfi as core


PROJECTS = ({'repo': 'dask/dask'}, {'repo': 'ManimCommunity/manim'})


def is_gfi_label(name):
    return re.sub(r'[-\s]+', ' ', name.strip()).casefold() == 'good first issue'


def issue_has_gfi(issue):
    return any(is_gfi_label(label['name']) for label in issue.get('labels', []))


def resolve_gfi_labels(client, repo):
    labels = client.pages(f'/repos/{repo}/labels', {'per_page': 100})
    matches = sorted({label['name'] for label in labels if is_gfi_label(label['name'])})
    if not matches:
        raise ValueError(f'{repo}: no exact good-first-issue / good first issue label found')
    return matches


def audit_project(folder, repo, live_issues, *, limited=False):
    folder = Path(folder)
    records = [json.loads(line) for line in (folder/'issues.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    issues = [record['issue'] for record in records]
    numbers = [issue['number'] for issue in issues]
    live_by_number = {issue['number']: issue for issue in live_issues if 'pull_request' not in issue and issue_has_gfi(issue)}
    non_gfi = sorted(issue['number'] for issue in issues if not issue_has_gfi(issue))
    prs = sorted(issue['number'] for issue in issues if 'pull_request' in issue)
    duplicate_numbers = sorted(number for number, count in collections.Counter(numbers).items() if count > 1)
    identity_errors = sorted(record['issue']['number'] for record in records
                             if record.get('repo') != repo or not record.get('complete'))
    live_invalid = sorted(issue['number'] for issue in live_issues if 'pull_request' not in issue and not issue_has_gfi(issue))
    individual_non_gfi, file_mismatches, individual_prs = [], [], []
    exported = {record['issue']['number']: record for record in records}
    individual_numbers = set()
    for path in (folder/'issues').glob('*.json'):
        record = json.loads(path.read_text(encoding='utf-8'))
        issue = record['issue']
        number = issue['number']
        individual_numbers.add(number)
        if path.stem != str(number) or record.get('repo') != repo or not record.get('complete'):
            file_mismatches.append(number)
        if not issue_has_gfi(issue):
            individual_non_gfi.append(number)
        if 'pull_request' in issue:
            individual_prs.append(number)
        if number in exported and record != exported[number]:
            file_mismatches.append(number)
    file_mismatches.extend(set(numbers)-individual_numbers)
    missing = [] if limited else sorted(set(live_by_number)-set(numbers))
    extra = sorted(set(numbers)-set(live_by_number))
    violations = [non_gfi, prs, duplicate_numbers, identity_errors, live_invalid,
                  individual_non_gfi, individual_prs, file_mismatches, missing, extra]
    report = {'repo': repo, 'checked_at': core.utc_now(), 'status': 'failed' if any(violations) else 'passed',
              'label_rule': 'Exact case-insensitive good first issue / good-first-issue; preserve original spelling',
              'local_issue_count': len(issues), 'individual_file_count': len(individual_numbers),
              'live_label_query_count': len(live_by_number), 'limited_run': limited,
              'state_counts': dict(collections.Counter(issue['state'] for issue in issues)),
              'snapshot_non_gfi_numbers': non_gfi, 'pull_request_numbers': prs,
              'duplicate_numbers': duplicate_numbers, 'identity_errors': identity_errors,
              'live_query_non_gfi_numbers': live_invalid,
              'individual_non_gfi_numbers': sorted(set(individual_non_gfi)),
              'individual_pull_request_numbers': sorted(set(individual_prs)),
              'individual_file_mismatches': sorted(set(file_mismatches)),
              'missing_from_export': missing, 'not_in_live_label_query': extra,
              'historical_files_outside_current_export': sorted(individual_numbers-set(numbers)),
              'scope_note': 'Current labels only; previously removed labels cannot be reconstructed by this query.'}
    core.atomic_json(folder/'label_audit.json', report)
    with (folder/'label_audit.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['number', 'state', 'labels', 'has_gfi_in_saved_record',
                                                  'has_gfi_in_fresh_query', 'url'])
        writer.writeheader()
        for issue in issues:
            writer.writerow({'number': issue['number'], 'state': issue['state'],
                             'labels': '; '.join(label['name'] for label in issue.get('labels', [])),
                             'has_gfi_in_saved_record': issue_has_gfi(issue),
                             'has_gfi_in_fresh_query': issue['number'] in live_by_number, 'url': issue['html_url']})
    lines = ['# GFI标签核验：'+repo, '', f"检查时间：{report['checked_at']}",
             f"结果：{report['status']}", '',
             f"- 本次汇总Issue：{len(issues)}条；核验所有单条文件：{len(individual_numbers)}个。",
             f"- 结束后重新分页查询到的GFI Issue：{len(live_by_number)}条。",
             '- 状态分布：'+json.dumps(report['state_counts'], ensure_ascii=False),
             f'- 汇总中未带GFI标签：{non_gfi}', f'- 单条文件中未带GFI标签：{report["individual_non_gfi_numbers"]}',
             f'- 混入的PR：{prs}', f'- 重复编号：{duplicate_numbers}',
             f'- 当前GFI查询中存在、汇总中缺失：{missing}', f'- 汇总中存在、当前GFI查询中不存在：{extra}',
             f'- 单条文件与汇总不一致：{report["individual_file_mismatches"]}', '',
             '通过表示保存记录具有GFI标签，且本次汇总与采集结束后查询的标签集合一致。',
             '标签可随维护者操作变化；检查是时间窗口内的观测，不是事务一致快照。',
             '不会将仅带help wanted、easy等其他标签的Issue算作GFI。']
    (folder/'LABEL_AUDIT.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return report


def collect_and_audit(project, client, output, state, source, refresh, limit):
    repo = project['repo']
    folder = Path(output)/repo.replace('/', '__')
    folder.mkdir(parents=True, exist_ok=True)
    try:
        labels = resolve_gfi_labels(client, repo)
        core.log(f'[{repo}] Actual GFI labels: {labels}; state={state}')
        result = core.collect_project({**project, 'labels': labels}, client, output, state, source, refresh, limit,
                                      issue_workers=4)
        if not (folder/'issues.jsonl').exists():
            return result
        core.log(f'[{repo}] Rechecking complete GFI label list for final audit')
        current = {}
        for label in labels:
            issues = client.pages(f'/repos/{repo}/issues', {'state': state, 'labels': label,
                                   'per_page': 100, 'sort': 'created', 'direction': 'asc'})
            for issue in issues:
                if 'pull_request' not in issue:
                    current[issue['number']] = issue
        core.atomic_json(folder/'raw/label_verification_snapshot.json',
                         {'repo': repo, 'labels': labels, 'state': state, 'checked_at': core.utc_now(), 'items': list(current.values())})
        audit = audit_project(folder, repo, list(current.values()), limited=limit is not None)
        result['label_audit'] = audit
        if audit['status'] != 'passed':
            result['status'] = 'partial'
            result['errors'].append({'stage': 'label_audit', 'error': 'Label integrity or live completeness check failed'})
        result['finished_at'] = core.utc_now()
        core.atomic_json(folder/'manifest.json', result)
        core.log(f'[{repo}] Label audit {audit["status"]}: {audit["local_issue_count"]} issues')
        return result
    except Exception as exc:
        result = core.read_json(folder/'manifest.json') or {'repo': repo, 'errors': []}
        result['status'] = 'partial'
        result['errors'].append({'stage': 'collection_or_audit', 'error': core.safe_error(client, exc)})
        core.atomic_json(folder/'manifest.json', result)
        core.log(f'[{repo}] Collection/audit failed; see manifest.json')
        return result


def main(argv=None):
    return core.main(argv, projects=PROJECTS, project_runner=collect_and_audit, default_state='all',
                     summary_name='collection_summary_dask_manim.json', description=__doc__)


if __name__ == '__main__':
    sys.exit(main())
