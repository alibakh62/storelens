"""Narrative generators (SPEC §8.5): handoff digest, PIR markdown, Teams Adaptive Card. Input is ONLY the
deterministic JSON. Uses the live LLM when available; otherwise deterministic templates."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .agent.providers import chain, live_provider
from .agent.templates import hours, money, t_local
from .config import app_config

PROMPTS = Path(__file__).resolve().parent / "agent" / "prompts"


def _live():
    for name in chain():
        if name in ("replay", "template"):
            return None
        p = live_provider(name)
        if p:
            return p
    return None


def handoff_digest(h: dict) -> tuple[str, str]:
    p = _live()
    if p:
        try:
            txt = p.complete((PROMPTS / "handoff.md").read_text(), json.dumps(h, default=str)[:14000],
                             timeout_s=app_config().get("llm", {}).get("timeout_s", 12))
            return " ".join(txt.split()[:150]), "live"
        except Exception:  # noqa: BLE001
            pass
    s = h["sections"]
    reg = "all regions" if h["region"] == "All" else h["region"]
    parts = [f"Shift handoff for {reg} as of {t_local(h['as_of'], date=True)}."]
    for m in s["majors"][:2]:
        usd = f", {money(m['usd_so_far'])} est. lost sales so far" if m.get("usd_so_far") is not None else ""
        parts.append(f"Major {m['number']} is still open: {m['short_description'].replace('MAJOR: ', '')} — "
                     f"{m['stores']} stores{usd}.")
    parts.append(f"{s['p1_p2_open_count']} P1/P2 incidents open; {s['sla_risk_count']} breached or within 2h of SLA"
                 + (f" (e.g. {', '.join(x['number'] for x in s['sla_risk'][:2])})" if s["sla_risk"] else "") + ".")
    if s["on_hold_24h_count"]:
        parts.append(f"{s['on_hold_24h_count']} incidents on hold for more than 24h — chase vendors/parts.")
    if s["new_chronic"]:
        c = s["new_chronic"][0]
        parts.append(f"{len(s['new_chronic'])} chronic devices failed again this week; worst is store {c['store_id']} "
                     f"({c['unit']}, {c['count_365d']} failures in 12 months).")
    if s["changes_next_24h"]:
        c = s["changes_next_24h"][0]
        parts.append(f"{len(s['changes_next_24h'])} change(s) scheduled in the next 24h, starting with {c['number']} "
                     f"({c['short_description']}).")
    if s["notable_resolutions"]:
        r = s["notable_resolutions"][0]
        parts.append(f"Resolved recently: {r['number']} ({r['short_description']}).")
    words = " ".join(parts).split()
    return " ".join(words[:150]), "template"


def adaptive_card(headline: str, bullets: list[str], impact: dict | None, open_url: str, parent: str | None,
                  status: str = "info") -> dict:
    color = {"critical": "Attention", "watch": "Warning", "healthy": "Good"}.get(status, "Accent")
    body = [{"type": "TextBlock", "text": "StoreLens", "weight": "Bolder", "size": "Small", "isSubtle": True},
            {"type": "TextBlock", "text": headline, "weight": "Bolder", "size": "Medium", "wrap": True, "color": color}]
    for b in bullets[:3]:
        body.append({"type": "TextBlock", "text": f"• {b}", "wrap": True, "spacing": "Small"})
    if impact and impact.get("usd_so_far") is not None:
        body.append({"type": "FactSet", "facts": [
            {"title": "Est. lost sales", "value": money(impact["usd_so_far"])},
            {"title": "Per hour now", "value": money(impact.get("per_hour_now") or 0)}]})
    actions = [{"type": "Action.OpenUrl", "title": "Open in StoreLens", "url": open_url}]
    if parent:
        actions.append({"type": "Action.OpenUrl", "title": "View parent incident", "url": f"{open_url}#incident={parent}"})
    return {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.5",
            "body": body, "actions": actions}


def pir_markdown(f: dict) -> tuple[str, str]:
    p = _live()
    if p:
        try:
            return p.complete((PROMPTS / "pir.md").read_text(), json.dumps(f, default=str)[:14000]), "live"
        except Exception:  # noqa: BLE001
            pass
    im, rc, fu = f["impact"], f["root_cause"], f["followups"]
    usd = f"{money(im['usd'])} est. lost sales" if im.get("usd") is not None else "lost sales: restricted for this role"
    L = [f"# {f['doc_title']} — {f['number']}", "", f"_{f['title']}_ · State: **{f['state']}**", "",
         "## Summary",
         f"{f['title'].replace('MAJOR: ', '')}. {im['stores']} stores affected ({im['offline']} fully offline, "
         f"{im['degraded']} degraded); {im['child_incidents']} child incidents linked to {f['number']}.", "",
         "## Customer / Business impact",
         f"- Duration: {hours(im['duration_h'])}" + (" (ongoing)" if f["interim"] else ""),
         f"- {usd}" + (f"; {money(im['per_hour_now'])}/hour at the current hour" if im.get("per_hour_now") else ""),
         f"- {im['offline']} stores cash-only (no card payments, no pay-at-pump); {im['degraded']} on LTE backup", "",
         "## Timeline (CT)"]
    for e in f["timeline"]:
        L.append(f"- **{t_local(e['ts'])}** — {e['text']}" + (f" ({e['incident_number']})" if e.get("incident_number") else ""))
    L += ["", "## Root cause"]
    if rc.get("parent_close_notes"):
        L.append(f"- {rc['parent_close_notes']} ({f['number']})")
    if rc.get("problem"):
        L.append(f"- Problem {rc['problem']} ({rc['problem_state']}): {rc['problem_title']}"
                 + (f" — {rc['cause_notes']}" if rc.get("cause_notes") else ""))
    if not rc.get("parent_close_notes") and not rc.get("cause_notes"):
        L.append("- Root cause pending (RCA in progress).")
    L += ["", "## Resolution", f"- {f['resolution']}" if f.get("resolution") else
          "- Not yet resolved." + (f" Workaround in place: {f['workaround']}" if f.get("workaround") else "")]
    L += ["", "## What went well / what didn't"]
    if f.get("minutes_to_declare") is not None:
        L.append(f"- Major declared {f['minutes_to_declare']:.0f} min after the first child alert.")
    L.append(f"- Change ruled out: {f['change_negative_evidence'][0]}" if f.get("change_negative_evidence") else
             f"- Linked change(s): {', '.join(f['linked_changes'])}")
    if fu.get("unlinked_lookalikes"):
        L.append(f"- {len(fu['unlinked_lookalikes'])} look-alike tickets were not linked to the parent.")
    L += ["", "## Follow-ups"]
    if fu.get("open_problem"):
        L.append(f"- Complete RCA on {fu['open_problem']}.")
    for c in fu.get("chronic_among_affected", [])[:5]:
        L.append(f"- Store {c['store_id']}: chronic {c['unit']} ({c['count_365d']} failures in 12 months).")
    for n in fu.get("unlinked_lookalikes", [])[:5]:
        L.append(f"- Review and link {n}.")
    L += ["", "_Estimate: hourly sales baseline × service loss profile. Gross sales, not margin. Generated by StoreLens "
          "from cited records; review before sharing._"]
    return "\n".join(L), "template"
