"""Chronic stores and devices (SPEC §7.6)."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import poisson

from ..config import app_config, value_model_config
from ..db import Data

RECO = {
    "Fuel Dispenser": "Replace the secure card reader (permanent fix) instead of another remote reset.",
    "POS Terminal": "Hardware swap of the terminal.",
    "Router": "Escalate circuit/ISP: open a carrier trouble ticket for chronic line quality.",
    "Walk-in Cooler Controller": "Compressor/fan replacement and open a problem record.",
    "7NOW Tablet": "Replace the 7NOW tablet.",
    "PIN Pad": "Replace the PIN pad.",
}
_CACHE: dict = {}


def _units(data: Data, as_of: pd.Timestamp) -> pd.DataFrame:
    key = ("units", as_of.floor("h"))
    if key in _CACHE:
        return _CACHE[key]
    cfg = app_config().get("chronic", {})
    w90, n90, n365 = cfg.get("window_d", 90), cfg.get("min_repeats_90d", 3), cfg.get("min_repeats_365d", 5)
    inc = data.incident
    inc = inc[inc["store_id"].notna() & ~inc["major_incident"] & (inc["opened_at"] <= as_of)
              & (inc["opened_at"] > as_of - pd.Timedelta(days=365))].copy()
    # repeats from a single parent outage are not "chronic"
    inc = inc[inc["parent_incident"].isna()]
    inc["unit"] = np.where(inc["cmdb_ci"].notna(), inc["cmdb_ci"].astype(str),
                           inc["store_id"].astype(str) + ":" + inc["issue_type"])
    inc["is90"] = inc["opened_at"] > as_of - pd.Timedelta(days=w90)
    full = inc
    cnt = inc.groupby(["store_id", "unit"]).agg(n365=("number", "size"), n90=("is90", "sum")).reset_index()
    hot = cnt[(cnt["n90"] >= n90) | (cnt["n365"] >= n365)]
    inc = inc.merge(hot[["store_id", "unit"]], on=["store_id", "unit"])
    g = inc.groupby(["store_id", "unit"]).agg(
        n365=("number", "size"), n90=("is90", "sum"), ci_class=("ci_class", "first"),
        issue_type=("issue_type", lambda s: s.mode().iat[0]), last=("opened_at", "max"),
        loss=("computed_loss_usd", "sum"), dispatched=("field_dispatch", "sum"),
        numbers=("number", list), problems=("problem_id", lambda s: [p for p in s.dropna().unique()]),
        cmdb_ci=("cmdb_ci", "first")).reset_index()
    cand = g[(g["n90"] >= n90) | (g["n365"] >= n365)].copy()
    # fleet rate per CI-day for the ci_class (fallback: per store-day for the issue_type)
    ci_counts = data.cmdb["ci_class"].value_counts()
    cls_inc = full.groupby("ci_class").size()
    it_inc = full.groupby("issue_type").size()
    n_stores = len(data.stores)
    pvals = []
    for r in cand.itertuples():
        if isinstance(r.ci_class, str) and r.ci_class in ci_counts:
            rate = cls_inc.get(r.ci_class, 0) / (ci_counts[r.ci_class] * 365.0)
        else:
            rate = it_inc.get(r.issue_type, 0) / (n_stores * 365.0)
        p365 = poisson.sf(r.n365 - 1, rate * 365)
        p90 = poisson.sf(r.n90 - 1, rate * w90) if r.n90 > 0 else 1.0
        pvals.append(float(min(p365, p90)))
    cand["p_value"] = pvals
    out = cand[cand["p_value"] < 0.01].copy()
    vm = value_model_config()
    out["cost_usd"] = (out["loss"] + out["n365"] * vm.get("handling_cost_per_incident", 35)
                       + out["dispatched"] * vm.get("truck_roll_cost", 250)).round(2)
    out = out.sort_values("cost_usd", ascending=False).reset_index(drop=True)
    _CACHE.clear()
    _CACHE[key] = out
    return out


def _fmt(r, data: Data) -> dict:
    s = data.stores.iloc[data.store_idx[int(r.store_id)]]
    nums = sorted(r.numbers)
    open_prb = [p for p in r.problems if p in set(data.problem.loc[data.problem["state"] != "Closed", "number"])]
    prbs = open_prb or list(r.problems)
    reco = RECO.get(r.ci_class, "Investigate root cause and open a problem record.")
    label = r.cmdb_ci if isinstance(r.cmdb_ci, str) else f"{r.issue_type} (store-level)"
    return dict(store_id=int(r.store_id), city=s["city"], state=s["state"], region=s["region"], unit=label,
                ci=r.cmdb_ci if isinstance(r.cmdb_ci, str) else None, ci_class=r.ci_class if isinstance(r.ci_class, str) else None,
                issue_type=r.issue_type, count_90d=int(r.n90), count_365d=int(r.n365), p_value=float(r.p_value),
                last_incident=r.last.isoformat(), cost_365d_usd=float(r.cost_usd), lost_sales_365d_usd=round(float(r.loss), 2),
                truck_rolls=int(r.dispatched), recommendation=reco, problem=prbs[0] if prbs else None,
                incidents=nums, citations=nums[-5:] + (prbs[:1] if prbs else []))


def get_chronic(data: Data, store_id: int | None = None, region: str | None = None, limit: int = 100) -> dict:
    # history is a snapshot: chronic units are computed as of the data's mock now (cached once)
    u = _units(data, data.clock.mock_now)
    if store_id is not None:
        u = u[u["store_id"] == int(store_id)]
    if region and region != "All":
        u = u[u["store_id"].map(lambda s: data.stores.iloc[data.store_idx[int(s)]]["region"]) == region]
    items = [_fmt(r, data) for r in u.head(limit).itertuples()]
    total = float(u["cost_usd"].sum())
    text = f"{len(u)} chronic devices cost an estimated ${total:,.0f} in the last 12 months."
    return dict(count=int(len(u)), total_cost_usd=round(total, 2), summary=text, items=items,
                store_count=int(u["store_id"].nunique()),
                citations=[c for it in items[:10] for c in it["citations"][:2]])


def chronic_flags_for_store(data: Data, store_id: int) -> list[dict]:
    return get_chronic(data, store_id=store_id)["items"]
