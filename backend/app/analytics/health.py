"""Store profile and health (SPEC §7.1)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..db import OPEN_STATES, Data, clean, net_row, store_row
from . import changes as changes_mod
from . import chronic as chronic_mod
from .impact import incident_impact, store_impact

PRIO_PENALTY = {1: 35, 2: 20, 3: 8, 4: 3, 5: 1}
SERVICE_FLAGS = [("has_fuel_station", "Fuel"), ("has_7now", "7NOW"), ("has_7md", "7MD"), ("has_dex", "DEX"),
                 ("has_mobile_checkout", "Mobile checkout"), ("has_self_checkout_kiosk", "Self-checkout"),
                 ("has_ev_charging", "EV"), ("has_car_wash", "Car wash"), ("has_lottery", "Lottery"),
                 ("has_atm", "ATM"), ("has_hot_foods", "Hot foods")]
_PCT_CACHE: dict = {}


def prio_num(p) -> int:
    try:
        return int(str(p)[0])
    except (ValueError, IndexError):
        return 5


def sla_status(r, now: pd.Timestamp) -> dict:
    due, target = r["sla_due"], float(r["sla_target_hours"] or 0)
    if pd.isna(due):
        return dict(status="ok", due=None, remaining_h=None)
    rem = (due - now) / pd.Timedelta(hours=1)
    if rem < 0:
        st = "breached"
    elif target and rem < 0.25 * target:
        st = "at_risk"
    else:
        st = "ok"
    return dict(status=st, due=due.isoformat(), remaining_h=round(float(rem), 2))


def services(s) -> list[dict]:
    chips = []
    for col, label in SERVICE_FLAGS:
        if bool(s.get(col)):
            detail = None
            if col == "has_fuel_station":
                detail = f"{int(s['num_fuel_dispensers'])} pumps"
            elif col == "has_ev_charging":
                detail = f"{int(s['num_ev_chargers'])} chargers"
            elif col == "has_self_checkout_kiosk":
                detail = f"{int(s['num_self_checkout_kiosks'])} kiosks"
            chips.append(dict(key=col, label=label, detail=detail))
    if bool(s.get("has_fuel_station")) and isinstance(s.get("fuel_types"), str):
        ft = [f.strip() for f in str(s["fuel_types"]).replace("|", "/").split("/")]
        if "Diesel" in ft:
            chips.insert(1, dict(key="diesel", label="Diesel", detail=None))
    return chips


def get_store_profile(data: Data, store_id: int) -> dict:
    s = store_row(data, store_id)
    if s is None:
        raise KeyError(f"Store {store_id} not found")
    n = net_row(data, store_id)
    cis = data.cmdb[data.cmdb["store_id"] == int(store_id)]
    prof = {k: clean(s[k]) for k in s.index}
    net = {k: clean(n[k]) for k in n.index} if n is not None else {}
    return dict(store_id=int(store_id), store=prof, services=services(s), network=net,
                ci_counts={k: int(v) for k, v in cis["ci_class"].value_counts().items()},
                district=dict(id=s["district_id"], name=s["district_name"], manager=s["district_manager"]),
                citations=[])


def _fleet_percentile(data: Data, store_id: int, as_of: pd.Timestamp) -> tuple[float, int, float]:
    key = as_of.floor("h")
    if key not in _PCT_CACHE:
        inc = data.incident
        m = inc[inc["store_id"].notna() & (inc["opened_at"] > as_of - pd.Timedelta(days=90)) & (inc["opened_at"] <= as_of)]
        cnt = m.groupby("store_id").size()
        st = data.stores[["store_id", "store_format"]].copy()
        st["n90"] = st["store_id"].map(cnt).fillna(0).astype(int)
        res = m[m["resolved_at"].notna() & (m["resolved_at"] <= as_of)]
        ttr = (res["resolved_at"] - res["opened_at"]) / pd.Timedelta(hours=1)
        _PCT_CACHE.clear()
        _PCT_CACHE[key] = (st, float(np.median(ttr)) if len(ttr) else 0.0)
    st, fleet_mttr = _PCT_CACHE[key]
    me = st[st["store_id"] == int(store_id)].iloc[0]
    peers = st[st["store_format"] == me["store_format"]]["n90"]
    pct = float((peers < me["n90"]).mean() * 100 + (peers == me["n90"]).mean() * 50)
    return round(pct, 1), int(me["n90"]), fleet_mttr


def open_incident_summary(data: Data, r, now: pd.Timestamp) -> dict:
    imp = incident_impact(data, r["number"], now)
    age_h = (now - r["opened_at"]) / pd.Timedelta(hours=1)
    return dict(number=r["number"], priority=r["priority"], priority_num=prio_num(r["priority"]),
                short_description=r["short_description"], state=r["state"], on_hold_reason=clean(r["on_hold_reason"]),
                issue_type=r["issue_type"], category=r["category"], cmdb_ci=clean(r["cmdb_ci"]),
                assignment_group=r["assignment_group"], opened_at=r["opened_at"].isoformat(), age_h=round(float(age_h), 2),
                sla=sla_status(r, now), parent_incident=clean(r["parent_incident"]),
                est_impact_usd=imp["usd"], per_hour_now_usd=imp["per_hour_now"], impact_profile=r["impact_profile"],
                vendor_ticket=clean(r["vendor_ticket"]), caused_by_change=clean(r["caused_by_change"]),
                problem_id=clean(r["problem_id"]))


def get_store_health(data: Data, store_id: int, as_of: pd.Timestamp | None = None) -> dict:
    as_of = as_of or data.now()
    s = store_row(data, store_id)
    if s is None:
        raise KeyError(f"Store {store_id} not found")
    inc = data.incident
    mine = inc[(inc["store_id"] == int(store_id)) & (inc["opened_at"] <= as_of)]
    open_ = mine[mine["state"].isin(OPEN_STATES)].copy()
    open_["pn"] = open_["priority"].map(prio_num)
    open_ = open_.sort_values(["pn", "opened_at"])
    open_list = [open_incident_summary(data, r, as_of) for _, r in open_.iterrows()]

    def cnt(days):
        return int((mine["opened_at"] > as_of - pd.Timedelta(days=days)).sum())

    pct, n90, fleet_mttr = _fleet_percentile(data, store_id, data.clock.mock_now)
    m90 = mine[mine["opened_at"] > as_of - pd.Timedelta(days=90)]
    res = m90[m90["resolved_at"].notna()]
    mttr = float(np.median((res["resolved_at"] - res["opened_at"]) / pd.Timedelta(hours=1))) if len(res) else None
    top = m90["category"].value_counts().head(3)
    flags = chronic_mod.chronic_flags_for_store(data, store_id)
    ch = changes_mod.store_changes(data, store_id, as_of)
    lost = store_impact(data, store_id, 365, as_of)

    # health score
    breakdown, score = [], 100.0
    for o in open_list:
        p = PRIO_PENALTY.get(o["priority_num"], 1)
        breakdown.append(dict(reason=f"Open P{o['priority_num']}: {o['number']}", points=-p, citation=o["number"]))
        score -= p
    if flags:
        p = min(20, 10 * len(flags))
        breakdown.append(dict(reason=f"{len(flags)} chronic device flag(s)", points=-p, citation=None))
        score -= p
    if pct >= 90:
        breakdown.append(dict(reason=f"90-day incident count in the top 10% of {s['store_format']} stores", points=-10,
                              citation=None))
        score -= 10
    nb = sum(1 for o in open_list if o["sla"]["status"] == "breached")
    if nb:
        p = min(15, 5 * nb)
        breakdown.append(dict(reason=f"{nb} open incident(s) past SLA", points=-p, citation=None))
        score -= p
    score = max(0.0, score)
    band = "Healthy" if score >= 80 else ("Watch" if score >= 50 else "Critical")

    # store-level open impact, deduplicated within one parent (max per parent) like the major-incident rule
    best: dict = {}
    for o in open_list:
        k = o["parent_incident"] or o["number"]
        if k not in best or o["est_impact_usd"] > best[k]["est_impact_usd"]:
            best[k] = o
    open_usd = round(sum(o["est_impact_usd"] for o in best.values()), 2)
    open_ph = round(sum(o["per_hour_now_usd"] for o in best.values()), 2)
    ups = sorted(ch["upcoming"], key=lambda c: (c["enterprise"], c["planned_start"] or ""))
    ch["upcoming"] = ups
    cites = [o["number"] for o in open_list] + [c for f in flags for c in f["citations"]] + \
            [c["number"] for c in ch["upcoming"]] + [c["number"] for c in ch["recent"]]
    return dict(store_id=int(store_id), store_name=s["store_name"], city=s["city"], state=s["state"],
                region=s["region"], market=s["market"], store_format=s["store_format"], banner=s["banner"],
                utc_offset_std=int(s["utc_offset_std"]), timezone=s["timezone"], as_of=as_of.isoformat(),
                health_score=int(round(score)), band=band, score_breakdown=breakdown,
                open_incidents=open_list, counts_30d=cnt(30), counts_90d=cnt(90), counts_365d=cnt(365),
                fleet_percentile_90d=pct, mttr_90d_hours=round(mttr, 2) if mttr is not None else None,
                fleet_mttr_90d_hours=round(fleet_mttr, 2),
                top_categories_90d=[dict(category=k, count=int(v)) for k, v in top.items()],
                chronic_flags=flags, upcoming_changes=ch["upcoming"], recent_changes=ch["recent"],
                lost_sales_365d_usd=lost["usd"], open_impact_usd=open_usd, open_impact_per_hour_usd=open_ph,
                citations=list(dict.fromkeys(cites)))
