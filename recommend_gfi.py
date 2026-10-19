#!/usr/bin/env python3
"""Demo: assume all collected GFIs open/unresolved; match same-project task technologies."""
import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
import uuid

from gfi_prototype import analysis_cache
from gfi_prototype.common import atomic_json, safe_text
from gfi_prototype.origins import origin_label
from gfi_prototype.task_matching import demo_record, technology_profile, match_tasks, match_cache_key


def _jsonl_from(read, path):
    return [json.loads(line) for line in read(path).decode('utf-8').splitlines() if line.strip()]


def _load_project_drafts(folder, config, snapshot, by_id, read):
    drafts_path = folder / 'annotations/task_analysis_drafts.jsonl'
    if not drafts_path.exists():
        return []
    profiles = []
    draft_ids = set()
    for draft in _jsonl_from(read, drafts_path):
        key = (draft['repo'], draft['issue_number'])
        if key in draft_ids or key not in by_id:
            raise ValueError('Duplicate task draft or missing raw issue')
        draft_ids.add(key)
        if by_id[key]['record_type'] != 'task':
            continue
        if draft['source_commit'] != snapshot['commit_sha']:
            raise ValueError('Draft source version differs from project snapshot')
        profile = technology_profile(draft, config['rules'])
        profile['state_at_snapshot'] = by_id[key]['state_at_snapshot']
        profile['record_type'] = 'task'
        profile['available_origins'] = [profile['analysis_origin']]
        profiles.append(profile)
    return profiles


def _merge_cached_profiles(folder, config, by_id, kinds, read, profiles):
    cache_path = folder / 'analysis_cache.json'
    if not cache_path.exists():
        return
    for task_id, _ in json.loads(read(cache_path)).items():
        repo, number_text = task_id.rsplit('#', 1)
        key = (repo, int(number_text))
        if key not in by_id or key not in kinds:
            raise ValueError('Cached analysis has no raw issue or triage record')
        model_profile = analysis_cache.profile_from_cached_analysis(folder, task_id, config['rules'])
        model_profile['record_type'] = kinds[key]
        model_profile['state_at_snapshot'] = by_id[key]['state_at_snapshot']
        existing = next((profile for profile in profiles if profile['task_id'] == task_id), None)
        if existing is not None:
            model_profile['available_origins'] = [existing['analysis_origin'], model_profile['analysis_origin']]
            profiles[:] = [profile for profile in profiles if profile['task_id'] != task_id] + [model_profile]
        else:
            model_profile['available_origins'] = [model_profile['analysis_origin']]
            profiles.append(model_profile)


def load_demo_inputs(root):
    captured = {}

    def read(path):
        path = path.resolve()
        if not path.is_relative_to(root):
            raise ValueError('Dataset path escapes root')
        if path not in captured:
            captured[path] = path.read_bytes()
        return captured[path]

    config = json.loads(read(root / 'technology_rules.json'))
    catalog, profiles = [], []
    seen = set()
    for raw_path in sorted((root / 'data').glob('*/issues.jsonl')):
        folder = raw_path.parent
        raw = _jsonl_from(read, raw_path)
        triage = _jsonl_from(read, folder / 'processed/triage.jsonl')
        kinds = {(row['repo'], row['number']): row['record_type'] for row in triage}
        if len(kinds) != len(triage):
            raise ValueError('Duplicate triage identity')
        by_id = {}
        for bundle in raw:
            key = (bundle['repo'], bundle['issue']['number'])
            if key in seen or not bundle.get('complete') or key not in kinds:
                raise ValueError('Duplicate, incomplete or unclassified raw issue')
            seen.add(key)
            item = demo_record(bundle, kinds[key])
            catalog.append(item)
            by_id[key] = item
        if set(kinds) != set(by_id):
            raise ValueError('Raw and triage issue sets differ')
        snapshot = json.loads(read(folder / 'source_snapshot.json'))
        profiles.extend(_load_project_drafts(folder, config, snapshot, by_id, read))
        _merge_cached_profiles(folder, config, by_id, kinds, read, profiles)
    if not catalog or not profiles:
        raise ValueError('No raw dataset or analyzed tasks')
    hashes = {path.relative_to(root).as_posix(): hashlib.sha256(raw).hexdigest() for path, raw in captured.items()}
    return catalog, profiles, config, hashes


def verify_inputs(root, hashes):
    if any(hashlib.sha256((root / path).read_bytes()).hexdigest() != expected for path, expected in hashes.items()):
        raise ValueError('Input changed during demo generation')


def write_jsonl(path, rows):
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')


def generate_demo(root, repo, issue, top_k=3):
    root = root.resolve()
    catalog, profiles, config, hashes = load_demo_inputs(root)
    source = next((p for p in profiles if p['repo'] == repo and p['issue_number'] == issue), None)
    if source is None:
        raise ValueError('This issue has no task analysis yet; choose an analyzed task or add its analysis first')
    origin_by_task = {p['task_id']: p['analysis_origin'] for p in profiles}

    def decorate(rows, origin):
        for row in rows:
            row['analysis_origin'] = origin
            row['candidate_analysis_origin'] = origin_by_task[row['task_id']]
        return rows

    pair_caches = {}
    dirty_caches = set()

    def cached_pair(source_profile, candidate_profile):
        folder = root / 'data' / source_profile['repo'].replace('/', '__')
        cache_path = folder / 'match_cache.json'
        if cache_path not in pair_caches:
            pair_caches[cache_path] = json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
        cache = pair_caches[cache_path]
        key = match_cache_key(source_profile, candidate_profile, config['rule_version'], top_k)
        if key not in cache:
            cache[key] = match_tasks(source_profile, [candidate_profile], top_k)
            dirty_caches.add(cache_path)
        return cache[key]

    def all_pairs(profile):
        rows = []
        for candidate in profiles:
            if (candidate['repo'] != profile['repo'] or candidate['task_id'] == profile['task_id']
                    or candidate.get('record_type', 'task') != 'task'):
                continue
            rows.extend(dict(row) for row in cached_pair(profile, candidate))
        return sorted(rows, key=lambda row: (-float(row['similarity_score']),
                                             -int(row.get('specific_shared_count', 0)), row['task_id']))

    matches = match_tasks(source, profiles, top_k)
    decorate(matches, source['analysis_origin'])
    all_matches = [{'source_task_id': p['task_id'],
                    'recommendations': decorate(all_pairs(p), p['analysis_origin']),
                    'demo_assumption': True} for p in profiles]
    verify_inputs(root, hashes)
    for cache_path in dirty_caches:
        atomic_json(cache_path, pair_caches[cache_path])
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid.uuid4().hex[:6]
    output = root / 'data/demo_runs' / run_id
    output.mkdir(parents=True, exist_ok=False)
    write_jsonl(output / 'demo_catalog.jsonl', catalog)
    write_jsonl(output / 'task_technology_profiles.jsonl', profiles)
    write_jsonl(output / 'all_task_matches.jsonl', all_matches)
    result = {'source_task': source, 'recommendations': matches, 'demo_assumption': True,
              'assumption': 'All collected GFIs are treated as open and unresolved for the demo only; raw state is preserved.',
              'status_used_for_filtering': False, 'current_availability_verified': False,
              'matching_method': 'weighted_jaccard_on_rule_normalized_task_requirements',
              'analysis_origin': source['analysis_origin']}
    atomic_json(output / 'recommendations.json', result)
    concrete_count = sum(p['record_type'] == 'task' for p in catalog)
    manifest = {'status': 'complete_demo', 'created_at': datetime.now(timezone.utc).isoformat(),
                'demo_assumption': True, 'status_policy': 'assume_all_open_unresolved_ignore_assignment_and_PR',
                'source_task_id': source['task_id'], 'demo_record_count': len(catalog),
                'concrete_task_count': concrete_count, 'profiled_task_count': len(profiles),
                'unprofiled_concrete_task_count': concrete_count - len(profiles),
                'selected_recommendation_count': len(matches),
                'all_recommendation_count': sum(len(p['recommendations']) for p in all_matches),
                'top_k': top_k, 'rule_version': config['rule_version'], 'model_calls': 0, 'network_calls': 0,
                'is_ground_truth': False, 'input_sha256': hashes,
                'code_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                [Path(__file__), Path(__file__).parent / 'gfi_prototype/task_matching.py']}}
    lines = ['# GFI技术栈匹配Demo', '',
             '**演示假设：所有已采集GFI统一视为开放、未解决，关闭/指派/PR不参与过滤；原始状态不改写。**', '',
             f"原始记录{len(catalog)}条，具体任务{concrete_count}条；当前只有{len(profiles)}条具备已有分析与技术标签，匹配范围不等于全部原始记录。", '',
             '本次使用既有助手草稿和规则归一化，没有新调用模型。相似度只描述技术标签重合，不代表成功概率或验证过的学习进阶。', '',
             f"## 起点：{safe_text(source['task_id'])}", '', safe_text(source['title']), '', safe_text(source['goal']), '',
             '技术栈/知识标签：' + '、'.join(t['name'] for t in source['technology_tags']), '',
             '适合背景：' + safe_text(source['suitable_background']), '',
             '分析来源：' + origin_label(source['analysis_origin']), '', '## 同项目候选', '']
    if not matches:
        lines += ['没有技术标签交集的候选，未补造推荐。', '']
    for match in matches:
        lines += [f"### {match['rank']}. {safe_text(match['task_id'])}", '', safe_text(match['title']), '',
                  'Issue：' + safe_text(match['url']), '', safe_text(match['goal']), '',
                  f"技术标签相似度：{match['similarity_score']:.4f}；匹配类型：{match['match_strength']}。", '',
                  '共同技术：' + '、'.join(t['name'] for t in match['shared_technologies']),
                  '新增技术：' + ('、'.join(t['name'] for t in match['additional_technologies']) or '规则未发现新增标签；不表示没有新的任务细节'), '',
                  '共同技术的双侧证据：', '']
        for tag in match['shared_technologies']:
            lines.append('- ' + tag['name'])
            for label, refs in [('A', tag['source_evidence']), ('B', tag['candidate_evidence'])]:
                for ref in refs:
                    lines.append(f"  - {label}:{ref['id']}：{safe_text(ref['url'])}")
        lines += ['', '分析来源：' + origin_label(match['analysis_origin'])
                  + '；候选来源：' + origin_label(match['candidate_analysis_origin']), '',
                  '候选所需背景：' + safe_text(match['suitable_background']), '']
    (output / 'DEMO_REPORT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    verify_inputs(root, hashes)
    atomic_json(output / 'run_manifest.json', manifest)
    return output, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--repo', default='ManimCommunity/manim')
    parser.add_argument('--issue', type=int, default=4983)
    parser.add_argument('--top-k', type=int, choices=range(1, 11), default=3)
    args = parser.parse_args()
    output, manifest = generate_demo(args.root, args.repo, args.issue, args.top_k)
    print(json.dumps({'output': str(output), 'profiled_tasks': manifest['profiled_task_count'],
                      'recommendations': manifest['selected_recommendation_count'], 'model_calls': 0}))


if __name__ == '__main__':
    main()
