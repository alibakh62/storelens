"""Agent tools (SPEC §8.3). Every call goes through RBAC scope checks and rbac.mask()."""
from __future__ import annotations

import time
from typing import Any

import pandas as pd

from ..analytics import blast, changes, chronic, handoff, health, impact, kb, nlq, pir, similar, timeline
from ..db import OPEN_STATES, clean, get_data, inc_row, store_row
from ..rbac import MaskStats, Scope, ScopeError, check_store, in_scope_store, mask

S = {"type": "string"}
I = {"type": "integer"}

TOOL_SPECS: list[dict] = [
    dict(name="get_store_profile",
         description="Store master data: format, banner, services (fuel, 7NOW, 7MD, DEX...), district, network summary. "
                     "Use when asked what a store is or what services/equipment it has.",
         parameters={"type": "object", "properties": {"store_id": I}, "required": ["store_id"]}),
    dict(name="get_store_health",
         description="Store health card: open incidents with SLA and $ impact, 30/90/365-day counts, MTTR, chronic flags, "
                     "recent/upcoming changes, health score. Use for 'what's going on at store X'.",
         parameters={"type": "object", "properties": {"store_id": I}, "required": ["store_id"]}),
    dict(name="list_incidents",
         description="List incidents (summary fields) filtered by store, region, state, category, days, priority. "
                     "Use for incident history questions.",
         parameters={"type": "object", "properties": {"store_id": I, "region": S, "state": S, "category": S,
                                                      "days": I, "priority": I, "limit": I}}),
    dict(name="get_incident",
         description="One incident with all fields and (optionally) its work notes. Use for details of a specific INC.",
         parameters={"type": "object", "properties": {"number": S, "include_notes": {"type": "boolean"}},
                     "required": ["number"]}),
    dict(name="get_incident_timeline",
         description="Stitched timeline across a parent major incident, its children and work notes, with a summary "
                     "(first alert, declared, ISP ticket, duration). Use for 'what happened', 'timeline', 'status of the outage'.",
         parameters={"type": "object", "properties": {"number": S}, "required": ["number"]}),
    dict(name="find_similar_incidents",
         description="Similar resolved incidents (this device → this store → fleet), the fix-type table with 30-day "
                     "recurrence ('which fix holds') and a deterministic recommendation. Use for 'what fixed this last "
                     "time', 'has this happened before', 'similar'.",
         parameters={"type": "object", "properties": {"number": S, "text": S, "k": I}}),
    dict(name="detect_blast_radius",
         description="Cross-store clustering around an incident: affected stores, anomaly vs baseline, likely common "
                     "cause (attribute lift), map data. Use when the user asks if other stores are affected, if something "
                     "is widespread, or about an outage's scope.",
         parameters={"type": "object", "properties": {"number": S, "store_id": I}}),
    dict(name="correlate_changes",
         description="Changes touching the store/enterprise in the 72h lookback, scored with fleet evidence (rollout "
                     "risk ratio / z-score), negative evidence, and upcoming scheduled changes. Use for 'did anything "
                     "change', 'deploy', 'scheduled', 'caused by a change'.",
         parameters={"type": "object", "properties": {"number": S, "store_id": I}}),
    dict(name="get_chronic",
         description="Chronic devices/stores with repeat failures, 12-month cost and permanent-fix recommendation. Use "
                     "for 'repeat', 'chronic', 'keeps failing'.",
         parameters={"type": "object", "properties": {"store_id": I, "region": S}}),
    dict(name="estimate_impact",
         description="Business impact in $ (lost sales estimate) for an incident (majors: deduplicated sum of children) "
                     "or a store over N days, with per-hour-now rate. Use for cost / $ / impact / lost sales.",
         parameters={"type": "object", "properties": {"number": S, "store_id": I, "days": I}}),
    dict(name="get_shift_handoff",
         description="Shift handoff JSON for a region: majors, P1/P2, SLA risk, on hold > 24h, new chronic flags, "
                     "changes next 24h, notable resolutions.",
         parameters={"type": "object", "properties": {"region": S}}),
    dict(name="get_pir_facts",
         description="Facts for a post-incident review / interim summary of a major incident.",
         parameters={"type": "object", "properties": {"number": S}, "required": ["number"]}),
    dict(name="run_analytics_query",
         description="Ad-hoc analytics over curated read-only views (text-to-SQL). Use for fleet-wide counts, rankings, "
                     "trends that other tools don't cover.",
         parameters={"type": "object", "properties": {"question": S}, "required": ["question"]}),
    dict(name="search_kb",
         description="Search knowledge base articles (troubleshooting steps). Use for fix steps / how-to.",
         parameters={"type": "object", "properties": {"query": S, "issue_type": S}, "required": ["query"]}),
]
TOOL_NAMES = {t["name"] for t in TOOL_SPECS}

ACTIVITY = {
    "get_store_profile": "Reading the store profile…", "get_store_health": "Checking store health…",
    "list_incidents": "Pulling incident history…", "get_incident": "Opening the ticket…",
    "get_incident_timeline": "Stitching parent and child tickets…", "find_similar_incidents": "Looking up past fixes…",
    "detect_blast_radius": "Checking other stores…", "correlate_changes": "Checking recent changes…",
    "get_chronic": "Looking for repeat failures…", "estimate_impact": "Estimating business impact…",
    "get_shift_handoff": "Building the shift handoff…", "get_pir_facts": "Gathering incident facts…",
    "run_analytics_query": "Running an analytics query…", "search_kb": "Searching the knowledge base…",
}


def _check_incident(scope: Scope, number: str):
    data = get_data()
    r = inc_row(data, number)
    if r is None:
        raise ScopeError(f"Incident {number} not found.")
    if scope.region_scope == "All":
        return r
    if pd.notna(r["store_id"]):
        check_store(scope, int(r["store_id"]))
    else:
        kids = data.incident[data.incident["parent_incident"] == number]
        if len(kids) and not (kids["region"] == scope.region_scope).any():
            raise ScopeError(f"{number} does not affect your {scope.region_scope} region.")
    return r


def _inc_summary(r) -> dict:
    return dict(number=r["number"], store_id=clean(r["store_id"]), priority=r["priority"], state=r["state"],
                category=r["category"], issue_type=r["issue_type"], short_description=r["short_description"],
                opened_at=clean(r["opened_at"]), resolved_at=clean(r["resolved_at"]), cmdb_ci=clean(r["cmdb_ci"]),
                assignment_group=r["assignment_group"], close_code=clean(r["close_code"]),
                close_notes=clean(r["close_notes"]), parent_incident=clean(r["parent_incident"]),
                time_to_resolve_hours=clean(r["time_to_resolve_hours"]), region=clean(r["region"]),
                est_impact_usd=clean(r.get("computed_loss_usd")))


def list_incidents(scope: Scope, store_id=None, region=None, state=None, category=None, days=None, priority=None,
                   limit=20) -> dict:
    data = get_data()
    inc = data.incident
    m = pd.Series(True, index=inc.index)
    if store_id is not None:
        check_store(scope, store_id)
        m &= inc["store_id"] == int(store_id)
    if scope.region_scope != "All":
        region = scope.region_scope
    if region and region != "All":
        m &= inc["region"] == region
    if state:
        m &= inc["state"].isin(OPEN_STATES) if state.lower() == "open" else inc["state"].str.lower() == state.lower()
    if category:
        m &= inc["category"].str.lower() == category.lower()
    if days:
        m &= inc["opened_at"] > data.now() - pd.Timedelta(days=int(days))
    if priority:
        m &= inc["priority"].str.startswith(str(priority))
    m &= inc["opened_at"] <= data.now()
    rows = inc[m].sort_values("opened_at", ascending=False)
    limit = min(int(limit or 20), 50)
    items = [_inc_summary(r) for _, r in rows.head(limit).iterrows()]
    return dict(total=int(len(rows)), items=items, citations=[i["number"] for i in items])


def get_incident(scope: Scope, number: str, include_notes: bool = True) -> dict:
    data = get_data()
    r = _check_incident(scope, number)
    d = {k: clean(r[k]) for k in r.index if not k.startswith("loss_") and k not in ("grp",)}
    d["est_impact_usd"] = impact.incident_impact(data, number)["usd"]
    d.pop("est_lost_sales_usd", None)  # validation-only column (§4.4); the app computes impact itself
    d.pop("computed_loss_usd", None)
    if include_notes:
        idx = data.notes_by_inc.get(number)
        notes = data.notes.iloc[idx] if idx is not None else data.notes.iloc[0:0]
        d["work_notes"] = [dict(ts=n.created_at.isoformat(), author=n.author, entry_type=n.entry_type, text=n.text)
                           for n in notes.itertuples() if n.created_at <= data.now()]
    d["citations"] = [number] + ([d["parent_incident"]] if d.get("parent_incident") else []) + \
                     ([d["problem_id"]] if d.get("problem_id") else [])
    return d


def _filter_blast(res: dict, scope: Scope) -> dict:
    if scope.region_scope == "All":
        return res
    keep = [s for s in res["stores"] if s["region"] == scope.region_scope]
    res["out_of_region_store_count"] = len(res["stores"]) - len(keep)
    res["stores"] = keep
    res["suggested_links"] = [s for s in res["suggested_links"] if in_scope_store(scope, s["store_id"])]
    return res


def execute(name: str, args: dict, scope: Scope) -> tuple[Any, dict]:
    """Run one tool. Returns (masked_result, trace_record)."""
    data = get_data()
    args = {k: v for k, v in (args or {}).items() if v is not None and v != ""}
    t0 = time.perf_counter()
    stats = MaskStats()
    try:
        res = _dispatch(name, args, scope, data)
    except ScopeError as e:
        res = dict(error=str(e), restricted=True, citations=[])
    except KeyError as e:
        res = dict(error=f"Not found: {e}", citations=[])
    except (ValueError, TypeError) as e:
        res = dict(error=f"Bad arguments: {e}", citations=[])
    res = mask(res, scope, stats)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    rows = _rowcount(res)
    return res, dict(name=name, args=args, ms=ms, rows=rows, masked_fields=stats.count,
                     error=res.get("error") if isinstance(res, dict) else None)


def _rowcount(res) -> int:
    if not isinstance(res, dict):
        return 0
    for k in ("items", "stores", "events", "rows", "hits", "candidates", "open_incidents"):
        if isinstance(res.get(k), list):
            return len(res[k])
    if isinstance(res.get("tiers"), dict):
        return sum(len(v) for v in res["tiers"].values())
    return 1


def _store_filter(scope):
    return None if scope.region_scope == "All" else (lambda s: in_scope_store(scope, s))


def _dispatch(name: str, a: dict, scope: Scope, data) -> dict:
    if name == "get_store_profile":
        check_store(scope, a["store_id"])
        return health.get_store_profile(data, int(a["store_id"]))
    if name == "get_store_health":
        check_store(scope, a["store_id"])
        return health.get_store_health(data, int(a["store_id"]))
    if name == "list_incidents":
        return list_incidents(scope, **{k: a[k] for k in a if k in
                                        ("store_id", "region", "state", "category", "days", "priority", "limit")})
    if name == "get_incident":
        return get_incident(scope, a["number"], bool(a.get("include_notes", True)))
    if name == "get_incident_timeline":
        _check_incident(scope, a["number"])
        res = timeline.get_incident_timeline(data, a["number"])
        if scope.region_scope != "All":
            for e in res["events"]:
                if e.get("children"):
                    e["children"] = [c for c in e["children"] if c["store_id"] is None or in_scope_store(scope, c["store_id"])]
            res["suggested_links"] = [s for s in res["suggested_links"] if in_scope_store(scope, s["store_id"])]
        return res
    if name == "find_similar_incidents":
        if a.get("number"):
            _check_incident(scope, a["number"])
        return similar.find_similar(data, a.get("number"), a.get("text"), int(a.get("k", 10)),
                                    store_filter=_store_filter(scope))
    if name == "detect_blast_radius":
        if a.get("number"):
            _check_incident(scope, a["number"])
        elif a.get("store_id") is not None:
            check_store(scope, a["store_id"])
        else:
            raise ValueError("number or store_id required")
        return _filter_blast(blast.detect_blast_radius(data, a.get("number"), a.get("store_id")), scope)
    if name == "correlate_changes":
        if a.get("number"):
            _check_incident(scope, a["number"])
        elif a.get("store_id") is not None:
            check_store(scope, a["store_id"])
        return changes.correlate_changes(data, a.get("number"), a.get("store_id"))
    if name == "get_chronic":
        if a.get("store_id") is not None:
            check_store(scope, a["store_id"])
        region = scope.region_scope if scope.region_scope != "All" else a.get("region")
        return chronic.get_chronic(data, a.get("store_id"), region, limit=25)
    if name == "estimate_impact":
        if a.get("number"):
            _check_incident(scope, a["number"])
        elif a.get("store_id") is not None:
            check_store(scope, a["store_id"])
        return impact.estimate_impact(data, a.get("number"), a.get("store_id"), int(a.get("days", 365)))
    if name == "get_shift_handoff":
        region = scope.region_scope if scope.region_scope != "All" else a.get("region", "All")
        return handoff.build_handoff(data, region)
    if name == "get_pir_facts":
        _check_incident(scope, a["number"])
        return pir.pir_facts(data, a["number"])
    if name == "run_analytics_query":
        from .providers import nlq_sql_fn
        return nlq.answer(data, scope, a["question"], nlq_sql_fn(scope))
    if name == "search_kb":
        return kb.search_kb(data, a["query"], a.get("issue_type"))
    raise ValueError(f"unknown tool {name}")
