"""Business impact (SPEC §7.7): integrate the hourly sales baseline × service loss profile.

Store-local hour resolution using utc_offset_std (no DST), weekend = local Sat/Sun, partial hours,
capped at 72h. Vectorised with a per-store cumulative weekly baseline so all 29k incidents can be
computed at once.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..db import EPOCH, STREAMS, Data, inc_row, is_open

CAP_H = 72.0
FOOTNOTE = "Estimate: hourly sales baseline × service loss profile. Gross sales, not margin."


def _F(cum: np.ndarray, week: np.ndarray, si: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Cumulative baseline (per stream) from EPOCH-local hour 0 to local hour t. Returns [n, 5]."""
    w = np.floor(t / 168.0)
    rem = t - w * 168.0
    k = np.floor(rem).astype(int)
    k = np.clip(k, 0, 167)
    frac = rem - k
    full = cum[si, 168, :]
    return w[:, None] * full + cum[si, k, :] + frac[:, None] * week[si, k, :]


def by_stream_many(data: Data, store_ids, start, end, profiles) -> np.ndarray:
    """Vectorised lost sales per stream for many incidents. Returns [n, 5]."""
    si = np.array([data.store_idx.get(int(s), -1) for s in store_ids])
    ok = si >= 0
    out = np.zeros((len(si), 5))
    if not ok.any():
        return out
    st = pd.to_datetime(pd.Series(start)).to_numpy()
    en = pd.to_datetime(pd.Series(end)).to_numpy()
    s0 = (st - np.datetime64(EPOCH)) / np.timedelta64(1, "h")
    e0 = (en - np.datetime64(EPOCH)) / np.timedelta64(1, "h")
    e0 = np.minimum(e0, s0 + CAP_H)
    e0 = np.maximum(e0, s0)
    off = data.stores["utc_offset_std"].to_numpy()[np.where(ok, si, 0)]
    lv = np.array([data.profile_vec.get(p, np.zeros(5)) for p in profiles])
    sel = np.where(ok)[0]
    a = _F(data.base_cum, data.base_week, si[sel], (s0 + off)[sel])
    b = _F(data.base_cum, data.base_week, si[sel], (e0 + off)[sel])
    out[sel] = (b - a) * lv[sel]
    return out


def rate_now(data: Data, store_id: int, profile: str, at: pd.Timestamp) -> float:
    """Loss per hour at time `at` (current local hour)."""
    si = data.store_idx.get(int(store_id))
    if si is None:
        return 0.0
    t = (at - EPOCH) / pd.Timedelta(hours=1) + int(data.stores.iloc[si]["utc_offset_std"])
    k = int(np.floor(t)) % 168
    return float(data.base_week[si, k, :] @ data.profile_vec.get(profile, np.zeros(5)))


def precompute(data: Data) -> None:
    """Computed loss for every store incident as of mock now (resolved ones don't change)."""
    inc = data.incident
    now = data.clock.mock_now
    m = inc["store_id"].notna()
    end = inc["resolved_at"].where(inc["resolved_at"].notna(), now)
    arr = by_stream_many(data, inc.loc[m, "store_id"].astype(int), inc.loc[m, "opened_at"], end[m],
                         inc.loc[m, "impact_profile"])
    tot = np.zeros(len(inc))
    tot[m.to_numpy()] = arr.sum(axis=1)
    inc["computed_loss_usd"] = np.round(tot, 2)
    for k, s in enumerate(STREAMS):
        col = np.zeros(len(inc))
        col[m.to_numpy()] = arr[:, k]
        inc[f"loss_{s}"] = col


def incident_impact(data: Data, number: str, as_of: pd.Timestamp | None = None) -> dict:
    r = inc_row(data, number)
    if r is None:
        raise KeyError(number)
    as_of = as_of or data.now()
    if bool(r["major_incident"]):
        return major_impact(data, number, as_of)
    if pd.isna(r["store_id"]):
        return dict(number=number, usd=0.0, by_stream={}, hours=0, per_hour_now=0.0, as_of=as_of.isoformat())
    end = r["resolved_at"] if pd.notna(r["resolved_at"]) and r["resolved_at"] <= as_of else as_of
    arr = by_stream_many(data, [r["store_id"]], [r["opened_at"]], [end], [r["impact_profile"]])[0]
    hours = min(max((end - r["opened_at"]) / pd.Timedelta(hours=1), 0), CAP_H)
    open_ = is_open(r["state"]) or (pd.notna(r["resolved_at"]) and r["resolved_at"] > as_of)
    per_hour = rate_now(data, int(r["store_id"]), r["impact_profile"], as_of) if open_ and hours < CAP_H else 0.0
    return dict(number=number, usd=round(float(arr.sum()), 2),
                by_stream={s: round(float(v), 2) for s, v in zip(STREAMS, arr)},
                hours=round(float(hours), 2), per_hour_now=round(per_hour, 2), as_of=as_of.isoformat(),
                impact_profile=r["impact_profile"], store_id=int(r["store_id"]), open=bool(open_),
                footnote=FOOTNOTE, citations=[number])


def major_impact(data: Data, number: str, as_of: pd.Timestamp | None = None) -> dict:
    """Sum of children, deduplicated per store (max per store within one parent)."""
    as_of = as_of or data.now()
    inc = data.incident
    kids = inc[inc["parent_incident"] == number]
    per_store: dict[int, dict] = {}
    for r in kids.itertuples():
        if pd.isna(r.store_id):
            continue
        imp = incident_impact(data, r.number, as_of)
        cur = per_store.get(int(r.store_id))
        if cur is None or imp["usd"] > cur["usd"]:
            per_store[int(r.store_id)] = imp
    usd = sum(v["usd"] for v in per_store.values())
    ph = sum(v["per_hour_now"] for v in per_store.values())
    by_stream = {s: round(sum(v["by_stream"].get(s, 0) for v in per_store.values()), 2) for s in STREAMS}
    top = sorted(per_store.values(), key=lambda v: -v["usd"])[:5]
    return dict(number=number, major=True, usd=round(usd, 2), usd_so_far=round(usd, 2), per_hour_now=round(ph, 2),
                by_stream=by_stream, stores=len(per_store), as_of=as_of.isoformat(),
                top_stores=[dict(store_id=v["store_id"], usd=v["usd"], incident_number=v["number"]) for v in top],
                footnote=FOOTNOTE, citations=[number] + [v["number"] for v in top])


def store_impact(data: Data, store_id: int, days: int = 365, as_of: pd.Timestamp | None = None) -> dict:
    as_of = as_of or data.now()
    inc = data.incident
    m = inc[(inc["store_id"] == int(store_id)) & (inc["opened_at"] >= as_of - pd.Timedelta(days=days))]
    total, rows = 0.0, []
    for r in m.itertuples():
        if is_open(r.state):
            v = incident_impact(data, r.number, as_of)["usd"]
        else:
            v = float(r.computed_loss_usd)
        total += v
        if v > 0:
            rows.append(dict(number=r.number, usd=round(v, 2), issue_type=r.issue_type, opened_at=r.opened_at.isoformat()))
    rows.sort(key=lambda x: -x["usd"])
    open_rate = sum(incident_impact(data, r.number, as_of)["per_hour_now"] for r in m.itertuples() if is_open(r.state))
    return dict(store_id=int(store_id), days=days, usd=round(total, 2), per_hour_now=round(open_rate, 2),
                incidents=len(m), top=rows[:5], as_of=as_of.isoformat(), footnote=FOOTNOTE,
                citations=[x["number"] for x in rows[:5]])


def estimate_impact(data: Data, number: str | None = None, store_id: int | None = None, days: int = 365) -> dict:
    if number:
        return incident_impact(data, number)
    if store_id is not None:
        return store_impact(data, int(store_id), days)
    raise ValueError("number or store_id required")
