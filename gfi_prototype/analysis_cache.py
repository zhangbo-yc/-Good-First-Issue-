"""Versioned cache for v2 model analyses and their normalized technology profiles."""
import json
from pathlib import Path

from gfi_prototype import unified
from gfi_prototype.common import atomic_json, sha256_file, sha256_text

COMPLETE_STATUS = 'analysis_ready_pending_human_review'


def compute_analysis_key(project_folder, number, model, prompt_version, schema_version,
                         retrieval_version='retrieval_v2'):
    project_folder = Path(project_folder)
    bundle = json.loads((project_folder / 'issues' / f'{number}.json').read_text(encoding='utf-8'))
    snapshot = json.loads((project_folder / 'source_snapshot.json').read_text(encoding='utf-8'))
    return {'repo': bundle['repo'], 'issue_number': number,
            'issue_content_sha256': sha256_text(json.dumps(bundle['issue'], sort_keys=True, ensure_ascii=False)),
            'source_commit': snapshot['commit_sha'], 'model': model, 'prompt_version': prompt_version,
            'schema_version': schema_version, 'retrieval_version': retrieval_version}


def find_complete_analysis_runs(project_folder):
    project_folder = Path(project_folder)
    runs = []
    runs_dir = project_folder / 'prototype_runs'
    if not runs_dir.is_dir():
        return runs
    for manifest_path in sorted(runs_dir.glob('*/run_manifest.json')):
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('status') == COMPLETE_STATUS:
            manifest = dict(manifest)
            manifest['run_dir'] = manifest_path.parent.relative_to(project_folder).as_posix()
            runs.append(manifest)
    return runs


def read_analysis_cache(project_folder):
    path = Path(project_folder) / 'analysis_cache.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def write_analysis_cache(project_folder, entries):
    atomic_json(Path(project_folder) / 'analysis_cache.json', entries)


def profile_from_cached_analysis(project_folder, task_id, rules):
    project_folder = Path(project_folder)
    entries = read_analysis_cache(project_folder)
    entry = entries.get(task_id)
    if not entry:
        raise ValueError(f'No cached analysis for {task_id}')
    number = int(task_id.rsplit('#', 1)[1])
    run_dir = project_folder / entry['run_dir']
    manifest = json.loads((run_dir / 'run_manifest.json').read_text(encoding='utf-8'))
    if manifest.get('status') != COMPLETE_STATUS:
        raise ValueError(f'Cached analysis for {task_id} is not complete')
    if manifest.get('prompt_version') != entry.get('prompt_version'):
        raise ValueError(f'Cached manifest for {task_id} has a different prompt version')
    result = json.loads((run_dir / 'analysis.json').read_text(encoding='utf-8'))
    if result.get('schema_version') != 2:
        raise ValueError(f'Cached analysis for {task_id} is not schema v2')
    key = compute_analysis_key(project_folder, number, entry['model'], entry['prompt_version'],
                               entry['schema_version'])
    if entry.get('retrieval_version') and entry['retrieval_version'] != key['retrieval_version']:
        raise ValueError(f'Cached retrieval version changed for {task_id}')
    for field in ('issue_content_sha256', 'source_commit', 'model', 'prompt_version', 'schema_version'):
        if key[field] != entry.get(field):
            raise ValueError(f'Cached analysis inputs changed for {task_id}')
    analysis_path = run_dir / 'analysis.json'
    if sha256_file(analysis_path) != entry.get('analysis_sha256'):
        raise ValueError(f'Cached analysis file changed for {task_id}')
    if sha256_text(json.dumps(rules, sort_keys=True, ensure_ascii=False)) != entry.get('rules_sha256'):
        raise ValueError(f'Technology rules changed for {task_id}')
    bundle = json.loads((project_folder / 'issues' / f'{number}.json').read_text(encoding='utf-8'))
    packet = json.loads((run_dir / 'evidence_bundle.json').read_text(encoding='utf-8'))
    meta = {'repo': bundle['repo'], 'issue_number': number, 'title': bundle['issue'].get('title', ''),
            'url': bundle['issue'].get('html_url'), 'source_commit': key['source_commit'],
            'issue_content_sha256': key['issue_content_sha256']}
    return unified.analysis_profile(result['analysis'], meta, rules, packet['evidence'])
