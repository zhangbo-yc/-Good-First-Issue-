"""Run the frozen 39-task holdout experiment and DeepSeek Pro review from PyCharm.

Run directly in PyCharm for the full experiment. Optional Run Configuration
parameters: --phase analyze | review | all (default: all), --output <folder>.
LangGraph runs default to langgraph_heldout_v1. The existing final_heldout_v3
folder contains historical Python-workflow results; pass --output to review it.
The run is resumable: completed A/B/C/D results and review calls use their caches.
"""
import argparse
import json
import os
from collections import Counter
from pathlib import Path

from gfi_prototype.common import atomic_json, utc_now
from gfi_prototype.research.protocol import verify_protocol
from gfi_prototype.research.runner import load_results
from gfi_prototype.settings import load_project_env
import judge_experiments
import research


ROOT = Path(__file__).resolve().parent
PROTOCOL_PATH = ROOT / 'experiments' / 'frozen_20261017' / 'protocol.json'
OUTPUT_RELATIVE = Path('experiments/frozen_20261017/langgraph_heldout_v1')
OUTPUT = ROOT / OUTPUT_RELATIVE
DEVELOPMENT_IDS = {
    'ManimCommunity/manim#2379',  # initial pipeline pilot
    'ManimCommunity/manim#52',
    'sympy/sympy#5839',
    'dask/dask#8878',
    'ray-project/ray#15501',
    'napari/napari#6251',
}


def select_holdout(protocol):
    verify_protocol(ROOT, protocol)
    available = {row['task_id'] for row in protocol['tasks']}
    missing_exclusions = DEVELOPMENT_IDS - available
    if missing_exclusions:
        raise ValueError(f'Development IDs missing from frozen sample: {sorted(missing_exclusions)}')
    selected = [row for row in protocol['tasks'] if row['task_id'] not in DEVELOPMENT_IDS]
    if len(protocol['tasks']) != 45 or len(selected) != 39:
        raise ValueError(f'Expected frozen 45 tasks and a 39-task holdout; got '
                         f'{len(protocol["tasks"])} and {len(selected)}')
    counts = Counter(row['repo'] for row in selected)
    expected = {'ManimCommunity/manim': 8, 'sympy/sympy': 9, 'dask/dask': 9,
                'ray-project/ray': 9, 'napari/napari': 4}
    if dict(counts) != expected:
        raise ValueError(f'Unexpected holdout project quotas: {dict(counts)}')
    return selected


def freeze_holdout_manifest(protocol, selected, output=OUTPUT):
    path = output / 'holdout_manifest.json'
    manifest = {'status': 'frozen_development_excluded_holdout',
        'created_at': utc_now(), 'protocol_sha256': protocol['protocol_sha256'],
        'development_ids_excluded': sorted(DEVELOPMENT_IDS),
        'holdout_count': len(selected), 'variants': list('ABCD'),
        'project_counts': dict(Counter(row['repo'] for row in selected)),
        'task_ids': sorted(row['task_id'] for row in selected),
        'interpretation': 'DeepSeek Pro model-assisted exploratory evaluation; not human gold or accuracy.'}
    if path.exists():
        previous = json.loads(path.read_text(encoding='utf-8'))
        if previous.get('protocol_sha256') != manifest['protocol_sha256'] or \
                previous.get('task_ids') != manifest['task_ids'] or \
                previous.get('development_ids_excluded') != manifest['development_ids_excluded']:
            raise ValueError('Existing holdout manifest differs; refusing to replace it')
    else:
        atomic_json(path, manifest)
    return manifest


def ensure_all_results(protocol, selected, output=OUTPUT):
    expected = {(task['task_id'], variant) for task in selected for variant in 'ABCD'}
    found = {(row['task_id'], row['variant']) for row in load_results(output, protocol)
             if row.get('status') == 'complete'}
    missing = sorted(expected - found)
    if missing:
        raise RuntimeError(f'{len(missing)} A/B/C/D results remain; first missing condition: {missing[0]}')
    return found


def run_analysis(protocol, selected, output=OUTPUT):
    task_ids = sorted(row['task_id'] for row in selected)
    args = ['--root', str(ROOT), '--protocol', str(PROTOCOL_PATH), '--output',
            str(output), 'run', '--variants', 'A', 'B', 'C', 'D']
    for task_id in task_ids:
        args.extend(['--task-id', task_id])
    print(f'开始分析：{len(task_ids)}个留出任务 × 4组 = {len(task_ids) * 4}份结果。', flush=True)
    research.main(args)
    ensure_all_results(protocol, selected, output)
    print('A/B/C/D分析已全部完成。', flush=True)


def run_review(output=OUTPUT):
    args = ['--root', str(ROOT), '--experiment', str(output),
            '--model', 'deepseek-v4-pro']
    print('开始DeepSeek Pro盲评和能力门槛一致性复核。', flush=True)
    result = judge_experiments.main(args)
    if result:
        print('评审暂未全部完成。再次使用 --phase review 运行本脚本，'
              '即可从已保存缓存继续。', flush=True)
        return False
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('analyze', 'review', 'all'), default='all',
                        help='analyze: run A/B/C/D; review: score completed outputs; all: both')
    parser.add_argument('--output', type=Path, default=OUTPUT_RELATIVE,
                        help='Use a new folder for a new workflow; old results are never replaced')
    args = parser.parse_args(argv)
    output = (ROOT / args.output).resolve()
    os.chdir(ROOT)
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding='utf-8-sig'))
    selected = select_holdout(protocol)
    freeze_holdout_manifest(protocol, selected, output)
    if args.phase in {'analyze', 'all'}:
        load_project_env(ROOT)
        if not os.environ.get('DEEPSEEK_API_KEY', '').strip():
            raise RuntimeError('请先在项目根目录.env设置DEEPSEEK_API_KEY。')
        run_analysis(protocol, selected, output)
    if args.phase in {'review', 'all'}:
        ensure_all_results(protocol, selected, output)
        if not run_review(output):
            return 1
    print(f'完成。正式留出集报告位于：{output / "model_judge"}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
