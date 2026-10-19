#!/usr/bin/env python3
"""Audit local GFI research inputs without network/model access or dataset mutation."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path, PurePosixPath
import zipfile

from gfi_prototype.common import atomic_json, sha256_file, utc_now
from gfi_prototype.task_matching import DEMO_POLICY


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.exists() else []


def audit_project(folder):
    folder = Path(folder)
    receipt = json.loads((folder / 'source_snapshot.json').read_text(encoding='utf-8'))
    repo = receipt['repo']
    sha = receipt['commit_sha']
    if len(sha) != 40 or any(c not in '0123456789abcdef' for c in sha) or receipt['archive'] != f'source-{sha}.zip':
        raise ValueError('Invalid pinned archive identity')
    archive = folder / receipt['archive']
    if sha256_file(archive) != receipt['archive_sha256']:
        raise ValueError('Source archive checksum mismatch')
    rows = read_jsonl(folder / 'issues.jsonl')
    numbers = [r['issue']['number'] for r in rows]
    if not rows or len(numbers) != len(set(numbers)) or any(r.get('repo') != repo or not r.get('complete') for r in rows):
        raise ValueError('Empty, duplicate or incomplete raw records')
    with zipfile.ZipFile(archive) as z:
        paths = sorted('/'.join(PurePosixPath(i.filename).parts[1:]) for i in z.infolist()
                       if not i.is_dir() and len(PurePosixPath(i.filename).parts) > 1)
    materials = {
        'readmes': [p for p in paths if PurePosixPath(p).name.lower().startswith('readme')],
        'contribution_guides': [p for p in paths if any(s in p.lower() for s in ('contribut', 'developer', 'development')) and p.endswith(('.md', '.rst', '.txt'))],
        'dependency_manifests': [p for p in paths if PurePosixPath(p).name in ('pyproject.toml', 'setup.py', 'setup.cfg', 'environment.yml', 'environment.yaml', 'tox.ini', 'Pipfile') or PurePosixPath(p).name.startswith('requirements')],
        'ci_workflows': [p for p in paths if p.startswith('.github/workflows/')],
        'tests': [p for p in paths if ('tests' in PurePosixPath(p).parts or PurePosixPath(p).name.startswith('test_')) and p.endswith('.py')],
        'licenses': [p for p in paths if PurePosixPath(p).name.lower().startswith(('license', 'copying'))],
    }
    processed = {name: read_jsonl(folder / 'processed' / f'{name}.jsonl') for name in ('tasks', 'historical_tasks', 'non_task_records')}
    drafts = read_jsonl(folder / 'annotations/task_analysis_drafts.jsonl')
    review_path = folder / 'annotations/review_form.csv'
    reviews = []
    if review_path.exists():
        with review_path.open(encoding='utf-8-sig', newline='') as stream:
            reviews = list(csv.DictReader(stream))
    entered = sum(any((value or '').strip() for key, value in row.items() if key not in ('repo', 'issue_number', 'issue_url')) for row in reviews)
    runs = []
    for manifest in sorted((folder / 'prototype_runs').glob('*/run_manifest.json')):
        run = json.loads(manifest.read_text(encoding='utf-8'))
        if run.get('status') == 'analysis_ready_pending_human_review' and (manifest.parent / 'analysis.json').exists():
            runs.append({'path': str(manifest.parent.relative_to(folder)), 'issue_number': run['issue_number'],
                         'model': run.get('model'), 'semantic_review_status': run.get('semantic_review_status')})
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    return {'repo': repo, 'raw_issue_count': len(rows), 'raw_sha256': sha256_file(folder / 'issues.jsonl'),
            'collection_status': manifest.get('status'), 'states': dict(Counter(r['issue']['state'] for r in rows)),
            'snapshot_range': {'first': min(r['fetched_at'] for r in rows), 'last': max(r['fetched_at'] for r in rows)},
            'comment_count': sum(len(r.get('comments', [])) for r in rows),
            'timeline_event_count': sum(len(r.get('timeline', [])) for r in rows),
            'related_pr_summary_count': sum(len(r.get('related_pull_requests', [])) for r in rows),
            'source_commit': sha, 'source_archive_verified': True, 'source_archive_sha256': receipt['archive_sha256'],
            'project_materials': materials, 'material_detection_scope': 'filename heuristics, not semantic completeness',
            'processed_counts': {key: len(value) for key, value in processed.items()},
            'availability_counts': dict(Counter(r['availability'] for r in processed['tasks'])),
            'draft_count': len(drafts), 'review_form_rows_with_content': entered,
            'model_analysis_run_count': len(runs), 'model_runs': runs,
            'closed_implies_success': False, 'current_availability_verified': False}


def run_audit(root):
    root = root.resolve()
    projects = []
    for folder in sorted((root / 'data').iterdir()):
        if not folder.is_dir():
            continue
        has_issues = (folder / 'issues.jsonl').exists()
        has_snapshot = (folder / 'source_snapshot.json').exists()
        if has_issues != has_snapshot:
            raise ValueError(f'Incomplete project dataset: {folder.name}; issues.jsonl and source_snapshot.json both required')
        if has_issues:
            projects.append(audit_project(folder))
    if not projects:
        raise ValueError('No project datasets found')
    totals = {key: sum(p[key] for p in projects) for key in ('raw_issue_count', 'comment_count', 'timeline_event_count',
              'related_pr_summary_count', 'draft_count', 'review_form_rows_with_content', 'model_analysis_run_count')}
    report = {'schema_version': 1, 'checked_at': utc_now(), 'scope': 'project_and_gfi_demo',
              'demo_policy': dict(DEMO_POLICY),
              'network_calls': 0, 'model_calls': 0, 'totals': totals, 'projects': projects,
              'gaps': [
                  {'priority': 'deferred_for_demo', 'item': 'Current issue/assignment/PR status', 'action': 'Demo assumes all collected GFIs open and unresolved. Do not refresh or filter by state, assignment, or PR. Original statuses remain in source records.'},
                  {'priority': 'optional_not_demo_blocker', 'item': 'Related PR evidence', 'action': f"{totals['related_pr_summary_count']} extracted PR summaries in current dataset. No supplementation required for the technology-matching demo; zero does not imply no PR exists. Collect solution evidence only for later real-world evaluation."},
                  {'priority': 'P1', 'item': 'Project context extraction', 'action': 'README, contribution/dependency/test/CI materials exist in archives; extract a small verified project context instead of redownloading repositories.'},
                  {'priority': 'P1', 'item': 'Task-level model outputs', 'action': f"{totals['model_analysis_run_count']} real analysis runs counted from success manifests and analysis files, not semantic validation. Ensure versioned technology stack, suitable background, newcomer-stage rationale and starting steps, then analyze a development batch."},
                  {'priority': 'P1', 'item': 'Task-to-task relations', 'action': 'Use recommend_gfi.py to compare normalized requirement technologies within each project. Outputs are demo technology similarities; extend analyzed-task coverage as needed. Independent relation labels are optional for the demo.'},
                  {'priority': 'P1', 'item': 'Independent evaluation', 'action': 'Review task claims and successor relations independently; reserve held-out tasks before tuning. Existing assistant drafts are not gold labels.'},
                  {'priority': 'P2', 'item': 'Selected historical solution evidence', 'action': 'If evaluating solved examples, collect verified solving PR diff/tests/reviews and pre-fix source; closed alone is not success.'}
              ]}
    lines = ['# 项目与GFI数据审计', '',
             '范围为项目/GFI分析与同项目技术栈匹配，不采集或构建个人画像。本报告仅检查本地数据。', '',
             '**Demo假设：全部GFI视为开放、未解决；不因关闭、指派或PR状态过滤，也不要求补查实时状态。下表开放/关闭列是原始采集事实，不是演示过滤条件。**', '',
             '| 项目 | 原始Issue | 开放/关闭 | 开放具体任务 | 助手草稿 | 真实模型运行 | PR摘要 |',
             '| --- | ---: | --- | ---: | ---: | ---: | ---: |']
    for p in projects:
        lines.append(f"| {p['repo']} | {p['raw_issue_count']} | {p['states'].get('open', 0)}/{p['states'].get('closed', 0)} | {p['processed_counts']['tasks']} | {p['draft_count']} | {p['model_analysis_run_count']} | {p['related_pr_summary_count']} |")
    lines += ['', f"合计{totals['raw_issue_count']}条原始Issue、{totals['comment_count']}条评论、{totals['timeline_event_count']}条timeline事件。",
              '归档SHA256全部核验通过；这是采集时间窗口的快照，不是历史修复前的版本。',
              f"有填写内容的任务复核表记录为{totals['review_form_rows_with_content']}；填写本身不代表完成独立审核。", '',
              '## 已有项目资料', '', '以下按文件名发现，存在不等于已抽取或足以回答所有环境问题。', '']
    for p in projects:
        lines += [f"### {p['repo']}", '']
        for key, paths in p['project_materials'].items():
            lines.append(f"- {key}：{len(paths)}项；示例：" + '、'.join(paths[:3]))
        lines.append('')
    lines += ['## 补充优先级', '',
              f"Demo无需补查当前状态与PR。当前提取的PR摘要有{totals['related_pr_summary_count']}条，仅保留为数据事实，不阻塞技术栈匹配。", '',
              '优先完善任务技术标签和任务分析覆盖；现有40条草稿已可通过recommend_gfi.py生成同项目技术栈匹配。项目环境、适合背景和新手阶段可在后续扩展。', '',
              '独立人工评测/留出集、PR diff/review与历史版本只在后续研究或实际可接手推荐中补充，不作为当前Demo前置条件。', '',
              '## 不需要扩张的内容', '',
              '不需要个人画像、GitHub个人历史、用户能力评分或个性化匹配数据。当前不需要增加仓库数量、全Git历史、全贡献者数据或复杂向量库。',
              '关闭历史任务仍有对照价值，不能删除或直接标为成功样本。源码ZIP与解压树目前分别服务可验证检索与草稿校验，均保留。', '',
              '现有Demo使用助手任务草稿+规则归一化，不冒称新的模型分析。新版模型字段和界面尚未实现，当前状态刷新按需求不启用。']
    atomic_json(root / 'data/data_audit.json', report)
    (root / 'data/DATA_AUDIT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    report = run_audit(args.root)
    print(json.dumps(report['totals']))


if __name__ == '__main__':
    main()
