"""Shared stdlib-only helpers used across collectors, analyzers and reports."""
import getpass
import hashlib
import html
import json
import re
import tempfile
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix=f'.{path.name}.', suffix='.tmp', delete=False) as stream:
        temp = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, indent=2)
    try:
        for attempt in range(5):
            try:
                temp.replace(path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                # Windows file scanners/readers can briefly deny replacement.
                time.sleep(0.03 * (attempt + 1))
    finally:
        temp.unlink(missing_ok=True)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def read_token(prompt):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            return getpass.getpass(prompt).strip()
    except getpass.GetPassWarning:
        raise RuntimeError(
            'Hidden terminal input unavailable; configure token environment variables instead') from None


def safe_text(text):
    text = html.escape(str(text)).replace('\n', ' ')
    return re.sub(r'([\\`*\[\]_!])', r'\\\1', text)
