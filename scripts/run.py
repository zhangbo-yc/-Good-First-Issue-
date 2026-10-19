"""Grouped repository CLI; execute frozen scripts at their original paths."""
import argparse
import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = {
    'collect': 'collect_gfi.py',
    'collect-dask-manim': 'collect_dask_manim.py',
    'collect-ray': 'collect_ray.py',
    'clean': 'clean_gfi.py',
    'audit': 'audit_gfi_data.py',
    'prepare-annotations': 'prepare_annotation_drafts.py',
    'prepare-content': 'prepare_newcomer.py',
    'prepare-guides': 'prepare_deep_guides.py',
    'analyze': 'analyze_issue.py',
    'batch': 'batch_analyze.py',
    'recommend': 'recommend_gfi.py',
    'research': 'research.py',
    'experiment': 'run_final_experiment.py',
    'control': 'run_equal_budget_experiment.py',
    'review': 'judge_experiments.py',
    'review-control': 'review_equal_budget_completed.py',
    'report-ablation': 'analyze_existing_experiments.py',
    'report-control': 'analyze_equal_budget_results.py',
    'export-workflow': 'export_workflow.py',
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=sorted(COMMANDS))
    parser.add_argument('arguments', nargs=argparse.REMAINDER,
                        help='Arguments for the original script; optionally separate with --.')
    args = parser.parse_args(argv)
    arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    target = ROOT / COMMANDS[args.command]
    old_argv, old_path, old_cwd = sys.argv, sys.path[:], Path.cwd()
    try:
        sys.argv = [str(target), *arguments]
        sys.path.insert(0, str(ROOT))
        os.chdir(ROOT)
        runpy.run_path(str(target), run_name='__main__')
    finally:
        sys.argv = old_argv
        sys.path[:] = old_path
        os.chdir(old_cwd)


if __name__ == '__main__':
    main()
