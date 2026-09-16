from __future__ import annotations
import json
import re
from pathlib import Path


def atomic_json(path: Path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def safe_error(exc):
    text = str(exc)
    text = re.sub(r'(?i)(api[_-]?key[=:\s]+|authorization[=:\s]+)[^\s,}]+', r'\1[redacted]', text)
    text = re.sub(r'AIza[\w-]+|sk-[\w-]+|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[redacted]', text)
    return text[:1200]
