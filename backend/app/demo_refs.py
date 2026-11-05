"""Resolve symbolic demo references from scenario_manifest.json (never hard-code scenario IDs)."""
from __future__ import annotations

from .db import get_data, inc_row


def resolve(ref):
    d = get_data()
    man = d.manifest
    scen = {s["id"]: s for s in man["scenarios"]}
    if ref in (None, "", "null"):
        return None
    if ref == "demo_store":
        return man["demo_store_id"]
    if ref == "demo_fuel_open":
        for n in man["demo_store_open_incidents"]:
            if inc_row(d, n)["category"] == "Fuel":
                return n
    if ref == "demo_offline_open":
        for n in man["demo_store_open_incidents"]:
            if inc_row(d, n)["issue_type"] == "store_offline":
                return n
    if ref == "demo_declines_open":
        for n in man["demo_store_open_incidents"]:
            if inc_row(d, n)["issue_type"] == "card_declines":
                return n
    if isinstance(ref, str) and "." in ref:          # e.g. "S1.parent_incident", "S4.changes", "S9.upcoming_change"
        sid, key = ref.split(".", 1)
        return scen[sid][key]
    return ref
