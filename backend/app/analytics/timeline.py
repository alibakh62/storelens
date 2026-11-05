"""Parent/child timeline stitching (SPEC §7.2)."""
from __future__ import annotations

import re

import pandas as pd

from ..db import Data, clean, inc_row
from .groups import group_of

COLLAPSE_GAP = pd.Timedelta(minutes=10)
VREF = re.compile(r"\b[A-Z]{2,5}-\d{6,8}\b")


def _family(data: Data, number: str) -> tuple[pd.Series | None, pd.DataFrame]:
    r = inc_row(data, number)
    if r is None:
        raise KeyError(number)
    inc = data.incident
    parent = None
    if pd.notna(r["parent_incident"]):
        parent = inc_row(data, r["parent_incident"])
    elif bool(r["major_incident"]) or int(r["child_incident_count"] or 0) > 0:
        parent = r
    if parent is None:
        return None, inc.iloc[[data.inc_by_number[number]]]
    kids = inc[inc["parent_incident"] == parent["number"]]
    fam = pd.concat([inc.iloc[[data.inc_by_number[parent["number"]]]], kids])
    return parent, fam


def _synthetic(r) -> list[dict]:
    ev = []
    sid = clean(r["store_id"])
    base = dict(incident_number=r["number"], store_id=sid)
    ev.append(dict(ts=r["opened_at"], kind="opened", actor=r["opened_by"], text=f"Opened: {r['short_description']}", **base))
    if pd.notna(r["assigned_to"]):
        ev.append(dict(ts=r["first_response_at"] if pd.notna(r["first_response_at"]) else r["opened_at"], kind="assigned",
                       actor=r["assigned_to"], text=f"Assigned to {r['assignment_group']} ({r['assigned_to']})", **base))
    if r["state"] == "On Hold":
        ev.append(dict(ts=r["sys_updated_on"], kind="on_hold", actor=r["assigned_to"],
                       text=f"On Hold — {r['on_hold_reason']}", **base))
    if pd.notna(r["resolved_at"]):
        ev.append(dict(ts=r["resolved_at"], kind="resolved", actor=r["resolved_by"],
                       text=f"Resolved ({r['close_code']}): {r['close_notes']}", **base))
    if pd.notna(r["closed_at"]):
        ev.append(dict(ts=r["closed_at"], kind="closed", actor="system", text="Closed", **base))
    return ev


def _notes(data: Data, number: str, r) -> list[dict]:
    idx = data.notes_by_inc.get(number)
    if idx is None:
        return []
    nt = data.notes.iloc[idx]
    out = []
    for n in nt.itertuples():
        if n.entry_type == "system" and n.text.startswith("Incident created"):
            continue  # already covered by the synthetic "opened" event
        out.append(dict(ts=n.created_at, incident_number=number, store_id=clean(r["store_id"]), actor=n.author,
                        kind=n.entry_type, text=n.text))
    return out


def get_incident_timeline(data: Data, number: str, now: pd.Timestamp | None = None) -> dict:
    now = now or data.now()
    parent, fam = _family(data, number)
    events: list[dict] = []
    focus = {number} | ({parent["number"]} if parent is not None else set())
    child_rows = fam[fam["number"] != (parent["number"] if parent is not None else "")]
    for _, r in fam.iterrows():
        if r["number"] in focus:
            for e in _synthetic(r) + _notes(data, r["number"], r):
                e["emphasis"] = "parent" if parent is not None and r["number"] == parent["number"] else "self"
                events.append(e)
    # other children: collapse their "opened" events and their notes
    others = child_rows[~child_rows["number"].isin(focus)].sort_values("opened_at")
    groups = []
    cur = None
    for r in others.itertuples():
        if cur is not None and r.opened_at - cur["end"] <= COLLAPSE_GAP:
            cur["items"].append(r)
            cur["end"] = r.opened_at
        else:
            cur = dict(start=r.opened_at, end=r.opened_at, items=[r])
            groups.append(cur)
    for g in groups:
        stores = sorted({int(x.store_id) for x in g["items"] if pd.notna(x.store_id)})
        st, en = g["start"], g["end"]
        off = -6
        if len(g["items"]) == 1:
            x = g["items"][0]
            events.append(dict(ts=st, kind="child_opened", incident_number=x.number, store_id=clean(x.store_id),
                               actor=x.opened_by, text=f"Store {int(x.store_id)} opened {x.number}: {x.short_description}",
                               emphasis="child"))
        else:
            fmt = lambda t: (t + pd.Timedelta(hours=off)).strftime("%H:%M")  # noqa: E731
            events.append(dict(ts=st, kind="child_group", incident_number=None, store_id=None, actor="multiple",
                               text=f"{len(stores)} stores opened tickets {fmt(st)}–{fmt(en)} CT",
                               emphasis="child", collapsed=True,
                               children=[dict(number=x.number, store_id=clean(x.store_id), ts=x.opened_at.isoformat(),
                                              short_description=x.short_description, state=x.state)
                                         for x in g["items"]]))
    # children resolution wave (collapsed)
    res = others[others["resolved_at"].notna()]
    if len(res) > 1:
        events.append(dict(ts=res["resolved_at"].min(), kind="child_group", incident_number=None, store_id=None,
                           actor="multiple", text=f"{len(res)} child incidents resolved", emphasis="child", collapsed=True,
                           children=[dict(number=x.number, store_id=clean(x.store_id), ts=x.resolved_at.isoformat(),
                                          short_description=x.short_description, state=x.state) for x in res.itertuples()]))
    events = [e for e in events if pd.notna(e["ts"]) and e["ts"] <= now]
    events.sort(key=lambda e: (e["ts"], 0 if e.get("emphasis") == "parent" else 1))
    for e in events:
        e["ts"] = e["ts"].isoformat()

    # summary
    summary = {}
    if parent is not None:
        kids = child_rows
        first_alert = kids["opened_at"].min() if len(kids) else parent["opened_at"]
        end = parent["resolved_at"] if pd.notna(parent["resolved_at"]) else now
        pnotes = _notes(data, parent["number"], parent)
        isp_ref = None
        for n in pnotes:
            m = VREF.search(n["text"])
            if m:
                isp_ref = m.group(0)
                break
        offline = kids[kids["issue_type"].isin(["store_offline", "power_outage"])]["store_id"].nunique()
        stores = kids["store_id"].nunique()
        summary = dict(parent=parent["number"], parent_title=parent["short_description"], parent_state=parent["state"],
                       first_alert=first_alert.isoformat(), major_declared=parent["opened_at"].isoformat(),
                       isp_ticket=isp_ref, restored_at=clean(parent["resolved_at"]),
                       duration_h=round(float((end - first_alert) / pd.Timedelta(hours=1)), 2),
                       ongoing=pd.isna(parent["resolved_at"]), child_count=int(len(kids)), stores=int(stores),
                       stores_offline=int(offline), stores_degraded=int(stores - offline),
                       minutes_to_declare=round(float((parent["opened_at"] - first_alert) / pd.Timedelta(minutes=1)), 1))
    else:
        r = inc_row(data, number)
        end = r["resolved_at"] if pd.notna(r["resolved_at"]) else now
        summary = dict(parent=None, first_alert=r["opened_at"].isoformat(),
                       duration_h=round(float((end - r["opened_at"]) / pd.Timedelta(hours=1)), 2),
                       ongoing=pd.isna(r["resolved_at"]), child_count=0)

    # unlinked look-alikes
    suggested = []
    if parent is not None:
        try:
            from .blast import detect_blast_radius
            b = detect_blast_radius(data, parent["number"])
            suggested = b["suggested_links"]
        except KeyError:
            pass
    cites = [number] + ([parent["number"]] if parent is not None else []) + [x["number"] for x in suggested[:3]]
    return dict(number=number, parent=parent["number"] if parent is not None else None, events=events, summary=summary,
                suggested_links=suggested, group=group_of(inc_row(data, number)["issue_type"]),
                citations=list(dict.fromkeys(cites)))
