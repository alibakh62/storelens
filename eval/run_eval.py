"""Eval harness (SPEC §8.9). Writes eval/out/report.md and eval/out/summary.json (consumed by the deck).

Usage: python eval/run_eval.py [--mode template|live]
"""
import argparse
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("STORELENS_PACING", "0")
os.environ.setdefault("STORELENS_CLOCK", "frozen")

from app.agent.orchestrator import ask  # noqa: E402
from app.agent.providers import chain, live_provider  # noqa: E402
from app.db import get_data  # noqa: E402
from app.demo_refs import resolve as _resolve  # noqa: E402
from app.rbac import CIRCUIT, IPV4, default_personas, scope_for  # noqa: E402
from app.startup import warm  # noqa: E402

ID_RX = re.compile(r"\b(?:INC|CHG|PRB|KB)\d{7}\b")


def resolve(ref):
    d = get_data()
    inc = d.incident
    if ref == "west_store":
        return int(d.stores[d.stores["region"] == "West"]["store_id"].iloc[0])
    if isinstance(ref, str) and ref.endswith("_incident") and ref[:2] in ("S4", "S5", "S6"):
        return inc[(inc["scenario_id"] == ref[:2]) & inc["store_id"].notna()].iloc[3]["number"]
    if ref == "background_p4":
        return inc[inc["priority"].str.startswith("4") & inc["scenario_id"].isna() & inc["store_id"].notna()].iloc[500]["number"]
    if isinstance(ref, str) and re.match(r"^S\d\w*\.\w+\.\d+$", ref):
        a, b, i = ref.split(".")
        return _resolve(f"{a}.{b}")[int(i)]
    return _resolve(ref)


def all_ids():
    d = get_data()
    return set(d.incident["number"]) | set(d.change["number"]) | set(d.problem["number"]) | set(d.kb["number"])


def cited(a):
    ids = set()
    for b in a["bullets"] + [x for s in a["sections"] for x in s["bullets"]] + a["recommended_actions"]:
        ids |= set(b.get("citations", [])) | set(ID_RX.findall(b["text"]))
    ids |= set(ID_RX.findall(a["headline"]))
    return ids


LIVE = None


def run(mode):
    warm()
    valid = all_ids()
    cases = yaml.safe_load((ROOT / "eval" / "golden.yaml").read_text())["cases"]
    P = default_personas()
    rows = []
    for c in cases:
        scope = scope_for(P[c["persona"]])
        store = resolve(c.get("store")) if c.get("store") else None
        inc = resolve(c.get("incident")) if c.get("incident") else None
        t = time.perf_counter()
        r = ask(c["question"], scope, store_id=store, incident_number=inc, force_mode=LIVE if mode == "live" else mode)
        lat = (time.perf_counter() - t) * 1000
        a, v = r["answer"], r["validation"]
        txt = json.dumps(a)
        ids = cited(a)
        # each entry may list alternatives "a|b|c": any one satisfies it (live models phrase things their own way)
        must = ["|".join(str(resolve(a)) for a in x.split("|")) for x in c.get("must_cite", [])]
        found = [m for m in must if any(a in ids or a in txt for a in m.split("|"))]
        facts = [re.sub(r"\{([^}]+)\}", lambda m: str(resolve(m.group(1))), f) for f in c.get("must_contain", [])]
        fact_hits = [f for f in facts if any(a.lower() in txt.lower() for a in f.split("|"))]
        bad = [p for p in c.get("must_not_contain", []) if re.search(p, txt, re.I)]
        leak = 0
        if not scope.can_view_network and (IPV4.search(txt) or CIRCUIT.search(txt)):
            leak = 1
        if not scope.can_view_financials and a.get("impact") and a["impact"].get("usd_so_far") is not None:
            leak = 1
        rows.append(dict(id=c["id"], persona=c["persona"], question=c["question"], mode=r["mode"], latency_ms=round(lat, 1),
                         cited=len(ids), cited_valid=len([i for i in ids if i in valid]), must_cite=len(must),
                         must_cite_found=len(found), facts=len(facts), facts_found=len(fact_hits), must_not_hits=bad,
                         rbac_leak=leak, removed=v.get("removed_count", 0), ungrounded=v.get("ungrounded_count", 0),
                         tokens_in=r.get("trace", {}).get("usage", {}).get("in", 0),
                         tokens_out=r.get("trace", {}).get("usage", {}).get("out", 0),
                         headline=a["headline"], missing=[m for m in must if m not in found] + [f for f in facts if f not in fact_hits]))
    lat = sorted(x["latency_ms"] for x in rows)
    tot_c = sum(x["cited"] for x in rows)
    summ = dict(
        mode=mode, n_cases=len(rows),
        citation_precision=round(sum(x["cited_valid"] for x in rows) / tot_c, 4) if tot_c else 1.0,
        citation_recall=round(sum(x["must_cite_found"] for x in rows) / max(1, sum(x["must_cite"] for x in rows)), 4),
        fact_accuracy=round(sum(x["facts_found"] for x in rows) / max(1, sum(x["facts"] for x in rows)), 4),
        must_not_violations=sum(len(x["must_not_hits"]) for x in rows),
        rbac_leaks=sum(x["rbac_leak"] for x in rows), injection_pass=all(not x["must_not_hits"] for x in rows if x["id"] == "g23"),
        bullets_removed=sum(x["removed"] for x in rows), ungrounded_numbers_removed=sum(x["ungrounded"] for x in rows),
        p50_latency_ms=round(statistics.median(lat), 1), p95_latency_ms=round(lat[int(0.95 * (len(lat) - 1))], 1),
        avg_tokens_in=round(statistics.mean(x["tokens_in"] for x in rows)), avg_tokens_out=round(statistics.mean(x["tokens_out"] for x in rows)))
    out = ROOT / "eval" / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summ, indent=2))
    (out / f"results_{mode}.json").write_text(json.dumps(rows, indent=1))
    L = [f"# StoreLens eval — {mode}", "", "| metric | value |", "|---|---|"] + [f"| {k} | {v} |" for k, v in summ.items()]
    L += ["", "| id | persona | mode | cite recall | facts | rbac leak | removed | ms | headline / missing |", "|---|---|---|---|---|---|---|---|---|"]
    for x in rows:
        L.append(f"| {x['id']} | {x['persona']} | {x['mode']} | {x['must_cite_found']}/{x['must_cite']} | {x['facts_found']}/{x['facts']} | "
                 f"{x['rbac_leak']} | {x['removed']} | {x['latency_ms']:.0f} | {x['headline'][:60]}"
                 + (f" — **missing:** {x['missing']}" if x["missing"] else "") + (f" — **violations:** {x['must_not_hits']}" if x["must_not_hits"] else "") + " |")
    (out / "report.md").write_text("\n".join(L) + "\n")
    print(json.dumps(summ, indent=2))
    for x in rows:
        if x["missing"] or x["must_not_hits"] or x["rbac_leak"]:
            print("FAIL", x["id"], x["headline"], x["missing"], x["must_not_hits"])
    return summ


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="template", choices=["template", "live", "replay"])
    m = ap.parse_args().mode
    from app.config import app_config
    cfg = app_config().get("llm", {})
    names = [cfg.get("provider")] + list(cfg.get("fallback_chain", []))
    LIVE = next((n for n in names if n not in ("replay", "template") and live_provider(n)), None)
    if m == "live" and LIVE is None:
        print("No live provider available — running template mode.")
        m = "template"
    globals()["LIVE"] = LIVE
    s = run(m)
    sys.exit(0 if s["rbac_leaks"] == 0 and s["citation_precision"] >= 0.95 else 1)
