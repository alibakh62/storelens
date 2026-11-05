"""make record: run every demo question × persona × UI context and write the replay cache.

Uses the configured live provider when one is available (LLM_API_KEY etc.); otherwise records the
template-mode answer (marked recorded_from=template) so the replay tier is still exercised.
"""
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("STORELENS_PACING", "0")

from app.agent.orchestrator import ask  # noqa: E402
from app.agent.providers import chain, live_provider  # noqa: E402
from app.demo_refs import resolve  # noqa: E402
from app.rbac import default_personas, scope_for  # noqa: E402
from app.startup import warm  # noqa: E402


def contexts(q, store):
    man_open = __import__("app.db", fromlist=["get_data"]).get_data().manifest["demo_store_open_incidents"]
    if q.get("incident"):
        return [resolve(q["incident"])]
    return [None] + (list(man_open) if store else [])


def main():
    warm()
    spec = yaml.safe_load((ROOT / "demo" / "questions.yaml").read_text())
    live = next((n for n in chain() if n not in ("replay", "template") and live_provider(n)), None)
    mode = live or "template"
    n = 0
    for who, uid in default_personas().items():
        scope = scope_for(uid)
        for q in spec["questions"]:
            store = resolve(q.get("store", spec["store"]))
            for inc in contexts(q, store):
                r = ask(q["text"], scope, store_id=store, incident_number=inc, force_mode=mode, record=True)
                n += 1
                print(f"[{who}] {q['id']:<12} inc={inc or '-':<11} mode={r['mode']:<9} removed={r['validation'].get('removed_count', 0)}"
                      f"  {r['answer']['headline'][:70]}")
    print(f"recorded {n} turns from {mode}")


if __name__ == "__main__":
    main()
