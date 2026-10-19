"""Offline immutable research sampling. Reads public corpus files only, never credentials.

SHA256 is an integrity check against accidental edits, not a digital signature.
The pinned archive is authoritative; mutable extracted working trees are not inputs.
"""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import random


QUOTAS = {'ManimCommunity/manim': 10, 'sympy/sympy': 10, 'dask/dask': 10,
          'ray-project/ray': 10, 'napari/napari': 5}
POLICY = {'version': 1, 'sampling': 'sorted strata round robin with seeded within-stratum shuffle',
          'eligible': 'triage concrete tasks at any state (fallback task and historical sets); snapshot understanding only',
          'stratum': 'lexicographically first task type; unknown when absent',
          'exclude': 'all analysis cache IDs and annotation draft IDs',
          'candidate_pool': 'all existing analysis cache IDs', 'comments': 'excluded from model and gold materials'}


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def _inside(root, path):
    path = Path(path).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Corpus path escapes project root')
    return path


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def _construct(root, seed):
    root = Path(root).resolve()
    inventory, snapshots, excluded, candidates, projects = {}, {}, set(), set(), {}

    def register(path):
        path = _inside(root, path)
        if not path.is_file():
            raise ValueError(f'Missing protocol input: {path.relative_to(root)}')
        inventory[path.relative_to(root).as_posix()] = _sha(path)
        return path

    rules = register(root / 'technology_rules.json')
    for repo in QUOTAS:
        base = root / 'data' / repo.replace('/', '__')
        snapshot_path = register(base / 'source_snapshot.json')
        snapshot = _json(snapshot_path)
        if snapshot.get('repo') != repo or not snapshot.get('commit_sha'):
            raise ValueError('Invalid source snapshot identity')
        archive = register(base / snapshot['archive'])
        if inventory[archive.relative_to(root).as_posix()] != snapshot['archive_sha256']:
            raise ValueError('Source archive SHA256 mismatch')
        snapshots[repo] = {'source_commit': snapshot['commit_sha'],
                           'archive_sha256': snapshot['archive_sha256'],
                           'archive': archive.relative_to(root).as_posix(),
                           'snapshot_sha256': _sha(snapshot_path)}
        cache = _json(register(base / 'analysis_cache.json'))
        for task_id, entry in cache.items():
            if not task_id.startswith(repo + '#'):
                raise ValueError('Invalid cached task identity')
            candidates.add(task_id)
            excluded.add(task_id)
            if entry.get('run_dir'):
                run = _inside(root, base / entry['run_dir'])
                register(run / 'analysis.json')
                register(run / 'evidence_bundle.json')
        for draft in sorted((base / 'annotations').glob('*draft*.jsonl')):
            for row in _rows(register(draft)):
                number = row.get('issue_number', row.get('number'))
                task_id = row.get('task_id') or (f"{row.get('repo', repo)}#{number}" if number else None)
                if task_id:
                    excluded.add(task_id)
        triage = base / 'processed' / 'triage.jsonl'
        if triage.exists():
            tasks = [row for row in _rows(register(triage)) if row.get('record_type') == 'task']
        else:
            tasks = _rows(register(base / 'processed' / 'tasks.jsonl'))
            historical = base / 'processed' / 'historical_tasks.jsonl'
            if historical.exists():
                tasks += _rows(register(historical))
        projects[repo] = (base, tasks)
        # Freeze every raw candidate input, so corpus edits cannot silently alter sampling.
        for row in tasks:
            register(base / row.get('source_bundle', f"issues/{row['number']}.json"))
    if len(candidates) != 50:
        raise ValueError('Expected exactly 50 existing development candidate IDs')
    selected = []
    rng = random.Random(seed)
    for repo, quota in QUOTAS.items():
        base, rows = projects[repo]
        strata = defaultdict(list)
        seen = set()
        for row in sorted(rows, key=lambda r: r['number']):
            number = row['number']
            task_id = f'{repo}#{number}'
            if task_id in seen:
                raise ValueError('Duplicate corpus task ID')
            seen.add(task_id)
            if task_id in excluded or row.get('record_type') != 'task':
                continue
            stratum = sorted(row.get('task_types') or ['unknown'])[0]
            bundle = base / row.get('source_bundle', f'issues/{number}.json')
            raw = _json(bundle)
            if raw.get('repo') != repo or raw.get('issue', {}).get('number') != number:
                raise ValueError('Raw issue identity mismatch')
            strata[stratum].append({'task_id': task_id, 'repo': repo, 'issue_number': number,
                'title': raw['issue'].get('title', row['title']), 'stratum': stratum,
                'issue_sha256': _sha(bundle), 'source_commit': snapshots[repo]['source_commit'],
                'state_at_snapshot': raw['issue'].get('state', row.get('state_at_snapshot')),
                'historical_record': row.get('historical_record', False),
                'issue_bundle': bundle.relative_to(root).as_posix()})
        if sum(map(len, strata.values())) < quota:
            raise ValueError(f'Insufficient unseen concrete tasks: {repo}')
        for key in sorted(strata):
            rng.shuffle(strata[key])
        project_selected = []
        while len(project_selected) < quota:
            for key in sorted(strata):
                if strata[key] and len(project_selected) < quota:
                    project_selected.append(strata[key].pop())
        selected.extend(project_selected)
    protocol = {'schema_version': 1, 'seed': seed, 'policy': POLICY, 'quotas': QUOTAS,
                'rules_sha256': _sha(rules), 'source_snapshots': snapshots,
                'excluded_development_ids': sorted(excluded), 'candidate_pool': sorted(candidates),
                'tasks': selected, 'input_sha256': inventory}
    protocol['protocol_sha256'] = _digest(protocol)
    return protocol


def source_annotation_template(root, tasks):
    """Independent gold inputs: raw issue body and pinned code URL; no model outputs."""
    root = Path(root)
    rows = []
    for task in tasks:
        issue = _json(root / task['issue_bundle'])['issue'] if task.get('issue_bundle') else {}
        rows.append({'task_id': task['task_id'], 'title': task['title'],
            'issue_url': f"https://github.com/{task['repo']}/issues/{task['issue_number']}",
            'issue_body': issue.get('body', ''),
            'source_url': f"https://github.com/{task['repo']}/tree/{task['source_commit']}",
            'source_commit': task['source_commit'], 'annotator_id': None, 'completed': False,
            'expected_technology_ids': None, 'requirements': None,
            'stage': None, 'goal': None, 'acceptance': None, 'notes': None})
    return rows


def freeze_protocol(root, output_dir, seed=20261017):
    """Create protocol.json once; identical reruns verify and return without writing."""
    if type(seed) is not int:
        raise ValueError('Seed must be an integer')
    output_dir = Path(output_dir)
    path = output_dir / 'protocol.json'
    if path.exists():
        protocol = _json(path)
        if protocol.get('seed') != seed:
            raise ValueError('Refusing to overwrite frozen seed')
        verify_protocol(root, protocol)
        return protocol
    protocol = _construct(root, seed)
    output_dir.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents two concurrent freezes silently replacing each other.
    with path.open('x', encoding='utf-8') as handle:
        json.dump(protocol, handle, ensure_ascii=False, indent=2)
    gold_path = output_dir / 'gold_template.json'
    with gold_path.open('x', encoding='utf-8') as handle:
        json.dump(source_annotation_template(root, protocol['tasks']), handle, ensure_ascii=False, indent=2)
    return protocol


def verify_protocol(root, protocol):
    """Recompute all identities and deterministic selection; reject any changed input."""
    if not isinstance(protocol, dict) or type(protocol.get('seed')) is not int:
        raise ValueError('Invalid protocol')
    try:
        expected = _construct(root, protocol['seed'])
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('Protocol input unavailable or invalid') from exc
    if protocol != expected:
        raise ValueError('Frozen protocol or current corpus inputs changed')
