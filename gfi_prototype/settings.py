"""Minimal local configuration loader; credentials never enter public DTOs."""
import os
from pathlib import Path


def load_project_env(root):
    path = Path(root) / '.env'
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip().removeprefix('export ')
        if key not in {'DEEPSEEK_API_KEY', 'DEEPSEEK_MODEL'}:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if value:
            os.environ.setdefault(key, value)
