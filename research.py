#!/usr/bin/env python3
"""Frozen ablation, directional recommendation and blinded review workflow."""
import argparse
import json
import os
from pathlib import Path

from gfi_prototype.common import atomic_json, sha256_text, utc_now
from gfi_prototype.research.client import DeepSeekClient, ResearchSession
from gfi_prototype.research.evaluation import (build_recommendation_review_bundle,
    build_review_bundle, compare_recommendation_methods, compare_variant_scores,
    score_recommendation_reviews, score_reviews)
from gfi_prototype.research.pipeline import PIPELINE_VERSION
from gfi_prototype.research.protocol import freeze_protocol, verify_protocol
from gfi_prototype.research.recommendations import (audit_candidate_profiles,
    load_candidate_profiles, profile_from_result, run_recommendation_methods)
from gfi_prototype.research.runner import ExperimentRunner, load_results, summarize_results
from gfi_prototype.settings import load_project_env


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def configured_model(root):
    load_project_env(root)
    return os.environ.get('DEEPSEEK_MODEL', 'deepseek-flash')


def review_paths(output, results, recommendations):
    analysis_inventory = ['blind_analysis_v2', *sorted((row.get('task_id'), row.get('variant'), row.get('cache_key'),
                                 row.get('status')) for row in results)
                         ]
    recommendation_inventory = ['blind_recommendation_v2', *sorted((row.get('method'), row.get('source_task_id'),
        tuple(item['task_id'] if isinstance(item, dict) else item for item in row.get('recommendations', [])))
        for row in recommendations)]
    analysis_hash = sha256_text(json.dumps(analysis_inventory, ensure_ascii=False, separators=(',', ':')))[:12]
    recommendation_hash = sha256_text(json.dumps(recommendation_inventory, ensure_ascii=False,
                                                 separators=(',', ':')))[:12]
    return {'analysis': Path(output) / f'review_{analysis_hash}',
            'recommendation': Path(output) / f'recommendation_review_{recommendation_hash}',
            'analysis_inventory_sha256': analysis_hash, 'recommendation_inventory_sha256': recommendation_hash}


def client_and_session(root, variant=None):
    load_project_env(root)
    key = os.environ.get('DEEPSEEK_API_KEY', '').strip()
    model = os.environ.get('DEEPSEEK_MODEL', 'deepseek-flash')
    client = DeepSeekClient(key, model)
    calls = {'A': 2, 'B': 2, 'C': 4, 'D': 9, None: 2}[variant]
    return ResearchSession(client, max_calls=calls, max_context_chars=60000,
                           max_reported_tokens=100000, attempts=2)


def write_operational_report(path, protocol, results, reviews=None, recommendation_reviews=None,
                             variant_comparison=None, recommendation_comparison=None,
                             candidate_audit_resources=None):
    operational = summarize_results(results)
    report = {'generated_at': utc_now(), 'protocol_sha256': protocol['protocol_sha256'],
              'pipeline_version': PIPELINE_VERSION, 'operational': operational,
              'human_evaluation': reviews or {'status': 'pending'},
              'recommendation_evaluation': recommendation_reviews or {'status': 'pending'},
              'variant_comparison': variant_comparison or {'status': 'pending'},
              'recommendation_comparison': recommendation_comparison or {'status': 'pending'},
              'candidate_audit_resources': candidate_audit_resources or {'status': 'pending'},
              'claim': 'No effectiveness claim is made until independent gold and two reviews are complete.'}
    atomic_json(path, report)
    lines = ['# 科研实验报告', '', f"冻结协议：`{protocol['protocol_sha256']}`", '',
             '## 运行资源', '', '| 条件 | 完成数 | 调用数 | tokens | 秒 | 暂缓回答数 |',
             '| --- | ---: | ---: | ---: | ---: | ---: |']
    for variant, row in operational['by_variant'].items():
        lines.append(f"| {variant} | {row['completed']} | {row['model_calls']} | {row['total_tokens']} | "
                     f"{row['elapsed_seconds']:.2f} | {row['abstained_results']} |")
    candidate_cost = report['candidate_audit_resources']
    if candidate_cost.get('status') == 'partial' or candidate_cost.get('status') == 'complete':
        lines += ['', f"推荐候选证据审查：{candidate_cost['audited']} / {candidate_cost['required']} 条，"
                  f"{candidate_cost['model_calls']} 次调用，{candidate_cost['total_tokens']} tokens；该成本与A–D分析成本分开。"]
    lines += ['', '## 人工效果评测', '']
    if report['human_evaluation'].get('status') != 'complete':
        lines.append('待完成独立Gold标注与两名评审；当前不报告准确率、证据支持提升或推荐效果。')
    else:
        lines += ['| 条件 | 目标正确率 | 需求准确率 | 需求召回率 | 引用支持率 | 阶段合理率 |',
                  '| --- | ---: | ---: | ---: | ---: | ---: |']
        for variant, row in report['variant_comparison']['by_variant'].items():
            show = lambda key: '—' if row[key] is None else f"{row[key]:.3f}"
            lines.append(f"| {variant} | {show('goal_correctness')} | {show('requirement_precision')} | "
                         f"{show('requirement_recall')} | {show('citation_support')} | {show('stage_reasonableness')} |")
        lines += ['', f"双评审一致性 Cohen κ：{report['human_evaluation']['cohen_kappa']}。"]
    path.with_suffix('.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--protocol', type=Path, default=Path('experiments/frozen_20261017/protocol.json'))
    parser.add_argument('--output', type=Path, default=Path('experiments/frozen_20261017'))
    commands = parser.add_subparsers(dest='command', required=True)
    freeze = commands.add_parser('freeze'); freeze.add_argument('--seed', type=int, default=20261017)
    commands.add_parser('verify')
    run = commands.add_parser('run')
    run.add_argument('--variants', nargs='+', choices=list('ABCD'), default=list('ABCD'))
    run.add_argument('--task-id', action='append'); run.add_argument('--limit', type=int)
    audit = commands.add_parser('audit-candidates'); audit.add_argument('--repo', action='append')
    recommend = commands.add_parser('recommend'); recommend.add_argument('--top-k', type=int, default=3)
    commands.add_parser('review')
    commands.add_parser('report')
    args = parser.parse_args(argv)
    root, output = args.root.resolve(), (args.root / args.output).resolve() if not args.output.is_absolute() else args.output
    protocol_path = (args.root / args.protocol).resolve() if not args.protocol.is_absolute() else args.protocol
    if args.command == 'freeze':
        value = freeze_protocol(root, output, args.seed)
        print(json.dumps({'status': 'frozen', 'tasks': len(value['tasks']), 'sha256': value['protocol_sha256']})); return 0
    protocol = load_json(protocol_path); verify_protocol(root, protocol)
    if args.command == 'verify':
        print(json.dumps({'status': 'verified', 'tasks': len(protocol['tasks']), 'sha256': protocol['protocol_sha256']})); return 0
    model = configured_model(root)
    if args.command == 'run':
        runner = ExperimentRunner(root, protocol, output,
            session_factory=lambda variant: client_and_session(root, variant), model=model,
            progress=lambda message: print(message, flush=True))
        print(json.dumps(runner.run(args.variants, args.task_id, args.limit), ensure_ascii=False)); return 0
    rules = load_json(root / 'technology_rules.json')['rules']
    audits_path = output / 'candidate_audits.json'
    if args.command == 'audit-candidates':
        audits = audit_candidate_profiles(root, protocol, rules, lambda task_id: client_and_session(root), audits_path, args.repo)
        complete = sum(value.get('status') == 'complete' for value in audits.values())
        print(json.dumps({'status': 'complete' if complete == len(protocol['candidate_pool']) else 'partial',
                          'audited': complete, 'required': len(protocol['candidate_pool'])})); return 0
    results = load_results(output, protocol)
    if args.command == 'recommend':
        candidates = load_candidate_profiles(root, protocol, rules)
        audits = load_json(audits_path) if audits_path.exists() else {}
        quality = {task_id: value.get('quality') for task_id, value in audits.items() if value.get('status') == 'complete'}
        rows = []
        for result in results:
            if result.get('variant') != 'D' or result.get('status') != 'complete': continue
            try:
                source, source_quality = profile_from_result(result, rules)
                quality[source['task_id']] = source_quality
                methods = run_recommendation_methods(source, candidates, quality, args.top_k, protocol['seed'])
                rows.extend({'method': method, 'source_task_id': source['task_id'], 'recommendations': values}
                            for method, values in methods.items())
            except ValueError:
                continue
        atomic_json(output / 'recommendation_results.json', rows)
        print(json.dumps({'status': 'complete', 'lists': len(rows), 'sources': len(rows)//3})); return 0
    recommendation_rows = load_json(output / 'recommendation_results.json') if (output / 'recommendation_results.json').exists() else []
    paths = review_paths(output, results, recommendation_rows)
    review_dir, recommendation_dir = paths['analysis'], paths['recommendation']
    if args.command == 'review':
        if not review_dir.exists():
            build_review_bundle(root, protocol, results, review_dir)
        if recommendation_rows and not recommendation_dir.exists():
            build_recommendation_review_bundle(root, protocol, recommendation_rows, recommendation_dir)
        atomic_json(output / 'review_index.json', {**{key: str(value.relative_to(output))
            for key, value in paths.items() if isinstance(value, Path)},
            'analysis_inventory_sha256': paths['analysis_inventory_sha256'],
            'recommendation_inventory_sha256': paths['recommendation_inventory_sha256'],
            'updated_at': utc_now()})
        print(json.dumps({'status': 'pending_human_review', 'predictions': len(results),
                          'recommendation_lists': len(recommendation_rows)})); return 0
    index_path = output / 'review_index.json'
    if index_path.exists():
        index = load_json(index_path)
        review_dir, recommendation_dir = output / index['analysis'], output / index['recommendation']
    human = score_reviews(review_dir) if review_dir.exists() else {'status': 'pending'}
    rec = score_recommendation_reviews(recommendation_dir) if recommendation_dir.exists() else {'status': 'pending'}
    variants = compare_variant_scores(review_dir, human) if review_dir.exists() else {'status': 'pending'}
    methods = compare_recommendation_methods(recommendation_dir, rec) if recommendation_dir.exists() else {'status': 'pending'}
    audits = load_json(audits_path) if audits_path.exists() else {}
    complete_audits = [value for value in audits.values() if value.get('status') == 'complete']
    audit_resources = [value.get('cumulative_resources') or value.get('resources')
                       for value in audits.values() if value.get('cumulative_resources') or value.get('resources')]
    candidate_cost = {'status': 'complete' if len(complete_audits) == len(protocol['candidate_pool']) else 'partial',
        'audited': len(complete_audits), 'required': len(protocol['candidate_pool']),
        'attempt_records': len(audit_resources),
        'model_calls': sum(value.get('model_calls', 0) for value in audit_resources),
        'total_tokens': sum(value.get('total_tokens', 0) for value in audit_resources),
        'usage_complete': all(value.get('usage_complete', False) for value in audit_resources),
        'elapsed_seconds': round(sum(value.get('elapsed_seconds', 0) for value in audit_resources), 4)}
    report = write_operational_report(output / 'research_report.json', protocol, results, human, rec,
                                      variants, methods, candidate_cost)
    print(json.dumps({'status': report['human_evaluation']['status'], 'report': str(output/'research_report.md')})); return 0


if __name__ == '__main__':
    raise SystemExit(main())
