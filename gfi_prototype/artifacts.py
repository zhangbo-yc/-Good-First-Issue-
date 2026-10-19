"""Read recorded demo and analysis artifacts without generating model calls."""
import json
from pathlib import Path

from gfi_prototype.storage import read_json, read_jsonl

def latest_demo_data(root):
    root = Path(root)
    runs = sorted((root / 'data/demo_runs').glob('*'), reverse=True)
    for run in runs:
        manifest_path = run / 'run_manifest.json'
        if not manifest_path.exists():
            continue
        manifest = read_json(manifest_path)
        if manifest.get('status') != 'complete_demo':
            continue
        catalog = read_jsonl(run / 'demo_catalog.jsonl')
        profiles = read_jsonl(run / 'task_technology_profiles.jsonl')
        matches = read_jsonl(run / 'all_task_matches.jsonl')
        return catalog, profiles, matches, manifest
    raise ValueError('No complete demo run found; run recommend_gfi.py first')



def _identity_matches(value, profile):
    return (value.get('repo') == profile['repo']
            and value.get('issue_number') == profile['issue_number']
            and value.get('source_commit') == profile['source_commit'])


def analysis_artifact(root, profile):
    """Prefer complete, identity-bound recorded calls; otherwise expose the draft.

    A model's input packet is displayed verbatim, rather than reconstructed and
    misrepresented as the packet sent at the time. Invalid runs are skipped.
    """
    project = Path(root) / 'data' / profile['repo'].replace('/', '__')
    for path in sorted(project.glob('prototype_runs/*/run_manifest.json'), reverse=True):
        try:
            manifest = json.loads(path.read_text(encoding='utf-8'))
            if (not _identity_matches(manifest, profile)
                    or manifest.get('status') != 'analysis_ready_pending_human_review'):
                continue
            packet = json.loads((path.parent / 'evidence_bundle.json').read_text(encoding='utf-8'))
            result = json.loads((path.parent / 'analysis.json').read_text(encoding='utf-8'))
            if (not _identity_matches(packet, profile) or not isinstance(packet.get('evidence'), list)
                    or not isinstance(result.get('analysis'), dict) or not result['analysis']):
                continue
            return {'kind': 'model', 'packet': packet, 'analysis': result['analysis'],
                    'manifest': manifest, 'run_name': path.parent.name}
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    draft_path = project / 'annotations/task_analysis_drafts.jsonl'
    if draft_path.exists():
        for line in draft_path.read_text(encoding='utf-8').splitlines():
            try:
                draft = json.loads(line)
                if _identity_matches(draft, profile):
                    return {'kind': 'draft', 'packet': {'evidence': draft.get('evidence', [])},
                            'analysis': draft, 'manifest': {}, 'run_name': ''}
            except (ValueError, TypeError, AttributeError):
                continue
    return {'kind': 'draft', 'packet': {'evidence': profile.get('evidence', [])},
            'analysis': profile, 'manifest': {}, 'run_name': ''}


