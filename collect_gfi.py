#!/usr/bin/env python3
"""Collect SymPy/napari GFI evidence and pinned source, using only Python stdlib."""
import argparse
import csv
import hashlib
import io
import json
import os
import posixpath
import re
import shutil
import stat
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath

# Re-exported for backward compatibility: other modules may import these names from here.
from gfi_prototype.common import atomic_json, read_token, sha256_file, utc_now


PROJECTS = (
    {'repo': 'sympy/sympy', 'labels': ['Easy to Fix', 'good first issue']},
    {'repo': 'napari/napari', 'labels': ['contribute:good first issue']},
)
SCHEMA_VERSION = 1
PRINT_LOCK = threading.Lock()


def log(message):
    with PRINT_LOCK:
        print(message, flush=True)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


class Credential:
    def __init__(self, token, interval=0.4):
        if any(ord(char) < 33 or ord(char) > 126 for char in token):
            raise ValueError('Token must contain printable ASCII characters without whitespace')
        self._token = token
        self.interval = interval
        self.lock = threading.Condition()
        self.next_request = 0.0

    def __repr__(self):
        return '<Credential redacted>'

    def wait(self):
        with self.lock:
            while self.next_request > time.monotonic():
                self.lock.wait(timeout=max(0, self.next_request - time.monotonic()))
            self.next_request = time.monotonic() + self.interval

    def cooldown(self, seconds):
        with self.lock:
            self.next_request = max(self.next_request, time.monotonic() + seconds)
            self.lock.notify_all()

    def redact(self, text):
        if self._token:
            text = text.replace(self._token, '[REDACTED]')
        return re.sub(r'(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+', '[REDACTED]', text)


def credentials_from_env(env):
    tokens = []
    for key in ('GITHUB_TOKEN_1', 'GITHUB_TOKEN_2', 'GITHUB_TOKEN', 'GH_TOKEN'):
        token = env.get(key, '').strip()
        if token and token not in tokens:
            tokens.append(token)
    return [Credential(token) for token in tokens]


def retry_delay(status, headers, now=None, attempt=0):
    headers = {key.lower(): value for key, value in headers.items()}
    if status in (403, 429) and 'retry-after' in headers:
        try:
            return max(0, float(headers['retry-after']))
        except ValueError:
            pass
    if status in (403, 429) and headers.get('x-ratelimit-remaining') == '0':
        return max(1, float(headers.get('x-ratelimit-reset', 0)) - (time.time() if now is None else now) + 1)
    if status == 429:
        return 60 * (attempt + 1)
    if status in (500, 502, 503, 504):
        return min(60, 2 ** (attempt + 1))
    return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward credentials even if a server returns a redirect.
        return None


class GitHubClient:
    def __init__(self, credential, api_base='https://api.github.com', max_rate_wait=3700):
        self.credential = credential
        self.api_base = api_base.rstrip('/')
        self.max_rate_wait = max_rate_wait
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, path, params=None):
        url = path if '://' in path else self.api_base + '/' + path.lstrip('/')
        if urllib.parse.urlsplit(url)[:2] != urllib.parse.urlsplit(self.api_base)[:2]:
            raise ValueError('Refusing API request to a different origin')
        if params:
            url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
        headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'gfi-research-collector',
                   'X-GitHub-Api-Version': '2022-11-28'}
        if self.credential._token:
            headers['Authorization'] = 'Bearer ' + self.credential._token
        for attempt in range(6):
            self.credential.wait()
            try:
                with self.opener.open(urllib.request.Request(url, headers=headers), timeout=60) as response:
                    return json.load(response), dict(response.headers)
            except urllib.error.HTTPError as exc:
                response_headers = dict(exc.headers)
                # Read the message only to identify documented secondary rate limits.
                message = exc.read().decode('utf-8', errors='replace')
                delay = retry_delay(exc.code, response_headers, attempt=attempt)
                if exc.code == 403 and delay is None and 'secondary rate limit' in message.lower():
                    delay = 60 * (attempt + 1)
                if delay is None or attempt == 5 or delay > self.max_rate_wait:
                    hint = ' Check token validity/permissions.' if exc.code in (401, 403) else ''
                    raise RuntimeError(f'GitHub HTTP {exc.code} at {urllib.parse.urlsplit(url).path}.{hint}') from None
                log(f'API rate/transient limit: waiting {delay:.0f}s (attempt {attempt + 1}/6)')
                self.credential.cooldown(delay)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                if attempt == 5:
                    raise RuntimeError('API network failure: ' + self.credential.redact(str(exc))) from None
                time.sleep(min(30, 2 ** attempt))
        raise RuntimeError('API retries exhausted')

    def get(self, path):
        return self.request(path)[0]

    def pages(self, path, params=None):
        items, seen = [], set()
        while path:
            if path in seen:
                raise RuntimeError('Pagination loop detected')
            seen.add(path)
            page, headers = self.request(path, params)
            if not isinstance(page, list):
                raise ValueError('Expected a list from paginated API')
            items.extend(page)
            link = next((v for k, v in headers.items() if k.lower() == 'link'), '')
            match = re.search(r'<([^>]+)>;\s*rel="next"', link)
            path, params = (match.group(1) if match else None), None
        return items


def materialized_archive_members(archive, *, preserve_missing_links=False):
    """Resolve only internal archive links; never create filesystem symlinks."""
    nodes, root = {}, None
    for info in archive.infolist():
        name = info.filename.rstrip('/')
        parts = PurePosixPath(name).parts
        if not parts or '\\' in name or ':' in name or name.startswith('/') or '..' in parts:
            raise ValueError('Unsafe source archive member path')
        root = root or parts[0]
        if parts[0] != root:
            raise ValueError('Expected a single root in source archive')
        if name in nodes and nodes[name] is not None:
            raise ValueError('Duplicate source archive member')
        nodes[name] = info
        for length in range(1, len(parts)):
            nodes.setdefault('/'.join(parts[:length]), None)
    children = {}
    for name in nodes:
        if name != root:
            children.setdefault(posixpath.dirname(name), []).append(name)
    result = []
    def walk(name, output_name, ancestors):
        if name in ancestors:
            raise ValueError('Cyclic source archive link')
        ancestors = ancestors | {name}
        info = nodes[name]
        if info is not None and stat.S_ISLNK(info.external_attr >> 16):
            link = archive.read(info).decode('utf-8')
            if not link or '\\' in link or ':' in link or link.startswith('/'):
                raise ValueError('Unsafe source archive link target')
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), link))
            if not target.startswith(root+'/'):
                raise ValueError('Source archive link target is outside repository or missing')
            if target not in nodes:
                if preserve_missing_links:
                    result.append((output_name, info))
                    return
                raise ValueError('Source archive link target is outside repository or missing')
            walk(target, output_name, ancestors)
        elif info is None or info.is_dir():
            result.append((output_name, None))
            for child in sorted(children.get(name, [])):
                walk(child, posixpath.join(output_name, posixpath.basename(child)), ancestors)
        else:
            result.append((output_name, info))
    if root:
        walk(root, '', set())
    return result


def extract_source(archive_path, destination, *, preserve_missing_links=False):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        members = []
        for relative, info in materialized_archive_members(archive, preserve_missing_links=preserve_missing_links):
            target = destination / relative
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError('Source archive escapes output directory')
            members.append((info, target))
        for info, target in members:
            if info is None:
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, target.open('wb') as dst:
                    shutil.copyfileobj(src, dst)


def source_snapshot(client, repo, metadata, folder, *, preserve_missing_links=False):
    receipt_path = folder / 'source_snapshot.json'
    receipt = read_json(receipt_path)
    if receipt:
        if (receipt.get('repo') != repo or not re.fullmatch(r'[0-9a-f]{64}', receipt.get('archive_sha256', ''))):
            raise ValueError('Invalid source snapshot receipt')
        sha = receipt.get('commit_sha', '')
        preserve_missing_links = receipt.get('missing_internal_link_handling') == 'preserve_link_text' or preserve_missing_links
    else:
        if (folder / 'source').exists():
            raise RuntimeError('Unmanaged source directory exists; choose a new output directory')
        commit = client.get(f'/repos/{repo}/commits/' + urllib.parse.quote(metadata['default_branch'], safe=''))
        sha = commit['sha']
    if not re.fullmatch(r'[0-9a-fA-F]{40}', sha):
        raise ValueError('Invalid source commit SHA')
    url = f'https://codeload.github.com/{repo}/zip/{sha}'
    archive_path = folder / f'source-{sha}.zip'
    if not archive_path.exists():
        partial = archive_path.with_suffix('.zip.part')
        for attempt in range(4):
            try:
                request = urllib.request.Request(url, headers={'User-Agent': 'gfi-research-collector'})
                # Public archive download never receives the API token.
                with urllib.request.urlopen(request, timeout=120) as response, partial.open('wb') as output:
                    shutil.copyfileobj(response, output, length=1024 * 1024)
                with zipfile.ZipFile(partial) as archive:
                    if archive.testzip() is not None:
                        raise ValueError('Corrupt source archive')
                partial.replace(archive_path)
                break
            except (OSError, urllib.error.URLError, zipfile.BadZipFile, ValueError):
                if attempt == 3:
                    raise RuntimeError('Source archive download failed') from None
                time.sleep(2 ** attempt)
    archive_hash = sha256_file(archive_path)
    if receipt and archive_hash != receipt['archive_sha256']:
        raise ValueError('Source archive checksum mismatch')
    if receipt and (folder / 'source').is_dir():
        if not source_is_complete(archive_path, folder / 'source', preserve_missing_links=preserve_missing_links):
            raise ValueError('Source tree is incomplete; choose a new output directory')
        return receipt
    # Interrupted extraction is safe to repeat; validated members are overwritten.
    staging = folder / f'source-{sha}.extracting'
    extract_source(archive_path, staging, preserve_missing_links=preserve_missing_links)
    receipt = receipt or {'repo': repo, 'commit_sha': sha, 'branch': metadata['default_branch'],
               'url': url, 'downloaded_at': utc_now(), 'archive': archive_path.name,
               'archive_sha256': archive_hash,
               'symlink_handling': 'Internal archive links copied as regular files/directories; external links rejected'}
    if preserve_missing_links:
        receipt['missing_internal_link_handling'] = 'preserve_link_text'
        with zipfile.ZipFile(archive_path) as archive:
            receipt['preserved_missing_internal_links'] = [
                {'path': relative, 'target': archive.read(info).decode('utf-8')}
                for relative, info in materialized_archive_members(archive, preserve_missing_links=True)
                if info is not None and stat.S_ISLNK(info.external_attr >> 16)]
    # Publish the receipt first so interrupted publication can resume at the same SHA.
    atomic_json(receipt_path, receipt)
    staging.replace(folder / 'source')
    return receipt


def source_is_complete(archive_path, source, *, preserve_missing_links=False):
    count = 0
    with zipfile.ZipFile(archive_path) as archive:
        for relative, info in materialized_archive_members(archive, preserve_missing_links=preserve_missing_links):
            if info is None:
                continue
            target = source / relative
            if not target.resolve().is_relative_to(source.resolve()):
                return False
            if not target.is_file() or target.stat().st_size != info.file_size:
                return False
            count += 1
    return count > 0


def safe_error(client, exc):
    credential = getattr(client, 'credential', None)
    if credential:
        return credential.redact(str(exc))
    return re.sub(r'(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+', '[REDACTED]', str(exc))


def related_prs(timeline):
    found = {}
    for event in timeline:
        candidate = (event.get('source') or {}).get('issue') or {}
        if candidate.get('pull_request') and candidate.get('html_url'):
            found[candidate['html_url']] = candidate
    return list(found.values())


def export_bundles(folder, bundles):
    path = folder / 'issues.jsonl'
    temp = path.with_suffix('.jsonl.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        for bundle in bundles:
            stream.write(json.dumps(bundle, ensure_ascii=False) + '\n')
    temp.replace(path)
    path = folder / 'issues.csv'
    temp = path.with_suffix('.csv.tmp')
    with temp.open('w', encoding='utf-8-sig', newline='') as stream:
        fields = ['number', 'title', 'state', 'html_url', 'created_at', 'updated_at', 'labels',
                  'assignees', 'comment_count', 'related_pr_count']
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for bundle in bundles:
            issue = bundle['issue']
            row = {key: issue.get(key, '') for key in fields[:6]}
            row.update(labels='; '.join(label['name'] for label in issue.get('labels', [])),
                       assignees='; '.join(user['login'] for user in issue.get('assignees', [])),
                       comment_count=len(bundle['comments']), related_pr_count=len(bundle['related_pull_requests']))
            # Avoid interpreting untrusted issue titles as Excel formulas.
            for key, value in row.items():
                if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                    row[key] = "'" + value
            writer.writerow(row)
    temp.replace(path)


def collect_project(project, client, output, state='open', source=True, refresh=False, limit=None, issue_workers=1):
    repo = project['repo']
    folder = Path(output) / repo.replace('/', '__')
    folder.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    errors, bundles, by_number = [], [], {}
    manifest = {'schema_version': SCHEMA_VERSION, 'repo': repo, 'labels': project['labels'],
                'requested_state': state, 'started_at': started, 'status': 'running',
                'limited_run': limit is not None, 'source_requested': source}
    atomic_json(folder / 'manifest.json', manifest)
    try:
        metadata = client.get(f'/repos/{repo}')
        atomic_json(folder / 'repository.json', metadata)
        for label in project['labels']:
            records = client.pages(f'/repos/{repo}/issues', {'state': state, 'labels': label,
                                   'per_page': 100, 'sort': 'created', 'direction': 'asc'})
            atomic_json(folder / 'raw' / ('label-' + hashlib.sha256(label.encode()).hexdigest()[:12] + '.json'),
                        {'label': label, 'fetched_at': utc_now(), 'items': records})
            for issue in records:
                if 'pull_request' not in issue and (state == 'all' or issue['state'] == state):
                    by_number[issue['number']] = issue
        issues = [by_number[number] for number in sorted(by_number)]
        manifest['discovered_issue_count'] = len(issues)
        if limit is not None:
            issues = issues[:limit]
        log(f'[{repo}] Found {len(by_number)} unique issues; collecting {len(issues)}')
        def fetch_issue(issue):
            number = issue['number']
            path = folder / 'issues' / f'{number}.json'
            cached = read_json(path)
            if (not refresh and isinstance(cached, dict) and cached.get('schema_version') == SCHEMA_VERSION
                    and cached.get('issue') == issue and cached.get('complete') is True
                    and isinstance(cached.get('comments'), list) and isinstance(cached.get('timeline'), list)):
                return cached, 'cached'
            comments = client.pages(f'/repos/{repo}/issues/{number}/comments', {'per_page': 100}) if issue.get('comments', 0) else []
            timeline = client.pages(f'/repos/{repo}/issues/{number}/timeline', {'per_page': 100})
            bundle = {'schema_version': SCHEMA_VERSION, 'repo': repo, 'fetched_at': utc_now(),
                      'complete': True, 'issue': issue, 'comments': comments, 'timeline': timeline,
                      'related_pull_requests': related_prs(timeline)}
            atomic_json(path, bundle)
            return bundle, 'saved'
        with ThreadPoolExecutor(max_workers=issue_workers, thread_name_prefix='issue') as issue_pool:
            pending = {issue_pool.submit(fetch_issue, issue): issue['number'] for issue in issues}
            for index, future in enumerate(as_completed(pending), 1):
                number = pending[future]
                try:
                    bundle, action = future.result()
                    bundles.append(bundle)
                    log(f'[{repo}] {index}/{len(issues)} #{number}: {action}')
                except Exception as exc:
                    errors.append({'stage': 'issue', 'number': number, 'error': safe_error(client, exc)})
                    log(f'[{repo}] #{number}: failed; will retry on next run')
        bundles.sort(key=lambda bundle: bundle['issue']['number'])
        export_bundles(folder, bundles)
        if source:
            log(f'[{repo}] Downloading/checking pinned source snapshot')
            try:
                manifest['source'] = source_snapshot(client, repo, metadata, folder,
                                                     preserve_missing_links=project.get('preserve_missing_source_links', False))
            except Exception as exc:
                errors.append({'stage': 'source', 'error': safe_error(client, exc)})
    except Exception as exc:
        errors.append({'stage': 'project', 'error': safe_error(client, exc)})
    manifest.update(status='partial' if errors else 'complete', finished_at=utc_now(),
                    issue_count=len(bundles), failed_issue_count=sum(e['stage'] == 'issue' for e in errors), errors=errors)
    atomic_json(folder / 'manifest.json', manifest)
    log(f'[{repo}] {manifest["status"]}: {len(bundles)} issues, {len(errors)} errors')
    return manifest


def main(argv=None, *, projects=None, project_runner=collect_project,
         default_state='open', summary_name='collection_summary.json', description=None):
    parser = argparse.ArgumentParser(description=description or __doc__)
    parser.add_argument('--output', type=Path, default=Path('data'))
    parser.add_argument('--state', choices=['open', 'closed', 'all'], default=default_state)
    parser.add_argument('--refresh', action='store_true', help='Refetch cached issue details')
    parser.add_argument('--no-source', action='store_true')
    parser.add_argument('--limit', type=int, help='Smoke test only; limit issues per project')
    parser.add_argument('--prompt-tokens', type=int, choices=[1, 2], help='Read tokens using hidden input')
    parser.add_argument('--anonymous', action='store_true', help='Use public unauthenticated quota')
    parser.add_argument('--max-rate-wait', type=float, default=3700, help='Maximum accepted API cooldown in seconds')
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be at least 1')
    if args.max_rate_wait < 0:
        parser.error('--max-rate-wait cannot be negative')
    try:
        credentials = [] if args.anonymous else credentials_from_env(os.environ)
    except ValueError as exc:
        parser.error(str(exc))
    if args.prompt_tokens:
        values = []
        for index in range(args.prompt_tokens):
            try:
                value = read_token(f'GitHub token {index + 1} (hidden): ')
            except (RuntimeError, EOFError) as exc:
                parser.error(str(exc) or 'Token input unavailable')
            if value and value not in values:
                values.append(value)
        try:
            credentials = [Credential(value) for value in values]
        except ValueError as exc:
            parser.error(str(exc))
        if not credentials:
            parser.error('No token entered')
    if not credentials:
        if not args.anonymous:
            parser.error('Set GITHUB_TOKEN_1 / GITHUB_TOKEN_2, or use --prompt-tokens 1')
        credentials = [Credential('')]
    log(f'Using {len(credentials)} unique credential(s), 2 project workers; output: {args.output.resolve()}')
    log('Tokens from the same account may share rate limits. Source snapshots do not include Git history.')
    args.output.mkdir(parents=True, exist_ok=True)
    # Prevent two processes from writing the same project/checkpoint files.
    lock_path = args.output / '.collector.lock'
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        parser.error(f'{lock_path} exists. Check whether another collector is running before removing a stale lock.')
    os.close(descriptor)
    results = []
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='gfi') as pool:
            futures = {}
            for index, project in enumerate(PROJECTS if projects is None else projects):
                client = GitHubClient(credentials[index % len(credentials)], max_rate_wait=args.max_rate_wait)
                futures[pool.submit(project_runner, project, client, args.output, args.state,
                                    not args.no_source, args.refresh, args.limit)] = project
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    message = str(exc)
                    for credential in credentials:
                        message = credential.redact(message)
                    results.append({'repo': futures[future]['repo'], 'status': 'partial', 'error': message})
        atomic_json(args.output / summary_name, {'finished_at': utc_now(), 'projects': results})
    finally:
        lock_path.unlink(missing_ok=True)
    return 0 if all(result['status'] == 'complete' for result in results) else 1


if __name__ == '__main__':
    sys.exit(main())
