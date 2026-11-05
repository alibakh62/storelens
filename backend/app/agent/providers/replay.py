"""Replay cache: whole recorded turns keyed by sha256(persona_role, normalized_question, store_context, digest)."""
from __future__ import annotations

import hashlib
import json
import re

from ...config import cache_dir


def norm_question(q: str) -> str:
    q = q.lower().strip()
    q = re.sub(r"[^a-z0-9$ ]+", " ", q)
    return re.sub(r"\s+", " ", q).strip()


def key(persona: str, question: str, store_id, incident_number, context_digest: str) -> str:
    raw = json.dumps([persona, norm_question(question), str(store_id or ""), str(incident_number or ""), context_digest])
    return hashlib.sha256(raw.encode()).hexdigest()


def path_for(k: str):
    d = cache_dir() / "replay"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{k}.json"


def load(k: str) -> dict | None:
    p = path_for(k)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return None
    return None


def save(k: str, record: dict):
    path_for(k).write_text(json.dumps(record, default=str, indent=1))
