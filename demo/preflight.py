"""make preflight (SPEC §11.1): data present, manifest IDs resolve, replay covers 100% of demo questions,
e2e flow passes in template AND replay mode, LLM ping (warn only)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "demo"))
os.environ.setdefault("STORELENS_PACING", "0")
ok = True


def check(name, cond, warn=False):
    global ok
    print(("✔" if cond else ("⚠" if warn else "✘")), name)
    if not cond and not warn:
        ok = False


import yaml  # noqa: E402

check("data present", (ROOT / "data/raw/incident.csv").exists() and (ROOT / "data/raw/scenario_manifest.json").exists())
from app.agent.providers import chain, live_provider  # noqa: E402
from app.agent.providers import replay as rp  # noqa: E402
from app.db import get_data, inc_row  # noqa: E402
from app.demo_refs import resolve  # noqa: E402
from app.rbac import default_personas, scope_for  # noqa: E402
from app.startup import warm  # noqa: E402

warm()
d = get_data()
m = d.manifest
ids = [m["demo_store_id"]] + m["demo_store_open_incidents"] + [s.get("parent_incident") for s in m["scenarios"] if s.get("parent_incident")]
check("manifest IDs resolve", all((inc_row(d, x) is not None) if isinstance(x, str) else int(x) in d.store_idx for x in ids))

spec = yaml.safe_load((ROOT / "demo/questions.yaml").read_text())
miss = 0
total = 0
from app.agent.orchestrator import Turn  # noqa: E402
for who, uid in default_personas().items():
    sc = scope_for(uid)
    for q in spec["questions"]:
        store = resolve(q.get("store", spec["store"]))
        incs = [resolve(q["incident"])] if q.get("incident") else [None] + (m["demo_store_open_incidents"] if store else [])
        for inc in incs:
            total += 1
            h = None
            if store:
                from app.agent.tools import execute
                h, _ = execute("get_store_health", {"store_id": store}, sc)
            digest = ",".join(sorted(o["number"] for o in (h or {}).get("open_incidents", []) or []))
            if rp.load(rp.key(sc.persona, q["text"], store, inc, digest)) is None:
                miss += 1
check(f"replay cache covers demo questions ({total - miss}/{total})", miss == 0)

live = [n for n in chain() if n not in ("replay", "template") and live_provider(n)]
check(f"LLM ping ({live or 'no live provider configured — replay/template only'})", bool(live), warn=True)

try:
    from playwright.sync_api import sync_playwright  # noqa: E402
except ImportError:
    check("e2e demo flow: Playwright not installed — run: uv sync --extra dev --extra llm && uv run playwright install chromium", False)
    print("\nPREFLIGHT RED")
    sys.exit(1)
from flow import Flow  # noqa: E402
from pw import launch  # noqa: E402
from server import server  # noqa: E402

for mode, port in [("template", 8771), ("replay", 8772)]:
    try:
        with server(port=port, provider=mode, pacing="0") as base, sync_playwright() as p:
            b = launch(p)
            pg = b.new_page(viewport={"width": 1536, "height": 864})
            Flow(pg, base, pace=0.1, check=True).run()
            b.close()
        check(f"e2e demo flow passes ({mode} mode)", True)
    except Exception as e:  # noqa: BLE001
        print("   ", e)
        check(f"e2e demo flow passes ({mode} mode)", False)

print("\nPREFLIGHT", "GREEN" if ok else "RED")
sys.exit(0 if ok else 1)
