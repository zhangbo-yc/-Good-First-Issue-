"""Small file readers for product data; research serialization stays frozen."""
import json
from pathlib import Path


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def read_json_or(path, default=None):
    try:
        return read_json(path)
    except (OSError, ValueError):
        return default


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
