"""Append-only JSONL audit log."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

from .config import audit_dir

_lock = threading.Lock()


def path():
    return audit_dir() / "audit.jsonl"


def write(record: dict):
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record}
    with _lock, open(path(), "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def read(limit: int = 200) -> list[dict]:
    p = path()
    if not p.exists():
        return []
    lines = p.read_text().splitlines()[-limit:]
    out = []
    for ln in reversed(lines):
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    return out


class Timer:
    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.ms = round((time.perf_counter() - self.t) * 1000, 1)
