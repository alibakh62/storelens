"""Post-incident review facts (SPEC §7.9)."""
from __future__ import annotations

import pandas as pd

from ..db import Data, clean, inc_row
from .changes import correlate_changes
from .chronic import get_chronic
from .impact import incident_impact
from .timeline import get_incident_timeline


def pir_facts(data: Data, number: str) -> dict:
    r = inc_row(data, number)
    if r is None:
        raise KeyError(number)
    if pd.notna(r["parent_incident"]):
        number = r["parent_incident"]
        r = inc_row(data, number)
    now = data.now()
    tl = get_incident_timeline(data, number)
    imp = incident_impact(data, number, now)
    kids = data.incident[data.incident["parent_incident"] == number]
    key = [e for e in tl["events"] if e.get("emphasis") in ("parent", "self") and e["kind"] in
           ("opened", "work_notes", "resolved")] + [e for e in tl["events"] if e["kind"] == "child_group"][:1]
    key.sort(key=lambda e: e["ts"])
    prb = data.problem[data.problem["number"] == r["problem_id"]] if pd.notna(r["problem_id"]) else data.problem.iloc[0:0]
    p = prb.iloc[0] if len(prb) else None
    chg = correlate_changes(data, number)
    stores = set(kids["store_id"].dropna().astype(int))
    chronic = [c for c in get_chronic(data, limit=1000)["items"] if c["store_id"] in stores][:10]
    linked_changes = [x["change"]["number"] for x in chg["candidates"] if x["score"] > 0.4]
    if pd.notna(r["caused_by_change"]):
        linked_changes = [r["caused_by_change"]] + [c for c in linked_changes if c != r["caused_by_change"]]
    open_ = pd.isna(r["resolved_at"])
    facts = dict(
        number=number, title=r["short_description"], state=r["state"], interim=bool(open_),
        doc_title="Interim incident summary" if open_ else "Post-incident review",
        impact=dict(stores=tl["summary"].get("stores", 0), offline=tl["summary"].get("stores_offline", 0),
                    degraded=tl["summary"].get("stores_degraded", 0), duration_h=tl["summary"].get("duration_h"),
                    usd=imp.get("usd_so_far", imp.get("usd")), per_hour_now=imp.get("per_hour_now"),
                    child_incidents=int(len(kids))),
        timeline=[dict(ts=e["ts"], text=e["text"], incident_number=e.get("incident_number")) for e in key][:14],
        root_cause=dict(parent_close_notes=clean(r["close_notes"]),
                        problem=p["number"] if p is not None else None,
                        problem_title=p["short_description"] if p is not None else None,
                        cause_notes=clean(p["cause_notes"]) if p is not None else None,
                        problem_state=p["state"] if p is not None else None),
        resolution=clean(r["close_notes"]) if not open_ else None,
        workaround=clean(p["workaround"]) if p is not None else None,
        linked_changes=linked_changes, change_negative_evidence=chg["negative_evidence"],
        followups=dict(open_problem=p["number"] if p is not None and p["state"] != "Closed" else None,
                       chronic_among_affected=[dict(store_id=c["store_id"], unit=c["unit"], count_365d=c["count_365d"])
                                               for c in chronic],
                       unlinked_lookalikes=[s["number"] for s in tl["suggested_links"]][:10]),
        minutes_to_declare=tl["summary"].get("minutes_to_declare"), first_alert=tl["summary"].get("first_alert"),
        major_declared=tl["summary"].get("major_declared"), isp_ticket=tl["summary"].get("isp_ticket"))
    facts["citations"] = list(dict.fromkeys([number] + ([p["number"]] if p is not None else []) + linked_changes[:2]
                                            + [e["incident_number"] for e in key if e.get("incident_number")][:3]))
    return facts
