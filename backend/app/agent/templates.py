"""Template mode (SPEC §8.8): keyword intent router + per-role renderers. No LLM.

Answers are less fluent than the LLM's but correct and cited. Every number is copied from a tool
result (formatted, never computed beyond rounding/summing what the tools return).
"""
from __future__ import annotations

import re

import pandas as pd

from ..clock import fmt_local
from ..db import get_data, inc_row

ID_RX = re.compile(r"\bINC\d{7}\b")
STORE_RX = re.compile(r"(?<![\d-])(\d{5})(?![\d-])")
OPEN = ("New", "In Progress", "On Hold")

INTENTS = [
    ("rbac_network", r"\b(ip|ip address|ips|circuit id|circuit|subnet|router ip)\b"),
    ("handoff", r"\b(handoff|hand-off|shift change|shift report|end of shift)\b"),
    ("pir", r"\b(pir|post-?mortem|postmortem|post-incident|incident summary|write.?up|summar(y|ize) (the|this) (outage|incident))\b"),
    ("chronic", r"\b(repeat|chronic|keeps failing|keep failing|recurring)\b"),
    ("impact", r"(\bcost|\$|\bimpact\b|\blost\b|\bsales\b|\bmoney\b|\bdollars?\b|costing)"),
    ("blast", r"\b(other stores|widespread|affecting|affected|blast|how many stores|spread|common cause|regional|scope)\b"),
    ("changes", r"\b(chang\w*|deploy\w*|scheduled|release|rollout|upgrade|update|maintenance|caus(e|ed|ing))\b"),
    ("fix_history", r"\b(fix\w*|last time|similar|before|history|resolve[ds]?|happened before|worked)\b"),
    ("nlq", r"\b(which stores|how many|trend|top \d+|top stores|by region|by category|by month|by week|average|rate|most)\b"),
    ("store_status", r"\b(what'?s going on|status|what is happening|what's happening|overview|tell me about|health)\b"),
]
FOLLOWUPS = {
    "engineer": ["Is this affecting other stores?", "What fixed this last time?", "Did anything change here recently?",
                 "How much is this costing us?"],
    "exec": ["Is this affecting other stores?", "How much is this costing us?", "What should I tell stakeholders?",
             "Did anything change here recently?"],
    "manager": ["Is this affecting other stores?", "Which of my stores are at SLA risk?", "How much is this costing us?",
                "Did anything change here recently?"],
}


def intent_of(q: str, has_store: bool) -> str:
    ql = q.lower().strip()
    if re.fullmatch(r"\s*\d{5}\s*", ql):
        return "store_status"
    if not has_store and re.search(r"\b(which stores|top \d+|trend|by region|by category|by month|by week|how many)\b", ql) \
            and not re.search(r"\b(handoff|chronic devices)\b", ql):
        return "nlq"
    for name, rx in INTENTS:
        if re.search(rx, ql):
            if name == "nlq" and has_store and not re.search(r"\b(which stores|fleet|all stores|by region)\b", ql):
                continue
            return name
    return "store_status" if has_store else "nlq"


# ---------------------------------------------------------------- formatting helpers
def money(x) -> str:
    if x is None:
        return "restricted"
    x = float(x)
    if abs(x) >= 1e6:
        return f"${x / 1e6:.1f}M"
    if abs(x) >= 1e4:
        return f"${x / 1e3:.1f}k"
    return f"${x:,.0f}"


def hours(h) -> str:
    h = float(h)
    if h < 1:
        return f"{h * 60:.0f} min"
    if h >= 48:
        return f"{h:.0f}h"
    return f"{h:.1f}h"


def b(text, cites=None, sources=None):
    d = dict(text=text, citations=[c for c in (cites or []) if c])
    if sources:
        d["sources"] = sources
    return d


def t_local(ts, off=-6, date=False) -> str:
    return fmt_local(pd.Timestamp(ts), off, with_date=date) if ts else "—"


def _restricted(x) -> bool:
    return x is None or x == "•••• (restricted)"


# ---------------------------------------------------------------- context resolution
def resolve_context(question: str, store_id, incident_number, scope) -> dict:
    data = get_data()
    q = question or ""
    m = ID_RX.search(q)
    if m:
        incident_number = m.group(0)
    if store_id is None:
        ms = STORE_RX.search(q)
        if ms and int(ms.group(1)) in data.store_idx:
            store_id = int(ms.group(1))
    if store_id is None and incident_number:
        r = inc_row(data, incident_number)
        if r is not None and pd.notna(r["store_id"]):
            store_id = int(r["store_id"])
    ql = q.lower()
    focus = None
    if incident_number and inc_row(data, incident_number) is not None:
        focus = incident_number
    elif store_id is not None:
        inc = data.incident
        op = inc[(inc["store_id"] == store_id) & inc["state"].isin(OPEN)].copy()
        if len(op):
            op["pn"] = op["priority"].str[0].astype(int)
            pick = None
            if re.search(r"\b(pump|dispenser|card reader|fuel)\b", ql):
                f = op[op["category"] == "Fuel"]
                pick = f.sort_values("pn").iloc[0] if len(f) else None
            if pick is None and re.search(r"\b(decline|card|payment|pin ?pad)\b", ql):
                f = op[op["category"] == "Payments"]
                pick = f.sort_values("pn").iloc[0] if len(f) else None
            if pick is None:
                pick = op.sort_values(["pn", "opened_at"]).iloc[0]
            focus = pick["number"]
    major = None
    if focus:
        r = inc_row(data, focus)
        major = r["parent_incident"] if pd.notna(r["parent_incident"]) else (focus if bool(r["major_incident"]) else None)
    if major is None and store_id is not None:
        inc = data.incident
        par = inc[(inc["store_id"] == store_id) & inc["state"].isin(OPEN) & inc["parent_incident"].notna()]
        if len(par):
            major = par["parent_incident"].iloc[0]
    if major is None and re.search(r"\b(outage|major|dfw|dallas|spectrum)\b", ql):
        inc = data.incident
        mj = inc[inc["major_incident"] & inc["state"].isin(OPEN)]
        if len(mj):
            major = mj.sort_values("opened_at").iloc[-1]["number"]
            if focus is None:
                focus = major
    return dict(store_id=store_id, focus=focus, major=major)


# ---------------------------------------------------------------- plans
def plan(intent: str, ctx: dict, question: str, persona: str, scope) -> list[tuple[str, dict]]:
    sid, focus, major = ctx["store_id"], ctx["focus"], ctx["major"]
    if intent == "rbac_network":
        return [("get_store_profile", dict(store_id=sid))] if sid else []
    if intent == "store_status":
        p = [] if sid is None else [("get_store_profile", dict(store_id=sid))]
        if major:
            p += [("get_incident_timeline", dict(number=major)), ("estimate_impact", dict(number=major))]
        elif focus:
            p += [("get_incident", dict(number=focus, include_notes=True))]
        return p
    if intent == "blast":
        tgt = focus or major
        p = [("detect_blast_radius", dict(number=tgt) if tgt else dict(store_id=sid))]
        if major:
            p.append(("estimate_impact", dict(number=major)))
        return p
    if intent == "fix_history":
        if not focus:
            return [("find_similar_incidents", dict(text=question))]
        r = inc_row(get_data(), focus)
        p = [("find_similar_incidents", dict(number=focus))]
        p.append(("search_kb", dict(query=str(r["short_description"]), issue_type=r["issue_type"])))
        if sid:
            p.append(("get_chronic", dict(store_id=sid)))
        return p
    if intent == "changes":
        if focus and not (inc_row(get_data(), focus)["major_incident"]):
            return [("correlate_changes", dict(number=focus))]
        if sid:
            return [("correlate_changes", dict(store_id=sid))]
        return [("correlate_changes", dict(number=focus))] if focus else [("correlate_changes", {})]
    if intent == "impact":
        if major:
            return [("estimate_impact", dict(number=major))]
        if focus and sid is None:
            return [("estimate_impact", dict(number=focus))]
        if sid:
            return [("estimate_impact", dict(store_id=sid, days=365))]
        return [("run_analytics_query", dict(question="Top stores by lost sales"))]
    if intent == "handoff":
        region = "Texas" if "texas" in question.lower() else _region_in(question)
        return [("get_shift_handoff", dict(region=region))]
    if intent == "pir":
        return [("get_pir_facts", dict(number=major or focus))] if (major or focus) else []
    if intent == "chronic":
        return [("get_chronic", dict(store_id=sid) if sid else dict(region=_region_in(question)))]
    return [("run_analytics_query", dict(question=question))]


def _region_in(q: str) -> str:
    for r in ["Texas", "West", "Southeast", "Mid-Atlantic", "Northeast", "Central"]:
        if r.lower() in q.lower():
            return r
    return "All"


# ---------------------------------------------------------------- renderers
def render(intent: str, ctx: dict, res: dict, persona: str, scope, question: str) -> dict:
    fn = globals().get(f"_r_{intent}", _r_nlq)
    try:
        out = fn(ctx, res, persona, scope, question)
    except _Refused as e:
        out = dict(headline=str(e), status="info", bullets=[], confidence="high", panels=[])
    out.setdefault("sections", [])
    out.setdefault("recommended_actions", [])
    out.setdefault("followups", [f for f in FOLLOWUPS.get(persona, []) if f.lower() not in question.lower()][:3])
    out.setdefault("confidence", "high")
    out.setdefault("impact", None)
    out.setdefault("panels", [])
    return out


class _Refused(Exception):
    pass


def _err(res: dict):
    for v in res.values():
        if isinstance(v, dict) and v.get("error"):
            raise _Refused(v["error"] if v.get("restricted") else f"I couldn't find that: {v['error']}")


def _store_open_impact(health: dict) -> tuple[float | None, float | None]:
    """Store-level open $ (deduplicated within one parent) — computed by the health tool."""
    return health.get("open_impact_usd"), health.get("open_impact_per_hour_usd")


def _eta(tl: dict | None) -> str | None:
    if not tl:
        return None
    for e in reversed(tl.get("events", [])):
        m = re.search(r"Estimated restoration (\d{1,2}):(\d{2}) local", e.get("text", ""))
        if m:
            h, mi = int(m.group(1)), m.group(2)
            return f"{(h - 1) % 12 + 1}:{mi} {'AM' if h < 12 else 'PM'} CT"
    return None


def _r_store_status(ctx, res, persona, scope, q):
    _err(res)
    h = res.get("get_store_health")
    if not h:
        raise _Refused("Pick a store first — type a store number.")
    prof = res.get("get_store_profile", {})
    tl = res.get("get_incident_timeline")
    mi = res.get("estimate_impact")
    sid, off = h["store_id"], h["utc_offset_std"]
    opens = h["open_incidents"]
    usd, per_h = _store_open_impact(h)
    major = ctx.get("major")
    summ = (tl or {}).get("summary", {})
    net = prof.get("network", {}) if prof else {}
    isp = net.get("primary_isp")
    flags = h["chronic_flags"]
    up = h["upcoming_changes"]
    status = {"Critical": "critical", "Watch": "watch"}.get(h["band"], "healthy")
    impact = dict(usd_so_far=usd, per_hour_now=per_h, as_of=h["as_of"], restricted=usd is None, scope="store")
    top = opens[0] if opens else None
    offline = next((o for o in opens if o["issue_type"] in ("store_offline", "power_outage")), None)
    bullets, actions, sections = [], [], []

    if not opens:
        return dict(headline=f"Store {sid} ({h['city']}) is healthy — no open incidents", status="healthy",
                    bullets=[b(f"{h['counts_90d']} incidents in the last 90 days; health score {h['health_score']}.",
                               h["citations"][:1] or None, ["get_store_health"])],
                    impact=impact, panels=["health"])

    if persona == "exec":
        if offline and major:
            headline = f"Store {sid} ({h['city']}) is offline — part of the {summ.get('stores', '')}-store internet provider outage"
        elif major:
            headline = f"Store {sid} ({h['city']}) is affected by an ongoing major incident"
        else:
            headline = f"Store {sid} ({h['city']}): {len(opens)} open issues, health {h['band'].lower()}"
        lead = []
        if usd is not None:
            lead.append(f"{money(usd)} est. lost sales so far at this store, about {money(per_h)}/hour while it's down")
        if major and summ:
            lead.append(f"one of {summ['stores']} stores hit by the outage ({summ['stores_offline']} cash-only)")
        if lead:
            bullets.append(b("; ".join(lead) + ".", [major or (top and top["number"])]))
        eta = _eta(tl)
        if offline:
            txt = "Card payments and pay-at-pump are down here; the store is on cash only"
            txt += f" until the provider restores service (ETA {eta})." if eta else "."
            bullets.append(b(txt, [offline["number"], major]))
        if flags:
            f = flags[0]
            bullets.append(b(f"Separately, {_friendly_ci(f['unit'])} has failed {f['count_365d']} times in 12 months — "
                             f"a permanent replacement is recommended.", f["citations"][-2:]))
        if major and summ:
            sections.append(dict(title="What to tell stakeholders", bullets=[b(
                f"{summ['stores']} Dallas–Fort Worth stores are on backup internet or cash-only because of a provider outage; "
                + (f"restoration is expected around {eta}." if eta else "the provider is working on restoration."), [major])]))
        return dict(headline=headline, status=status, bullets=bullets[:3], sections=sections, impact=impact,
                    panels=["health", "impact"], confidence="high")

    # engineer / manager
    if major and offline:
        headline = f"Store {sid} is hard down — part of major {major}" + (f" ({isp} outage)" if isp and persona == "engineer" else "")
    elif major:
        headline = f"Store {sid} is affected by major incident {major}"
    else:
        headline = f"Store {sid} ({h['city']}): {len(opens)} open incidents — health {h['band']}"
    for o in opens[:4]:
        age = hours(o["age_h"])
        since = t_local(o["opened_at"], off)
        sla = o["sla"]["status"]
        sla_txt = {"breached": "SLA breached", "at_risk": "SLA at risk", "ok": "SLA ok"}[sla]
        if o["parent_incident"] and o["issue_type"] in ("store_offline", "wan_primary_down"):
            txt = (f"{o['number']} ({o['priority'][:1] and 'P' + o['priority'][0]}): {o['short_description']} — since {since} "
                   f"({age}), {o['state']}, {o['assignment_group']}; linked to major {o['parent_incident']}")
            if summ:
                txt += f" ({summ['stores']} stores, {summ['stores_offline']} offline)"
            bullets.append(b(txt + ".", [o["number"], o["parent_incident"]]))
        elif o["parent_incident"]:
            bullets.append(b(f"{o['number']}: {o['short_description']} — opened {since}; a symptom of the same outage "
                             f"(child of {o['parent_incident']}), no separate fix needed.", [o["number"], o["parent_incident"]]))
        else:
            hold = f" — On Hold ({o['on_hold_reason']}) for {age}" if o["state"] == "On Hold" else f" — {o['state']} for {age}"
            extra = ""
            f = next((f for f in flags if f.get("ci") and f["ci"] == o["cmdb_ci"]), None)
            if f:
                extra = f"; failure #{f['count_365d']} on {o['cmdb_ci'] if persona == 'engineer' else _friendly_ci(o['cmdb_ci'])} in 12 months"
            bullets.append(b(f"{o['number']}: {o['short_description']}{hold}, {sla_txt}{extra}.", [o["number"]]))
    if persona == "engineer" and net:
        circ = net.get("primary_circuit_id")
        parts = [f"Primary {isp} ({net.get('circuit_type')})"]
        if not _restricted(circ):
            parts[0] += f" circuit {circ}"
        parts.append(f"LTE backup {net.get('backup_carrier')}")
        parts.append(f"router {net.get('sdwan_router_model')} on {net.get('router_firmware')}")
        if not _restricted(net.get("router_lan_ip")):
            parts.append(f"router LAN {net.get('router_lan_ip')}")
        if summ.get("isp_ticket"):
            parts.append(f"ISP ticket {summ['isp_ticket']}")
        sections.append(dict(title="Network", bullets=[b("; ".join(parts) + ".", [major] if major else None,
                                                         ["get_store_profile"])]))
    if persona == "manager" and opens:
        risk = [o for o in opens if o["sla"]["status"] in ("breached", "at_risk")]
        if risk:
            sections.append(dict(title="SLA risk", bullets=[b(
                f"{o['number']} {o['sla']['status'].replace('_', ' ')} — due {t_local(o['sla']['due'], off)}.",
                [o["number"]]) for o in risk]))
    if up:
        c = next((u for u in up if not u.get("enterprise")), up[0])
        bullets.append(b(f"Upcoming: {c['number']} {c['short_description'].split(': ', 1)[-1]} — "
                         f"{t_local(c['planned_start'], off, True)}.", [c["number"]]))
    if major:
        actions.append(dict(text=f"Track restoration on parent {major}; no store-level fix until the provider restores service.",
                            owner="Major Incident Management", citations=[major]))
        if persona == "manager" and offline:
            actions.append(dict(text="Put up cash-only signage and route fuel customers to the nearest unaffected store.",
                                owner="Store / District manager", citations=[offline["number"]]))
    for f in flags[:1]:
        actions.append(dict(text=f"{_friendly_ci(f['unit']) if persona != 'engineer' else f['unit']}: {f['recommendation']}",
                            owner="Fuel Systems Support" if f["ci_class"] == "Fuel Dispenser" else "Field Services",
                            citations=f["citations"][-2:]))
    return dict(headline=headline, status=status, bullets=bullets, sections=sections, impact=impact,
                recommended_actions=actions, panels=["health", "timeline"] if major else ["health"],
                confidence="high")


def _friendly_ci(ci: str | None) -> str:
    if not ci:
        return "a device"
    m = re.match(r"S\d{5}-([A-Z0-9]+)-(\d{2})", ci)
    if not m:
        return ci
    kind, n = m.group(1), int(m.group(2))
    return {"DISP": f"pump {n} card reader", "POS": f"register {n}", "PIN": f"PIN pad {n}", "RTR": "the router",
            "CLR": "the walk-in cooler", "7NOW": "the 7NOW tablet", "EV": f"EV charger {n}", "PRN": f"printer {n}"
            }.get(kind, ci)


def _r_blast(ctx, res, persona, scope, q):
    _err(res)
    bl = res["detect_blast_radius"]
    mi = res.get("estimate_impact")
    c = bl["likely_common_cause"]
    n, off_, deg = bl["affected_store_count"], bl["offline_count"], bl["degraded_count"]
    parents = bl["parent_incidents"]
    pnum = parents[0]["number"] if parents else None
    anchor = bl["anchor"]["number"]
    if not bl["unusual"]:
        return dict(headline="No — this looks isolated to this store", status="info",
                    bullets=[b(bl["summary"], [anchor])], panels=["blast"], confidence="medium")
    mk = ", ".join(bl["markets"])
    impact = None
    if mi:
        impact = dict(usd_so_far=mi.get("usd_so_far", mi.get("usd")), per_hour_now=mi.get("per_hour_now"),
                      as_of=mi.get("as_of"), restricted=mi.get("restricted", False), scope="major")
    bullets = []
    if persona == "exec":
        headline = f"Yes — {n} stores in {mk} are hit by one internet provider outage" if c and c["attribute"] == "primary_isp" \
            else f"Yes — {n} stores are affected"
        if c:
            bullets.append(b(f"Common thread: {c['value']} — {c['support']:.0%} of affected stores use it vs "
                             f"{c['base']:.0%} of stores in these markets.", [anchor, pnum]))
        bullets.append(b(f"{off_} stores are fully offline (cash only); {deg} are running on backup internet.", [pnum or anchor]))
        if impact and impact["usd_so_far"] is not None:
            bullets.append(b(f"{money(impact['usd_so_far'])} est. lost sales so far, about {money(impact['per_hour_now'])}/hour.",
                             [pnum]))
        return dict(headline=headline, status="critical", bullets=bullets, impact=impact, panels=["blast", "impact"])
    headline = f"{n} stores affected" + (f" — likely common cause: {c['value']} (lift {c['lift']:.1f}×)" if c else "")
    if c:
        bullets.append(b(f"Likely cause: {c['explanation']}", [anchor, pnum]))
    bullets.append(b(f"Unusual: {bl['observed']} {bl['anchor']['group']} tickets in the window vs {bl['expected']:.1f} "
                     f"expected for this weekday/hour (Poisson p < 0.01).", [anchor]))
    geo = bl.get("geo") or {}
    bullets.append(b(f"{off_} offline, {deg} degraded across {mk}" +
                     (f"; max radius {geo['max_radius_km']:.0f} km from the centroid." if geo else "."), [pnum or anchor]))
    if pnum:
        bullets.append(b(f"All linked under parent {pnum} ({parents[0]['child_count']} child tickets); "
                         f"{bl['suggested_link_count']} unlinked look-alikes to review.", [pnum]))
    if persona == "engineer" and bl["other_candidates"]:
        o = bl["other_candidates"][0]
        bullets.append(b(f"Also over-represented: {o['label']} {o['value']} ({o['support']:.0%} vs {o['base']:.0%}) — "
                         f"consistent with the same provider.", [anchor]))
    if bl.get("out_of_region_store_count"):
        bullets.append(b(f"{bl['out_of_region_store_count']} affected stores are outside your region (counted, not listed).",
                         [anchor]))
    actions = []
    if persona == "manager":
        actions.append(dict(text=f"Notify district managers in {mk}: cash-only signage at the {off_} offline stores.",
                            owner="Regional Ops", citations=[pnum or anchor]))
    else:
        actions.append(dict(text=f"Keep child tickets linked to {pnum}; link the look-alikes if they match." if pnum else
                            "Declare a major incident and link the child tickets.", owner="Major Incident Management",
                            citations=[pnum or anchor]))
    if impact and impact["usd_so_far"] is not None:
        bullets.append(b(f"{money(impact['usd_so_far'])} est. lost sales so far across affected stores "
                         f"({money(impact['per_hour_now'])}/hour now).", [pnum]))
    return dict(headline=headline, status="critical", bullets=bullets, impact=impact, recommended_actions=actions,
                panels=["blast"])


def _r_fix_history(ctx, res, persona, scope, q):
    _err(res)
    s = res["find_similar_incidents"]
    det = s.get("recommendation_detail", {})
    dev = s["tiers"]["device"]
    fix = s["fix_table"]
    kb = (res.get("search_kb") or {}).get("hits", [])
    ch = (res.get("get_chronic") or {}).get("items", [])
    ci = s.get("cmdb_ci")
    unit_cost = next((c for c in ch if c.get("ci") == ci), None)
    qn = s["query"] if ID_RX.match(str(s["query"])) else None
    bullets, actions = [], []
    name = ci if persona == "engineer" else _friendly_ci(ci)
    nfail = det.get("device_failures_365d", 0)
    if dev:
        dft = det.get("device_fix_table", [])
        rec = sum(r["recurred"] for r in dft)
        fixes = ", ".join(f"{r['fix_label'].lower()} ×{r['n']}" for r in dft)
        headline = f"Failure #{nfail} on {name} in 12 months — past fixes aren't holding" if nfail >= 3 else \
            f"{len(dev)} prior incidents on {name}"
        if persona == "exec":
            bullets.append(b(f"{name.capitalize()} has failed {nfail} times in 12 months; {rec} of {len(dev)} repairs "
                             f"were followed by another failure within 30 days.", [d["number"] for d in dev[:4]] + [qn]))
            if unit_cost and unit_cost.get("cost_365d_usd") is not None:
                bullets.append(b(f"Repeat failures on it cost an estimated {money(unit_cost['cost_365d_usd'])} in the last "
                                 f"12 months (lost sales, handling and truck rolls).", unit_cost["citations"][-2:]))
            if det.get("problem"):
                bullets.append(b(f"Recommendation: replace the card reader rather than reset it again; a vendor fix is "
                                 f"tracked under known error {det['problem']}.", [det["problem"]]))
            return dict(headline=headline, status="watch", bullets=bullets, panels=["similar"])
        bullets.append(b(f"{len(dev)} prior incidents on {name}: {', '.join(d['number'] for d in dev[:8])}.",
                         [d["number"] for d in dev]))
        bullets.append(b(f"Fixes applied on this device: {fixes}; {rec} of {len(dev)} were followed by another failure "
                         f"within 30 days.", [d["number"] for d in dev]))
    else:
        headline = "Similar incidents across the fleet"
    if fix:
        lo, hi = min(r["recurrence_30d"] for r in fix), max(r["recurrence_30d"] for r in fix)
        best = fix[0]
        n_all = s.get("fix_table_n", "")
        bullets.append(b(f"Fleet-wide ({n_all} resolved {s['issue_type']} tickets): lowest 30-day recurrence is "
                         f"{best['fix_label'].lower()} ({best['recurrence_30d']:.1%}); all fix types fall between "
                         f"{lo:.1%} and {hi:.1%}.", best["examples"][:2]))
    if det.get("text"):
        cites = (det.get("citations") or [])[-3:]
        rec_txt = det["text"].split("Recommendation: ", 1)
        if len(rec_txt) == 2:
            bullets.append(b("Recommendation: " + rec_txt[1], cites))
        elif not dev and s["tiers"]["fleet"]:
            f0 = s["tiers"]["fleet"][0]
            bullets.append(b(f"Closest match {f0['number']}: {f0['close_notes']}", [f0["number"]]))
    if persona == "engineer" and kb:
        k = kb[0]
        bullets.append(b(f"Steps from {k['number']} ({k['short_description']}): " + re.sub(r"\s*\n\s*", " ", k["snippet"]),
                         [k["number"]]))
    if det.get("problem"):
        actions.append(dict(text=f"Replace the secure card reader and attach this incident to {det['problem']}."
                            if "Dispenser" in str(det.get("text")) or "DISP" in str(ci) else
                            f"Attach to {det['problem']} and apply the permanent fix.",
                            owner="Fuel Systems Support", citations=[det["problem"]] + ([qn] if qn else [])))
    return dict(headline=headline, status="watch", bullets=bullets, recommended_actions=actions, panels=["similar"])


def _r_changes(ctx, res, persona, scope, q):
    _err(res)
    c = res["correlate_changes"]
    a = c["anchor"]
    sid = a.get("store_id")
    off = -6
    if sid:
        st = get_data().stores.iloc[get_data().store_idx[int(sid)]]
        off = int(st["utc_offset_std"])
    cands = [x for x in c["candidates"] if x["score"] > 0.4]
    bullets = []
    anchor_cite = [a["number"]] if a.get("number") else []
    if cands:
        top = cands[0]
        ch = top["change"]
        headline = f"Likely related change: {ch['number']} ({ch['short_description']})"
        bullets.append(b(f"{ch['number']} started {t_local(ch['actual_start'], off, True)}, {top['hours_before']:.0f}h before "
                         f"the incident; score {top['score']:.2f}.", [ch["number"]] + anchor_cite))
        if top["evidence_text"]:
            bullets.append(b(top["evidence_text"], [ch["number"]]))
        if top["incidents_linked"]:
            bullets.append(b(f"{top['incidents_linked']} incidents are already marked caused-by {ch['number']}.", [ch["number"]]))
        status = "watch"
    else:
        headline = "No recent change explains this" + (f" — store {sid}" if sid else "")
        for t in c["negative_evidence"]:
            bullets.append(b(t + (" Change is unlikely to be the cause." if "No changes touched" in t else ""), anchor_cite or None,
                             ["correlate_changes"]))
        status = "info"
    for u in sorted(c["upcoming"], key=lambda u: bool(u.get("enterprise")))[:3]:
        if not u.get("enterprise") or len(c["upcoming"]) <= 3:
            bullets.append(b(f"Scheduled: {u['number']} {u['short_description']} — {t_local(u['planned_start'], off, True)}"
                             + (" (store visit planned)" if not u.get("enterprise") else "") + ".", [u["number"]]))
    if persona == "exec":
        bullets = bullets[:1] + [x for x in bullets[1:] if "Scheduled" in x["text"]][:2] if not cands else bullets[:3]
    return dict(headline=headline, status=status, bullets=bullets, panels=["changes"], confidence="high" if cands or
                c["negative_evidence"] else "medium")


def _r_impact(ctx, res, persona, scope, q):
    _err(res)
    if "run_analytics_query" in res:
        return _r_nlq(ctx, res, persona, scope, q)
    m = res["estimate_impact"]
    usd = m.get("usd_so_far", m.get("usd"))
    if usd is None:
        bl = []
        if m.get("major") and m.get("number"):
            bl.append(b(f"{m['stores']} stores are affected under {m['number']}; dollar figures are restricted for your role.",
                        [m["number"]]))
        elif m.get("number"):
            bl.append(b(f"{m['number']}: dollar figures are restricted for your role.", [m["number"]]))
        else:
            bl.append(b(f"Store {m.get('store_id')}: {m.get('incidents')} incidents in the last {m.get('days')} days; dollar "
                        f"figures are restricted for your role.", None, ["estimate_impact"]))
        return dict(headline="Financial impact is restricted for your role", status="info", bullets=bl,
                    impact=dict(usd_so_far=None, per_hour_now=None, as_of=m.get("as_of"), restricted=True),
                    panels=["impact"])
    impact = dict(usd_so_far=usd, per_hour_now=m.get("per_hour_now"), as_of=m.get("as_of"), restricted=False,
                  scope="major" if m.get("major") else "store")
    bullets = []
    bs = m.get("by_stream") or {}
    tot = sum(v for v in bs.values() if v) or 0
    lab = {"inside": "inside sales", "fuel": "fuel", "sevennow": "7NOW delivery", "lottery": "lottery", "ev": "EV charging"}
    mix = ", ".join(f"{lab[k]} {money(v)}" for k, v in sorted(bs.items(), key=lambda kv: -(kv[1] or 0)) if v and v > 0)
    if m.get("major"):
        headline = f"{money(usd)} est. lost sales so far — and about {money(m['per_hour_now'])} more every hour"
        bullets.append(b(f"{money(usd)} across {m['stores']} affected stores (counted once per store), "
                         f"{money(m['per_hour_now'])}/hour at the current hour's sales baseline.", [m["number"]]))
        if mix:
            bullets.append(b(f"By stream: {mix}.", [m["number"]]))
        if m.get("top_stores"):
            ts = m["top_stores"][:3]
            bullets.append(b("Hardest hit: " + ", ".join(f"store {t['store_id']} {money(t['usd'])}" for t in ts) + ".",
                             [t["incident_number"] for t in ts]))
    elif "number" in m and m.get("number"):
        headline = f"{money(usd)} est. lost sales on {m['number']}"
        bullets.append(b(f"{money(usd)} over {hours(m['hours'])}" + (f", {money(m['per_hour_now'])}/hour now." if m.get('per_hour_now') else "."),
                         [m["number"]]))
    else:
        headline = f"Store {m['store_id']}: {money(usd)} est. lost sales over the last {m['days']} days"
        bullets.append(b(f"{money(usd)} across {m['incidents']} incidents; {money(m['per_hour_now'])}/hour right now from "
                         f"open incidents.", [t["number"] for t in m.get("top", [])[:3]] or None, ["estimate_impact"]))
        for t in m.get("top", [])[:3]:
            bullets.append(b(f"{t['number']} ({t['issue_type']}): {money(t['usd'])}.", [t["number"]]))
    sections = [dict(title="How we estimate", bullets=[b(m.get("footnote", ""), [m.get("number")] if m.get("number") else None,
                                                         ["estimate_impact"])])]
    return dict(headline=headline, status="critical" if m.get("per_hour_now") else "info", bullets=bullets[:3 if persona == "exec" else 5],
                impact=impact, sections=sections if persona != "exec" else [], panels=["impact"])


def _r_handoff(ctx, res, persona, scope, q):
    _err(res)
    h = res["get_shift_handoff"]
    s = h["sections"]
    reg = h["region"]
    bullets = []
    for m in s["majors"][:2]:
        bullets.append(b(f"MAJOR {m['number']}: {m['short_description']} — {m['stores']} stores, "
                         + (f"{money(m['usd_so_far'])} so far." if m.get("usd_so_far") is not None else "impact restricted."),
                         [m["number"]]))
    bullets.append(b(f"{s['p1_p2_open_count']} P1/P2 open, {s['sla_risk_count']} at SLA risk, {s['on_hold_24h_count']} on hold "
                     f"> 24h, {s['open_store_incidents']} open store incidents in total.",
                     [x["number"] for x in s["p1_p2_open"][:3]] or None, ["get_shift_handoff"]))
    if s["changes_next_24h"]:
        c = s["changes_next_24h"][0]
        bullets.append(b(f"Next 24h: {len(s['changes_next_24h'])} scheduled change(s), first {c['number']} "
                         f"{c['short_description']} at {t_local(c['planned_start'])}.", [c["number"]]))
    if s["new_chronic"]:
        bullets.append(b(f"{len(s['new_chronic'])} chronic devices failed again this week (top: store "
                         f"{s['new_chronic'][0]['store_id']}, {s['new_chronic'][0]['unit']}).",
                         s["new_chronic"][0]["citations"][-1:]))
    sla = [dict(title="SLA at risk", bullets=[b(f"{x['number']} store {x['store_id']}: {x['short_description']} — "
                                                 + ("SLA breached" if x["sla"]["status"] == "breached" else
                                                    f"SLA due {t_local(x['sla']['due'])}") + ".", [x["number"]])
                                               for x in s["sla_risk"][:5]])]
    return dict(headline=f"Shift handoff — {reg if reg != 'All' else 'all regions'}", status="watch" if s["majors"] else "info",
                bullets=bullets, sections=sla, panels=["handoff"])


def _r_pir(ctx, res, persona, scope, q):
    _err(res)
    f = res.get("get_pir_facts")
    if not f:
        raise _Refused("Which incident? Open a major incident first.")
    im = f["impact"]
    bullets = [b(f"{f['doc_title']} for {f['number']}: {f['title']}.", [f["number"]]),
               b(f"{im['stores']} stores ({im['offline']} offline, {im['degraded']} degraded), {hours(im['duration_h'])} so far"
                 + (f", {money(im['usd'])} est. lost sales." if im.get("usd") is not None else "."), [f["number"]])]
    rc = f["root_cause"]
    if rc.get("problem"):
        bullets.append(b(f"Root cause tracked in {rc['problem']} ({rc['problem_state']}): {rc['problem_title']}.", [rc["problem"]]))
    return dict(headline=f"{f['doc_title']} drafted", status="info", bullets=bullets, panels=["pir"])


def _r_chronic(ctx, res, persona, scope, q):
    _err(res)
    c = res["get_chronic"]
    if not c["items"]:
        return dict(headline="No chronic devices flagged", status="healthy",
                    bullets=[b(c["summary"], None, ["get_chronic"])], panels=["health"])
    bullets = [b(c["summary"], [c["items"][0]["citations"][-1]], ["get_chronic"])]
    for it in c["items"][:4]:
        nm = it["unit"] if persona == "engineer" else _friendly_ci(it["unit"])
        cost = f", {money(it['cost_365d_usd'])} in 12 months" if it.get("cost_365d_usd") is not None else ""
        bullets.append(b(f"Store {it['store_id']} ({it['city']}): {nm} — {it['count_365d']} failures{cost}. {it['recommendation']}",
                         it["citations"][-2:]))
    return dict(headline=f"{c['count']} chronic devices flagged", status="watch", bullets=bullets, panels=["chronic"])


def _r_rbac_network(ctx, res, persona, scope, q):
    _err(res)
    p = res.get("get_store_profile")
    if not p:
        raise _Refused("Which store? Type a store number.")
    n = p["network"]
    if _restricted(n.get("router_lan_ip")):
        return dict(headline="Network details (IPs, circuit IDs) are restricted for your role", status="info", bullets=[
            b(f"Store {p['store_id']} is on {n.get('primary_isp')} with {n.get('backup_carrier')} LTE backup.", None,
              ["get_store_profile"])], panels=["health"], confidence="high")
    return dict(headline=f"Store {p['store_id']} network details", status="info", bullets=[
        b(f"Primary {n['primary_isp']} circuit {n['primary_circuit_id']} ({n['circuit_type']}, {n['bandwidth_mbps']} Mbps); "
          f"LTE {n['backup_carrier']}.", None, ["get_store_profile"]),
        b(f"Subnet {n['network_subnet']}; router {n['router_lan_ip']}; store server {n['store_server_ip']}; "
          f"POS {n['pos_ip_range']}; PIN pads {n['pinpad_ip_range']}.", None, ["get_store_profile"]),
        b(f"Router {n['sdwan_router_model']} on {n['router_firmware']}.", None, ["get_store_profile"])], panels=["health"])


def _r_nlq(ctx, res, persona, scope, q):
    _err(res)
    r = res.get("run_analytics_query", {})
    if not r.get("ok"):
        return dict(headline=r.get("error", "I couldn't answer that"), status="info", bullets=[], panels=["nlq"],
                    confidence="low")
    cols, rows = r["columns"], r["rows"]
    bullets = [b(f"{len(rows)} rows from the analytics views" + (f" (closest analysis: “{r['matched_example']}”)"
                                                                 if r.get("matched_example") else "") + ".", None,
                 ["run_analytics_query"])]
    for row in rows[:5]:
        txt = ", ".join(f"{c}: {_fmt_cell(v)}" for c, v in zip(cols, row))
        ids = [str(v) for v in row if isinstance(v, str) and re.fullmatch(r"(INC|CHG|PRB|KB)\d{7}", str(v))]
        bullets.append(b(txt, ids or None, ["run_analytics_query"]))
    return dict(headline=q.strip().rstrip("?") + " — results", status="info", bullets=bullets, panels=["nlq"],
                confidence="medium")


def _fmt_cell(v):
    if isinstance(v, float):
        return f"{v:,.1f}" if abs(v) < 1e4 else f"{v:,.0f}"
    if isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}T", v):
        return v[:10]
    return v
