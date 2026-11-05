"""Business-impact value model (SPEC §12). Every input is labeled measured / mock-derived / assumption."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import poisson

from ..config import validation_config, value_model_config
from ..db import Data
from .changes import _ensure_group
from .similar import build_index

_CACHE: dict = {}


def mock_metrics(data: Data) -> dict:
    if "m" in _CACHE:
        return _CACHE["m"]
    build_index(data)
    _ensure_group(data)
    inc = data.incident
    now = data.clock.mock_now
    yr = inc[inc["store_id"].notna() & (inc["opened_at"] > now - pd.Timedelta(days=365))].copy()
    n_st = len(data.stores)
    # dedupe per store within one parent (same as the generator / major impact)
    dup = yr["parent_incident"].notna() & yr.duplicated(["parent_incident", "store_id"], keep=False)
    keep = yr[dup].groupby(["parent_incident", "store_id"])["computed_loss_usd"].idxmax()
    loss = yr["computed_loss_usd"].where(~dup | yr.index.isin(keep.values), 0.0)
    total_loss = float(loss.sum())
    # assisted: had a prior resolved incident on the same device / same store+issue, or part of a cluster (parent)
    yr = yr.sort_values("opened_at")
    prior_dev = yr.groupby(yr["cmdb_ci_id"].fillna(yr["store_id"].astype(str) + yr["issue_type"])).cumcount() > 0
    assisted = prior_dev | yr["parent_incident"].notna()
    assisted_share = float(loss.loc[assisted[assisted].index].sum() / total_loss) if total_loss else 0.0
    # repeats: same CI + issue_type had an incident in the 30 days before
    k = yr[yr["cmdb_ci_id"].notna()].sort_values(["cmdb_ci_id", "issue_type", "opened_at"])
    prev = k.groupby(["cmdb_ci_id", "issue_type"])["opened_at"].shift(1)
    yr["repeat"] = False
    yr.loc[k.index, "repeat"] = (prev.notna() & ((k["opened_at"] - prev) <= pd.Timedelta(days=30))).to_numpy()
    reps = yr[yr["repeat"]]
    # minutes to detect: first child -> blast-radius flag (Poisson p<0.01) vs -> major declared
    det = []
    majors = inc[inc["major_incident"]]
    g_all = inc[~inc["major_incident"]]
    for m in majors.itertuples():
        kids = inc[inc["parent_incident"] == m.number].sort_values("opened_at")
        if len(kids) < 3:
            continue
        grp = kids["grp"].mode().iat[0]
        t0 = kids["opened_at"].iloc[0]
        gg = g_all[g_all["grp"] == grp]["opened_at"]
        hist = [int(((gg >= t0 - pd.Timedelta(days=7 * k) - pd.Timedelta(hours=3)) &
                     (gg <= t0 - pd.Timedelta(days=7 * k) + pd.Timedelta(hours=1))).sum()) for k in range(1, 9)]
        lam = max(float(np.mean(hist)), 0.5)
        kmin = next(k for k in range(1, 1000) if poisson.sf(k - 1, lam) < 0.01)
        if kmin > len(kids):
            continue
        flag = kids["opened_at"].iloc[kmin - 1]
        det.append(dict(number=m.number, to_flag_min=round((flag - t0) / pd.Timedelta(minutes=1), 1),
                        to_declared_min=round((m.opened_at - t0) / pd.Timedelta(minutes=1), 1)))
    out = dict(
        stores=n_st, incidents_365d=int(len(yr)), incidents_per_store_year=round(len(yr) / n_st, 2),
        lost_sales_365d_usd=round(total_loss, 2), lost_sales_per_store_per_year=round(total_loss / n_st, 2),
        assisted_share=round(assisted_share, 3),
        repeat_share=round(float(yr["repeat"].mean()), 4), repeat_incidents_per_store_per_year=round(len(reps) / n_st, 3),
        repeat_dispatch_rate=round(float(reps["field_dispatch"].mean()) if len(reps) else 0.0, 3),
        avg_lost_sales_per_repeat=round(float(reps["computed_loss_usd"].mean()) if len(reps) else 0.0, 2),
        detection=det,
        median_minutes_to_flag=float(np.median([d["to_flag_min"] for d in det])) if det else None,
        median_minutes_to_declare=float(np.median([d["to_declared_min"] for d in det])) if det else None,
        work_notes=int(len(data.notes)), tickets=int(len(inc)))
    _CACHE["m"] = out
    return out


def value_model(data: Data, overrides: dict | None = None) -> dict:
    vm = {**value_model_config(), **(overrides or {})}
    m = mock_metrics(data)
    fleet = vm["fleet_store_count"]
    rows = []

    def rng(key):
        v = vm[key]
        return v if isinstance(v, dict) else dict(low=v, base=v, high=v)

    ms = rng("minutes_saved_per_lookup")
    a = {k: vm["ops_users"] * vm["lookups_per_user_per_day"] * ms[k] / 60 * vm["working_days"] * vm["loaded_cost_per_hour"]
         for k in ms}
    rows.append(dict(lever="A. Time to context", **{k: round(v) for k, v in a.items()},
                     formula="ops_users × lookups/day × minutes_saved / 60 × working_days × loaded $/hr"))
    mr = rng("mttr_reduction_on_assisted")
    b = {k: m["lost_sales_per_store_per_year"] * fleet * m["assisted_share"] * mr[k] * (1 - vm["sales_recapture"])
         for k in mr}
    rows.append(dict(lever="B. Faster restoration → sales protected", **{k: round(v) for k, v in b.items()},
                     formula="lost_sales/store/yr[mock] × fleet × assisted_share[mock] × MTTR reduction × (1 − recapture)"))
    rp = rng("repeat_prevented_share")
    unit = (vm["handling_cost_per_incident"] + m["repeat_dispatch_rate"] * vm["truck_roll_cost"]
            + m["avg_lost_sales_per_repeat"] * (1 - vm["sales_recapture"]))
    c = {k: m["repeat_incidents_per_store_per_year"] * fleet * rp[k] * unit for k in rp}
    rows.append(dict(lever="C. Repeat failures avoided", **{k: round(v) for k, v in c.items()},
                     formula="repeats/store/yr[mock] × fleet × prevented share × (handling + dispatch_rate × truck roll + "
                             "lost sales/repeat × (1 − recapture))"))
    total = {k: round(a[k] + b[k] + c[k]) for k in ("low", "base", "high")}
    users = vm["ops_users"]
    q_month = users * vm.get("llm_queries_per_user_per_day", 4) * 21
    tok_in, tok_out = vm.get("llm_tokens_in_per_answer", 9000), vm.get("llm_tokens_out_per_answer", 700)
    llm_cost = q_month * (tok_in * vm.get("llm_price_in_per_mtok", 3) + tok_out * vm.get("llm_price_out_per_mtok", 15)) / 1e6
    assumptions = [
        dict(key="fleet_store_count", value=fleet, label="assumption", note="replace with internal count"),
        dict(key="ops_users", value=users, label="assumption", note="TSC + NOC + Prod Support + regional managers"),
        dict(key="lookups_per_user_per_day", value=vm["lookups_per_user_per_day"], label="assumption"),
        dict(key="minutes_saved_per_lookup", value=ms, label="measured" if _measured() else "assumption",
             note="time trial (§17)"),
        dict(key="loaded_cost_per_hour", value=vm["loaded_cost_per_hour"], label="assumption"),
        dict(key="mttr_reduction_on_assisted", value=mr, label="assumption", note="pilot KPI"),
        dict(key="sales_recapture", value=vm["sales_recapture"], label="assumption"),
        dict(key="repeat_prevented_share", value=rp, label="assumption"),
        dict(key="handling_cost_per_incident", value=vm["handling_cost_per_incident"], label="assumption"),
        dict(key="truck_roll_cost", value=vm["truck_roll_cost"], label="assumption"),
        dict(key="lost_sales_per_store_per_year", value=m["lost_sales_per_store_per_year"], label="mock-derived"),
        dict(key="assisted_share", value=m["assisted_share"], label="mock-derived"),
        dict(key="repeat_incidents_per_store_per_year", value=m["repeat_incidents_per_store_per_year"], label="mock-derived"),
        dict(key="repeat_share", value=m["repeat_share"], label="mock-derived"),
        dict(key="repeat_dispatch_rate", value=m["repeat_dispatch_rate"], label="mock-derived"),
        dict(key="avg_lost_sales_per_repeat", value=m["avg_lost_sales_per_repeat"], label="mock-derived"),
    ]
    fleet_scale = fleet / m["stores"]
    headline = dict(
        incidents_per_year_fleet=round(m["incidents_per_store_year"] * fleet),
        sales_exposed_per_year_fleet=round(m["lost_sales_per_store_per_year"] * fleet),
        repeat_share=m["repeat_share"], fleet_scale=round(fleet_scale, 2))
    return dict(rows=rows, total=total, assumptions=assumptions, mock=m, headline=headline,
                llm_monthly_cost=round(llm_cost, 2), llm_queries_per_month=q_month,
                pilot_kpis=["Time to context (seconds)", "MTTR, assisted vs not assisted", "Repeat rate on flagged devices",
                            "PIR drafting time", "User CSAT"],
                note="Gross sales, not margin; recapture factor applied. Conservative: present the base case.")


def _measured() -> bool:
    v = validation_config().get("participants") or []
    return any(p.get("before_min") for p in v)
