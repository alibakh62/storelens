"""Shift handoff digest (SPEC §7.8): deterministic JSON first; the LLM narrates from it only."""
from __future__ import annotations

import pandas as pd

from ..db import OPEN_STATES, Data
from .chronic import get_chronic
from .health import prio_num, sla_status
from .impact import incident_impact, major_impact

H = pd.Timedelta(hours=1)


def _brief(data, r, now):
    imp = incident_impact(data, r["number"], now)
    return dict(number=r["number"], store_id=int(r["store_id"]) if pd.notna(r["store_id"]) else None,
                priority=r["priority"], short_description=r["short_description"], state=r["state"],
                assignment_group=r["assignment_group"], opened_at=r["opened_at"].isoformat(),
                age_h=round(float((now - r["opened_at"]) / H), 1), sla=sla_status(r, now),
                parent_incident=r["parent_incident"] if pd.notna(r["parent_incident"]) else None,
                est_impact_usd=imp["usd"], region=r["region"])


def build_handoff(data: Data, region: str = "All", as_of: pd.Timestamp | None = None) -> dict:
    now = as_of or data.now()
    inc = data.incident
    reg = (inc["region"] == region) if region and region != "All" else pd.Series(True, index=inc.index)
    op = inc[inc["state"].isin(OPEN_STATES) & (inc["opened_at"] <= now)]
    op_r = op[reg.loc[op.index]]
    majors = []
    for _, m in op[op["major_incident"]].iterrows():
        kids = inc[inc["parent_incident"] == m["number"]]
        if region != "All" and not (kids["region"] == region).any():
            continue
        mi = major_impact(data, m["number"], now)
        majors.append(dict(number=m["number"], short_description=m["short_description"], state=m["state"],
                           opened_at=m["opened_at"].isoformat(), stores=mi["stores"], usd_so_far=mi["usd_so_far"],
                           per_hour_now=mi["per_hour_now"],
                           stores_in_region=int(kids[kids["region"] == region]["store_id"].nunique()) if region != "All"
                           else mi["stores"]))
    store_open = op_r[~op_r["major_incident"]]
    p12 = store_open[store_open["priority"].map(prio_num) <= 2].sort_values(["priority", "opened_at"])
    sla_rows = []
    for _, r in store_open.iterrows():
        s = sla_status(r, now)
        if s["status"] == "breached" or (s["remaining_h"] is not None and s["remaining_h"] < 2):
            sla_rows.append(r)
    hold = store_open[(store_open["state"] == "On Hold") & (store_open["opened_at"] < now - 24 * H)]
    ch = get_chronic(data, region=region if region != "All" else None, limit=500)
    new_chronic = [c for c in ch["items"] if pd.Timestamp(c["last_incident"]) > now - 7 * 24 * H][:10]
    c = data.change
    nxt = c[(c["planned_start"] >= now) & (c["planned_start"] <= now + 24 * H) & (c["state"] == "Scheduled")]
    if region != "All":
        regional = set(data.change_map.merge(data.stores[["store_id", "region"]], on="store_id")
                       .query("region == @region")["change_number"])
        nxt = nxt[nxt["number"].isin(regional) | nxt["number"].isin(data.enterprise_changes)]
    res = inc[reg & inc["resolved_at"].between(now - 12 * H, now) & ~inc["major_incident"]]
    res = res[res["priority"].map(prio_num) <= 2].sort_values("resolved_at", ascending=False)
    counts = op[~op["major_incident"]].groupby("region").size().to_dict()
    sections = dict(
        majors=majors,
        p1_p2_open=[_brief(data, r, now) for _, r in p12.head(15).iterrows()],
        p1_p2_open_count=int(len(p12)),
        sla_risk=[_brief(data, r, now) for r in sla_rows[:15]], sla_risk_count=len(sla_rows),
        on_hold_24h=[_brief(data, r, now) for _, r in hold.head(15).iterrows()], on_hold_24h_count=int(len(hold)),
        new_chronic=new_chronic,
        changes_next_24h=[dict(number=r.number, short_description=r.short_description, planned_start=r.planned_start.isoformat(),
                               scope=r.scope, risk=r.risk) for r in nxt.sort_values("planned_start").itertuples()][:15],
        notable_resolutions=[dict(number=r.number, store_id=int(r.store_id) if pd.notna(r.store_id) else None,
                                  short_description=r.short_description, resolved_at=r.resolved_at.isoformat(),
                                  close_notes=r.close_notes) for r in res.head(10).itertuples()],
        counts_by_region={k: int(v) for k, v in counts.items()},
        open_store_incidents=int(len(store_open)))
    cites = [m["number"] for m in majors] + [x["number"] for x in sections["p1_p2_open"][:5]] + \
            [x["number"] for x in sections["changes_next_24h"][:3]]
    return dict(region=region, as_of=now.isoformat(), sections=sections, citations=list(dict.fromkeys(cites)))
