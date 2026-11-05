"""Blast radius: cross-store clustering, anomaly vs baseline, attribute lift (SPEC §7.4)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.stats import poisson

from ..config import app_config
from ..db import Data, inc_row
from .changes import _ensure_group
from .groups import GROUP_LABEL, group_of
from .impact import incident_impact

H = pd.Timedelta(hours=1)
ATTRS = ["primary_isp", "router_firmware", "pos_software_version", "market", "district_id", "circuit_type",
         "backup_carrier", "banner", "fuel_controller_firmware"]
NETWORK_ATTRS = {"primary_isp", "router_firmware", "circuit_type", "backup_carrier"}
ATTR_LABEL = {"primary_isp": "internet provider", "router_firmware": "router firmware",
              "pos_software_version": "POS version", "market": "market", "district_id": "district",
              "circuit_type": "circuit type", "backup_carrier": "LTE backup carrier", "banner": "banner",
              "fuel_controller_firmware": "fuel controller firmware"}
OFFLINE_TYPES = {"store_offline", "power_outage", "fuel_controller_down"}


def _store_attrs(data: Data) -> pd.DataFrame:
    if not hasattr(data, "_store_attrs"):
        s = data.stores[["store_id", "market", "district_id", "banner", "pos_software_version", "region", "latitude",
                         "longitude", "city", "state"]]
        n = data.network[["store_id", "primary_isp", "router_firmware", "circuit_type", "backup_carrier"]]
        fc = data.cmdb[data.cmdb["ci_class"] == "Fuel Controller"].groupby("store_id")["firmware_version"].first()
        a = s.merge(n, on="store_id", how="left")
        a["fuel_controller_firmware"] = a["store_id"].map(fc)
        data._store_attrs = a.set_index("store_id")
    return data._store_attrs


def _haversine(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def _majors_between(data: Data, a, b) -> bool:
    m = data.incident[data.incident["major_incident"]]
    return bool(((m["opened_at"] >= a - 2 * H) & (m["opened_at"] <= b)).any())


def _anomaly(data: Data, group: str, lo: pd.Timestamp, hi: pd.Timestamp, observed: int) -> dict:
    g = data.incident[(data.incident["grp"] == group) & ~data.incident["major_incident"]]["opened_at"]
    counts = []
    for k in range(1, 9):
        a, b = lo - pd.Timedelta(days=7 * k), hi - pd.Timedelta(days=7 * k)
        if _majors_between(data, a, b):
            continue
        counts.append(int(((g >= a) & (g <= b)).sum()))
    expected = float(np.mean(counts)) if counts else 0.0
    lam = max(expected, 0.5)
    p = float(poisson.sf(observed - 1, lam)) if observed > 0 else 1.0
    return dict(expected=round(expected, 2), observed=observed, p_value=p, unusual=bool(p < 0.01),
                weeks_used=len(counts))


def _attribution(data: Data, affected: list[int], group: str) -> tuple[dict | None, list[dict], str]:
    cfg = app_config().get("blast", {})
    min_sup, min_lift = cfg.get("min_support", 0.6), cfg.get("min_lift", 1.5)
    A = _store_attrs(data)
    a = A.loc[affected]
    markets = a["market"].unique().tolist()
    pop = A[A["market"].isin(markets)]
    pop_label = "stores in these markets"
    if len(affected) < 5:
        regions = a["region"].unique().tolist()
        pop = A[A["region"].isin(regions)]
        pop_label = "stores in this region"
        if len(pop) < 20:
            pop, pop_label = A, "stores fleet-wide"
    cands = []
    for attr in ATTRS:
        vals = a[attr].dropna()
        if vals.empty:
            continue
        v = vals.mode().iat[0]
        sup = float((a[attr] == v).mean())
        base = float((pop[attr] == v).mean())
        if base <= 0:
            continue
        lift = sup / base
        n_has = int((a[attr] == v).sum())
        cands.append(dict(attribute=attr, label=ATTR_LABEL[attr], value=v, support=round(sup, 2), base=round(base, 2),
                          lift=round(lift, 2), n_with=n_has, n_affected=len(a), population=pop_label,
                          network=attr in NETWORK_ATTRS))
    ok = [c for c in cands if c["support"] >= min_sup and c["lift"] >= min_lift]
    pref = group == "connectivity"
    ok.sort(key=lambda c: (-(round(c["lift"] * c["support"], 2)), 0 if (pref and c["network"]) else 1))
    for c in ok:
        c["explanation"] = (f"{c['n_with']} of {c['n_affected']} affected stores ({c['support']:.0%}) are on "
                            f"{c['value']} vs {c['base']:.0%} of {c['population']} (lift {c['lift']:.1f}×).")
    return (ok[0] if ok else None), ok[1:5], pop_label


def detect_blast_radius(data: Data, number: str | None = None, store_id: int | None = None,
                        at_time: pd.Timestamp | None = None) -> dict:
    _ensure_group(data)
    now = data.now()
    cfg = app_config().get("blast", {})
    before, after = cfg.get("window_before_h", 3), cfg.get("window_after_h", 1)
    inc = data.incident
    anchor_store = None
    if number:
        r = inc_row(data, number)
        if r is None:
            raise KeyError(number)
        if bool(r["major_incident"]):
            kids = inc[inc["parent_incident"] == number]
            if len(kids):
                k0 = kids.sort_values("opened_at").iloc[0]
                r = k0
        t = r["opened_at"]
        group = group_of(r["issue_type"], r["category"])
        anchor_store = int(r["store_id"]) if pd.notna(r["store_id"]) else None
        anchor_num = r["number"]
    else:
        t = at_time or now
        anchor_store = int(store_id) if store_id is not None else None
        # pick the store's most recent open incident to define the group
        mine = inc[(inc["store_id"] == anchor_store) & inc["state"].isin(["New", "In Progress", "On Hold"])]
        if mine.empty:
            mine = inc[inc["store_id"] == anchor_store].tail(1)
        if mine.empty:
            raise KeyError(f"No incidents for store {store_id}")
        pr = mine.sort_values("priority").iloc[0]
        group, t, anchor_num = pr["grp"], pr["opened_at"], pr["number"]
    lo, hi = t - before * H, min(t + after * H, now)
    peers = inc[(inc["grp"] == group) & ~inc["major_incident"] & inc["store_id"].notna()
                & (inc["opened_at"] >= lo) & (inc["opened_at"] <= hi)]
    affected = sorted(peers["store_id"].astype(int).unique().tolist())
    an = _anomaly(data, group, lo, hi, len(peers))
    cause, others, pop_label = (None, [], None)
    if an["unusual"] and len(affected) >= 3:
        cause, others, pop_label = _attribution(data, affected, group)

    # if a cause is found, the blast radius is the stores that share it (others are background noise)
    in_cluster = set(affected)
    if cause:
        A = _store_attrs(data)
        in_cluster = {s for s in affected if A.at[s, cause["attribute"]] == cause["value"]}
    A = _store_attrs(data)
    stores_out, offline, degraded = [], 0, 0
    per_store_loss = {}
    for sid in affected:
        rows = peers[peers["store_id"] == sid]
        is_off = bool(rows["issue_type"].isin(OFFLINE_TYPES).any())
        main = rows.sort_values("priority").iloc[0]
        loss = max(incident_impact(data, n, now)["usd"] for n in rows["number"])
        if sid in in_cluster:
            offline += is_off
            degraded += not is_off
            per_store_loss[sid] = loss
        stores_out.append(dict(store_id=sid, lat=float(A.at[sid, "latitude"]), lon=float(A.at[sid, "longitude"]),
                               market=A.at[sid, "market"], region=A.at[sid, "region"],
                               status="offline" if is_off else "degraded", incident_number=main["number"],
                               parent_incident=main["parent_incident"] if pd.notna(main["parent_incident"]) else None,
                               in_cluster=sid in in_cluster, anchor=sid == anchor_store, est_impact_usd=round(loss, 2)))
    cl = [s for s in stores_out if s["in_cluster"]]
    geo = None
    if cl:
        lats, lons = np.array([s["lat"] for s in cl]), np.array([s["lon"] for s in cl])
        c_lat, c_lon = float(lats.mean()), float(lons.mean())
        dist = _haversine(c_lat, c_lon, lats, lons)
        geo = dict(centroid=[c_lat, c_lon], max_radius_km=round(float(dist.max()), 1),
                   bbox=[[float(lats.min()), float(lons.min())], [float(lats.max()), float(lons.max())]])
        if anchor_store is not None:
            al, ao = A.at[anchor_store, "latitude"], A.at[anchor_store, "longitude"]
            for s in stores_out:
                s["dist_km"] = round(float(_haversine(al, ao, s["lat"], s["lon"])), 1)
    parents = sorted(peers["parent_incident"].dropna().unique().tolist())
    parent_info = []
    for p in parents:
        pr = inc_row(data, p)
        parent_info.append(dict(number=p, short_description=pr["short_description"], state=pr["state"],
                                child_count=int((inc["parent_incident"] == p).sum())))
    sugg = peers[peers["parent_incident"].isna() & peers["store_id"].astype(int).isin(in_cluster)]
    suggested = [dict(number=r.number, store_id=int(r.store_id), short_description=r.short_description,
                      opened_at=r.opened_at.isoformat()) for r in sugg.itertuples()] if parents else []
    markets = sorted({s["market"] for s in cl})
    n_cl = len(cl)
    gl = GROUP_LABEL.get(group, group)
    if an["unusual"]:
        headline = (f"{n_cl} stores with {gl} incidents in the window — {an['observed']} tickets vs "
                    f"{an['expected']:.1f} expected (unusual).")
    else:
        headline = (f"No unusual cluster: {an['observed']} {gl} tickets in the window vs {an['expected']:.1f} "
                    f"expected for this weekday/hour.")
    cites = [anchor_num] + parents + [s["incident_number"] for s in cl[:5]]
    return dict(anchor=dict(number=anchor_num, store_id=anchor_store, t=t.isoformat(), group=group,
                            window=[lo.isoformat(), hi.isoformat()]),
                affected_store_count=n_cl, total_stores_with_peer_incidents=len(affected),
                offline_count=int(offline), degraded_count=int(degraded),
                unusual=an["unusual"], p_value=an["p_value"], expected=an["expected"], observed=an["observed"],
                likely_common_cause=cause, other_candidates=others, markets=markets, geo=geo, stores=stores_out,
                parent_incidents=parent_info, suggested_links=suggested[:25], suggested_link_count=len(suggested),
                est_impact_usd_so_far=round(sum(per_store_loss.values()), 2), summary=headline,
                citations=list(dict.fromkeys([c for c in cites if c])))
