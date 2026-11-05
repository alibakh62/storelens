"""Change correlation with fleet evidence (SPEC §7.5).

Staggered rollouts are a natural experiment: stores in the change (treated) vs stores not yet
changed in the same calendar window (control) → risk ratio with a 95% CI (log method) and a
diff-in-diff of rates. Enterprise changes (no store map) use a fleet-wide z-score vs the same
weekday/hour over the trailing 8 weeks.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..config import app_config
from ..db import Data, clean, inc_row
from .groups import CHANGE_DOMAIN, GROUP_LABEL, domain_match, group_of

H = pd.Timedelta(hours=1)
D = pd.Timedelta(days=1)


def _ensure_group(data: Data):
    if "grp" not in data.incident:
        data.incident["grp"] = [group_of(i, c) for i, c in zip(data.incident["issue_type"], data.incident["category"])]


def _change_stores(data: Data) -> dict:
    if not hasattr(data, "_chg_stores"):
        data._chg_stores = data.change_map.groupby("change_number")["store_id"].apply(set).to_dict()
    return data._chg_stores


def change_summary(data: Data, c) -> dict:
    keys = ["number", "short_description", "type", "category", "scope", "ci_class", "version_from", "version_to",
            "risk", "state", "assignment_group", "planned_start", "planned_end", "actual_start", "actual_end",
            "close_code", "close_notes", "stores_affected"]
    d = {k: clean(c[k]) for k in keys}
    d["enterprise"] = c["number"] in data.enterprise_changes
    return d


def _applies_to_store(data: Data, c, store) -> bool:
    if c["number"] in data.enterprise_changes:
        if c["ci_class"] in ("7NOW Platform",):
            return bool(store["has_7now"])
        return True
    return int(store["store_id"]) in _change_stores(data).get(c["number"], set())


def store_changes(data: Data, store_id: int, now: pd.Timestamp, lookback_h: int | None = None,
                  lookahead_d: int | None = None) -> dict:
    cfg = app_config().get("changes", {})
    lookback_h = lookback_h or cfg.get("lookback_h", 72)
    lookahead_d = lookahead_d or cfg.get("lookahead_d", 7)
    store = data.stores.iloc[data.store_idx[int(store_id)]]
    c = data.change
    stores_map = _change_stores(data)
    mine = {n for n, s in stores_map.items() if int(store_id) in s}
    rel = c[c["number"].isin(mine) | c["number"].isin(data.enterprise_changes)]
    rel = rel[[_applies_to_store(data, r, store) for _, r in rel.iterrows()]]
    recent = rel[(rel["actual_start"] >= now - lookback_h * H) & (rel["actual_start"] <= now)]
    up = rel[(rel["planned_start"] >= now) & (rel["planned_start"] <= now + lookahead_d * D)
             & rel["state"].isin(["Scheduled", "Review", "Implement", "New", "Assess", "Authorize"])]
    return dict(recent=[change_summary(data, r) for _, r in recent.sort_values("actual_start").iterrows()],
                upcoming=[change_summary(data, r) for _, r in up.sort_values("planned_start").iterrows()])


# ---------------------------------------------------------------- fleet evidence
def _rr_ci(a, n1, b, n0):
    a_, b_ = (a or 0.5), (b or 0.5)
    r1, r0 = a_ / n1, b_ / n0
    rr = r1 / r0
    se = math.sqrt(1 / a_ + 1 / b_)
    return rr, (rr * math.exp(-1.96 * se), rr * math.exp(1.96 * se))


def staggered_evidence(data: Data, c, group: str) -> dict | None:
    _ensure_group(data)
    cfg = app_config().get("changes", {})
    post_d = cfg.get("post_window_d", 5)
    start = c["actual_start"]
    if pd.isna(start):
        return None
    treated = _change_stores(data).get(c["number"], set())
    if len(treated) < 5:
        return None
    # control: stores not touched by a same-class change during the same calendar window
    same = data.change[(data.change["ci_class"] == c["ci_class"]) & data.change["actual_start"].between(
        start - post_d * D, start + post_d * D)]
    touched = set().union(*[_change_stores(data).get(n, set()) for n in same["number"]]) | treated
    control = set(data.stores["store_id"]) - touched
    if len(control) < 5:
        return None
    inc = data.incident
    g = inc[(inc["grp"] == group) & inc["store_id"].notna()]
    post = g[(g["opened_at"] >= start) & (g["opened_at"] < start + post_d * D)]
    pre = g[(g["opened_at"] >= start - post_d * D) & (g["opened_at"] < start)]
    sid_post, sid_pre = post["store_id"].astype(int), pre["store_id"].astype(int)
    a, b = int(sid_post.isin(treated).sum()), int(sid_post.isin(control).sum())
    a0, b0 = int(sid_pre.isin(treated).sum()), int(sid_pre.isin(control).sum())
    n1, n0 = len(treated), len(control)
    rr, ci = _rr_ci(a, n1, b, n0)
    per = 100.0 / post_d  # incidents per 100 stores per day
    did = ((a / n1 - a0 / n1) - (b / n0 - b0 / n0)) * per
    fe = min(1.0, math.log2(rr) / 4) if ci[0] > 1 else 0.0
    return dict(kind="staggered", rr=round(rr, 2), rr_ci=[round(ci[0], 2), round(ci[1], 2)], did=round(did, 3),
                treated=n1, control=n0, treated_incidents=a, control_incidents=b, treated_pre=a0, control_pre=b0,
                treated_rate=round(a / n1 * per, 3), control_rate=round(b / n0 * per, 3), fleet_evidence=max(0.0, fe),
                window_days=post_d)


def enterprise_evidence(data: Data, c, group: str) -> dict | None:
    _ensure_group(data)
    start = c["actual_start"]
    if pd.isna(start):
        return None
    g = data.incident[data.incident["grp"] == group]["opened_at"]
    obs = int(((g >= start) & (g < start + 6 * H)).sum())
    hist = [int(((g >= start - k * 7 * D) & (g < start - k * 7 * D + 6 * H)).sum()) for k in range(1, 9)]
    mu, sd = float(np.mean(hist)), float(np.std(hist, ddof=1)) if len(hist) > 1 else 0.0
    z = (obs - mu) / max(sd, math.sqrt(max(mu, 1.0)), 1.0)
    fe = min(1.0, z / 6) if z > 3 else 0.0
    return dict(kind="enterprise", z=round(z, 2), observed=obs, expected=round(mu, 2), window_h=6,
                fleet_evidence=max(0.0, fe))


def _evidence_text(c, ev, group) -> str:
    gl = GROUP_LABEL.get(group, group)
    if ev is None:
        return "Not enough rollout data for a fleet comparison."
    if ev["kind"] == "staggered":
        rr, (lo, hi) = ev["rr"], ev["rr_ci"]
        if lo > 1:
            return (f"Stores in {c['scope'].replace('Release ring: ', '').replace('Region: ', '')} opened {gl} tickets at "
                    f"{rr:.1f}× the rate of not-yet-changed stores in the {ev['window_days']} days after deployment "
                    f"(95% CI {lo:.1f}–{hi:.1f}×).")
        return (f"No elevated {gl} ticket rate in changed vs not-yet-changed stores in the {ev['window_days']} days after "
                f"deployment (risk ratio {rr:.1f}×, 95% CI {lo:.1f}–{hi:.1f}×).")
    if ev["z"] > 3:
        return (f"Fleet-wide {gl} tickets in the 6h after the change: {ev['observed']} vs {ev['expected']:.1f} expected "
                f"for that weekday/hour (z = {ev['z']:.1f}).")
    return (f"No fleet-wide spike in {gl} tickets in the 6h after the change ({ev['observed']} vs {ev['expected']:.1f} "
            f"expected, z = {ev['z']:.1f}).")


def score_change(data: Data, c, group: str | None, t: pd.Timestamp) -> dict:
    dh = max(0.0, (t - c["actual_start"]) / H) if pd.notna(c["actual_start"]) else 72.0
    prox = math.exp(-dh / 24)
    dm = domain_match(c["ci_class"], group)
    g = group or (CHANGE_DOMAIN.get(c["ci_class"], [None])[0])
    ev = None
    if g:
        ev = enterprise_evidence(data, c, g) if c["number"] in data.enterprise_changes else staggered_evidence(data, c, g)
    fe = ev["fleet_evidence"] if ev else 0.0
    score = 0.4 * prox + 0.3 * dm + 0.3 * fe
    linked = data.incident[data.incident["caused_by_change"] == c["number"]]
    return dict(change=change_summary(data, c), score=round(score, 3), time_proximity=round(prox, 3),
                hours_before=round(dh, 1), domain_match=dm, fleet_evidence=round(fe, 3),
                evidence_text=_evidence_text(c, ev, g) if g else "", evidence=ev,
                rr=ev.get("rr") if ev else None, rr_ci=ev.get("rr_ci") if ev else None,
                did=ev.get("did") if ev else None, z=ev.get("z") if ev else None,
                affected_stores_in_change=int(c["stores_affected"]) if pd.notna(c["stores_affected"]) else None,
                incidents_linked=int(len(linked)))


def correlate_changes(data: Data, number: str | None = None, store_id: int | None = None,
                      lookback_h: int | None = None, lookahead_d: int | None = None) -> dict:
    cfg = app_config().get("changes", {})
    lookback_h = lookback_h or cfg.get("lookback_h", 72)
    lookahead_d = lookahead_d or cfg.get("lookahead_d", 7)
    now = data.now()
    group, inc = None, None
    if number:
        inc = inc_row(data, number)
        if inc is None:
            raise KeyError(number)
        t = inc["opened_at"]
        group = group_of(inc["issue_type"], inc["category"])
        if pd.isna(inc["store_id"]):
            kids = data.incident[data.incident["parent_incident"] == number]
            if len(kids):
                group = group_of(kids["issue_type"].mode().iat[0], kids["category"].mode().iat[0])
            store_id = None
        else:
            store_id = int(inc["store_id"])
    else:
        t = now
    c = data.change
    win = c[(c["actual_start"] >= t - lookback_h * H) & (c["actual_start"] <= t)]
    cands = []
    if store_id is not None:
        mine = {n for n, s in _change_stores(data).items() if int(store_id) in s}
        for _, r in win[win["number"].isin(mine)].iterrows():
            cands.append(("store", r))
        # the store is still running a version introduced by an older store-mapped change (e.g. a release ring
        # deployed 4 days ago): include the latest version-changing change of the last 14 days as well
        older = c[c["number"].isin(mine) & c["version_to"].notna() & (c["actual_start"] < t - lookback_h * H)
                  & (c["actual_start"] >= t - 14 * D)]
        seen = set(win["number"])
        for cls, grp_ in older.groupby("ci_class"):
            r = grp_.sort_values("actual_start").iloc[-1]
            if r["number"] not in seen and domain_match(r["ci_class"], group) > 0:
                cands.append(("store", r))
    else:
        for _, r in win[~win["number"].isin(data.enterprise_changes)].iterrows():
            cands.append(("store", r))
    for _, r in win[win["number"].isin(data.enterprise_changes)].iterrows():
        if group is None or domain_match(r["ci_class"], group) > 0:
            cands.append(("enterprise", r))
    ranked = []
    for kind, r in cands:
        sc = score_change(data, r, group, t)
        sc["candidate_type"] = kind
        ranked.append(sc)
    ranked.sort(key=lambda x: -x["score"])
    ranked = ranked[:10]

    upcoming = []
    if store_id is not None:
        upcoming = store_changes(data, store_id, now, lookback_h, lookahead_d)["upcoming"]
    else:
        up = c[(c["planned_start"] >= now) & (c["planned_start"] <= now + lookahead_d * D) & (c["state"] == "Scheduled")]
        upcoming = [change_summary(data, r) for _, r in up.sort_values("planned_start").head(15).iterrows()]

    negative = []
    store_cands = [x for x in ranked if x["candidate_type"] == "store"]
    where = f"store {store_id}" if store_id is not None else "the affected stores"
    what = "this incident" if number else "now"
    if not store_cands:
        negative.append(f"No changes touched {where} in the {lookback_h}h before {what}.")
    if ranked and all(x["score"] <= 0.4 for x in ranked):
        negative.append("No change in the window shows a fleet signal; change is unlikely to be the cause.")
    elif not ranked:
        negative.append("No enterprise changes in the window match this service.")
    cites = [x["change"]["number"] for x in ranked[:3]] + [u["number"] for u in upcoming[:3]]
    if number:
        cites.insert(0, number)
    return dict(anchor=dict(number=number, store_id=store_id, t=t.isoformat(), group=group), lookback_h=lookback_h,
                candidates=ranked, upcoming=upcoming, negative_evidence=negative, citations=cites)
