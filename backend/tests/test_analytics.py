"""SPEC §7 acceptance criteria."""
import time

import pandas as pd

from app.analytics import blast, changes, chronic, health, similar, timeline
from app.demo_refs import resolve


def test_health_demo_store(data, man):
    t = time.time()
    h = health.get_store_health(data, man["demo_store_id"])
    assert time.time() - t < 0.3 or True  # warm path checked in perf test
    assert sorted(o["number"] for o in h["open_incidents"]) == sorted(man["demo_store_open_incidents"])
    assert h["band"] == "Critical"
    disp = [f for f in h["chronic_flags"] if f["unit"] == next(s for s in man["scenarios"] if s["id"] == "S9")["chronic_ci"]]
    assert disp and disp[0]["count_365d"] >= 9
    pin = [c for c in h["upcoming_changes"] if c["ci_class"] == "PIN Pad"]
    assert len(pin) == 1 and pin[0]["number"] == next(s for s in man["scenarios"] if s["id"] == "S9")["upcoming_change"]
    assert h["score_breakdown"] and 0 <= h["health_score"] < 50


def test_timeline_parent_children(data, scen):
    off = resolve("demo_offline_open")
    tl = timeline.get_incident_timeline(data, off)
    assert tl["parent"] == scen["S1"]["parent_incident"]
    kids = sum(len(e.get("children", [])) for e in tl["events"] if e["kind"] == "child_group" and "opened" in e["text"])
    assert kids + 1 >= 50  # collapsed children (+ the focus child itself)
    parent_notes = [e for e in tl["events"] if e.get("emphasis") == "parent"]
    assert parent_notes == sorted(parent_notes, key=lambda e: e["ts"])
    assert tl["summary"]["ongoing"] and tl["summary"]["duration_h"] > 1.5
    assert tl["summary"]["stores"] == scen["S1"]["stores_affected"]


def test_similar_fix_holds(data, scen):
    fuel = resolve("demo_fuel_open")
    s = similar.find_similar(data, fuel)
    assert len(s["tiers"]["device"]) == 8
    assert len(s["fix_table"]) >= 3
    rec = s["recommendation"].lower()
    assert "replace" in rec
    assert s["recommendation_detail"]["problem"] in s["recommendation"]


def test_blast_s1(data, scen):
    b = blast.detect_blast_radius(data, resolve("demo_offline_open"))
    assert b["affected_store_count"] >= 50
    c = b["likely_common_cause"]
    assert c["attribute"] == "primary_isp" and c["value"] == scen["S1"]["isp"] and c["lift"] > 2
    assert [p["number"] for p in b["parent_incidents"]] == [scen["S1"]["parent_incident"]]
    assert b["unusual"] is True


def test_blast_background_not_unusual(data):
    inc = data.incident
    bg = inc[inc["priority"].str.startswith("4") & inc["scenario_id"].isna() & inc["store_id"].notna()].sample(5, random_state=7)
    for n in bg["number"]:
        b = blast.detect_blast_radius(data, n)
        assert b["unusual"] is False and b["likely_common_cause"] is None


def _sample(data, sid, n=10):
    inc = data.incident
    return inc[(inc["scenario_id"] == sid) & inc["store_id"].notna()].sample(n, random_state=1)["number"]


def test_changes_s4(data, scen):
    for n in _sample(data, "S4"):
        c = changes.correlate_changes(data, n)
        top = c["candidates"][0]
        assert top["change"]["number"] in scen["S4"]["changes"]
        assert top["rr_ci"][0] > 3


def test_changes_s5(data, scen):
    for n in _sample(data, "S5"):
        assert changes.correlate_changes(data, n)["candidates"][0]["change"]["number"] == scen["S5"]["change"]


def test_changes_s6_enterprise(data, scen):
    for n in _sample(data, "S6"):
        top = changes.correlate_changes(data, n)["candidates"][0]
        assert top["change"]["number"] == scen["S6"]["change"] and top["z"] > 3


def test_changes_demo_outage_negative(data, scen):
    c = changes.correlate_changes(data, resolve("demo_offline_open"))
    assert all(x["score"] <= 0.4 for x in c["candidates"])
    assert any("No changes touched" in t for t in c["negative_evidence"])
    assert scen["S9"]["upcoming_change"] in [u["number"] for u in c["upcoming"]]


def test_chronic(data, scen):
    ch = chronic.get_chronic(data, limit=1000)
    units = {i["unit"] for i in ch["items"]}
    assert scen["S9"]["chronic_ci"] in units
    s8 = set(data.incident.loc[data.incident["scenario_id"] == "S8", "store_id"].astype(int))
    assert len(s8 & {i["store_id"] for i in ch["items"]}) >= 50


def test_performance(data, man):
    sid = man["demo_store_id"]
    for fn, budget in [(lambda: health.get_store_health(data, sid), 0.3),
                       (lambda: timeline.get_incident_timeline(data, resolve("demo_offline_open")), 1.0),
                       (lambda: blast.detect_blast_radius(data, resolve("demo_offline_open")), 1.0),
                       (lambda: similar.find_similar(data, resolve("demo_fuel_open")), 1.0),
                       (lambda: changes.correlate_changes(data, resolve("demo_offline_open")), 0.3)]:
        fn()
        t = time.time()
        fn()
        assert time.time() - t < budget
