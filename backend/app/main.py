"""StoreLens API (SPEC §9)."""
from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
from pathlib import Path

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import audit
from .agent.orchestrator import run_chat
from .agent.providers import chain, live_provider
from .agent.tools import execute, list_incidents
from .analytics import health, impact, value
from .clock import fmt_local
from .config import ROOT, app_config
from .db import OPEN_STATES, clean, get_data, inc_row
from .narrative import adaptive_card, handoff_digest, pir_markdown
from .rbac import Scope, ScopeError, check_store, default_personas, demo_personas, in_scope_store, mask, scope_dep
from .startup import warm

app = FastAPI(title="StoreLens", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
_WARM: dict = {}


@app.on_event("startup")
def _startup():
    _WARM.update(warm())


def tool(name: str, args: dict, scope: Scope):
    res, tr = execute(name, args, scope)
    if isinstance(res, dict) and res.get("error"):
        code = 403 if res.get("restricted") else 404
        raise HTTPException(code, res["error"])
    audit.write(dict(persona=scope.user_id, persona_name=scope.name, route=name, args=args, ms=tr["ms"],
                     masked_fields=tr["masked_fields"], provider_mode="deterministic"))
    return res


# ---------------------------------------------------------------- meta
@app.get("/api/meta")
def meta():
    d = get_data()
    now = d.now()
    man = d.manifest
    s1 = next((s for s in man["scenarios"] if s["id"] == "S1"), {})
    live = [n for n in chain() if n not in ("replay", "template") and live_provider(n)]
    return dict(now=now.isoformat(), now_display=f"As of {fmt_local(now, -6, with_date=True)} · simulated",
                clock_mode=d.clock.mode, mock_now=d.clock.mock_now.isoformat(),
                personas=demo_personas(), default_personas=default_personas(),
                demo=dict(store_id=man["demo_store_id"], open_incidents=man["demo_store_open_incidents"],
                          major=s1.get("parent_incident"), scenarios=[dict(id=s["id"], title=s["title"], status=s["status"])
                                                                     for s in man["scenarios"]]),
                provider=dict(configured=app_config().get("llm", {}).get("provider"), chain=chain(),
                              live_available=live, mode="Live" if live else "Replay"),
                startup=_WARM, regions=sorted(d.stores["region"].unique().tolist()))


@app.post("/api/admin/reset")
def reset():
    get_data().clock.reset()
    return dict(ok=True, now=get_data().now().isoformat())


# ---------------------------------------------------------------- stores
@app.get("/api/stores/search")
def store_search(q: str = "", scope: Scope = Depends(scope_dep)):
    d = get_data()
    st = d.stores
    q = q.strip()
    if not q:
        m = st[st["store_id"] == d.manifest["demo_store_id"]]
    elif q.isdigit():
        m = st[st["store_id"].astype(str).str.startswith(q)]
    else:
        ql = q.lower()
        m = st[st["city"].str.lower().str.contains(ql) | st["store_name"].str.lower().str.contains(ql)
               | st["market"].str.lower().str.contains(ql)]
    if scope.region_scope != "All":
        m = m[m["region"] == scope.region_scope]
    return [dict(store_id=int(r.store_id), store_name=r.store_name, city=r.city, state=r.state, region=r.region,
                 store_format=r.store_format) for r in m.head(10).itertuples()]


@app.get("/api/stores/{sid}/profile")
def store_profile(sid: int, scope: Scope = Depends(scope_dep)):
    return tool("get_store_profile", dict(store_id=sid), scope)


@app.get("/api/stores/{sid}/health")
def store_health(sid: int, scope: Scope = Depends(scope_dep)):
    return tool("get_store_health", dict(store_id=sid), scope)


@app.get("/api/stores/{sid}/incidents")
def store_incidents(sid: int, state: str | None = None, days: int | None = None, limit: int = 50,
                    scope: Scope = Depends(scope_dep)):
    return tool("list_incidents", dict(store_id=sid, state=state, days=days, limit=limit), scope)


@app.get("/api/stores/{sid}/changes")
def store_changes(sid: int, lookback_h: int | None = None, lookahead_d: int | None = None,
                  scope: Scope = Depends(scope_dep)):
    return tool("correlate_changes", dict(store_id=sid), scope)


@app.get("/api/stores/{sid}/impact")
def store_impact(sid: int, days: int = 365, scope: Scope = Depends(scope_dep)):
    return tool("estimate_impact", dict(store_id=sid, days=days), scope)


# ---------------------------------------------------------------- incidents
@app.get("/api/incidents/{num}")
def incident(num: str, scope: Scope = Depends(scope_dep)):
    return tool("get_incident", dict(number=num, include_notes=True), scope)


@app.get("/api/incidents/{num}/timeline")
def incident_timeline(num: str, scope: Scope = Depends(scope_dep)):
    return tool("get_incident_timeline", dict(number=num), scope)


@app.get("/api/incidents/{num}/similar")
def incident_similar(num: str, scope: Scope = Depends(scope_dep)):
    return tool("find_similar_incidents", dict(number=num), scope)


@app.get("/api/incidents/{num}/blast-radius")
def incident_blast(num: str, scope: Scope = Depends(scope_dep)):
    return tool("detect_blast_radius", dict(number=num), scope)


@app.get("/api/incidents/{num}/changes")
def incident_changes(num: str, scope: Scope = Depends(scope_dep)):
    return tool("correlate_changes", dict(number=num), scope)


@app.get("/api/incidents/{num}/impact")
def incident_impact(num: str, scope: Scope = Depends(scope_dep)):
    return tool("estimate_impact", dict(number=num), scope)


@app.get("/api/records/{rid}")
def record(rid: str, scope: Scope = Depends(scope_dep)):
    """Record drawer for CHG / PRB / KB (and INC)."""
    d = get_data()
    if rid.startswith("INC"):
        return dict(kind="incident", record=tool("get_incident", dict(number=rid, include_notes=True), scope))
    for kind, df, key in [("change", d.change, "number"), ("problem", d.problem, "number"), ("kb", d.kb, "number")]:
        m = df[df[key] == rid]
        if len(m):
            r = {k: clean(v) for k, v in m.iloc[0].items()}
            if kind == "change":
                stores = d.change_map[d.change_map["change_number"] == rid]["store_id"].astype(int).tolist()
                r["store_ids"] = [s for s in stores if in_scope_store(scope, s)][:50]
                r["store_count"] = len(stores)
            return mask(dict(kind=kind, record=r), scope)
    raise HTTPException(404, f"{rid} not found")


@app.get("/api/majors")
def majors(state: str = "open", scope: Scope = Depends(scope_dep)):
    d = get_data()
    inc = d.incident
    m = inc[inc["major_incident"] & (inc["opened_at"] <= d.now())]
    if state == "open":
        m = m[m["state"].isin(OPEN_STATES)]
    out = []
    for _, r in m.sort_values("opened_at", ascending=False).iterrows():
        kids = inc[inc["parent_incident"] == r["number"]]
        if scope.region_scope != "All" and not (kids["region"] == scope.region_scope).any():
            continue
        mi = impact.major_impact(d, r["number"])
        offline = kids[kids["issue_type"].isin(["store_offline", "power_outage"])]["store_id"].nunique()
        mk = kids["market"].dropna().unique().tolist()
        out.append(dict(number=r["number"], short_description=r["short_description"], state=r["state"],
                        opened_at=r["opened_at"].isoformat(), stores=mi["stores"], offline=int(offline), markets=mk,
                        usd_so_far=mi["usd_so_far"], per_hour_now=mi["per_hour_now"], as_of=mi["as_of"],
                        anchor_store=int(kids.sort_values("opened_at")["store_id"].dropna().iloc[0]) if len(kids) else None,
                        region=kids["region"].mode().iat[0] if len(kids) else None))
    return mask(out, scope)


# ---------------------------------------------------------------- fleet
@app.get("/api/fleet/chronic")
def fleet_chronic(region: str | None = None, days: int | None = None, scope: Scope = Depends(scope_dep)):
    return tool("get_chronic", dict(region=region), scope)


@app.get("/api/fleet/overview")
def fleet_overview(region: str | None = None, scope: Scope = Depends(scope_dep)):
    d = get_data()
    now = d.now()
    inc = d.incident
    op = inc[inc["state"].isin(OPEN_STATES) & ~inc["major_incident"] & (inc["opened_at"] <= now)]
    if scope.region_scope != "All":
        region = scope.region_scope
    if region and region != "All":
        op = op[op["region"] == region]
    tiles = []
    for reg, g in op.groupby("region"):
        risk = sum(1 for _, r in g.iterrows() if health.sla_status(r, now)["status"] in ("breached", "at_risk"))
        tiles.append(dict(region=reg, open=int(len(g)),
                          by_priority={f"P{k}": int(v) for k, v in g["priority"].str[0].value_counts().sort_index().items()},
                          by_state={k: int(v) for k, v in g["state"].value_counts().items()}, sla_at_risk=risk,
                          majors=int(g["parent_incident"].dropna().nunique()), stores=int(g["store_id"].nunique())))
    tiles.sort(key=lambda t: -t["open"])
    return dict(as_of=now.isoformat(), tiles=tiles, total_open=int(len(op)), majors=majors(scope=scope))


@app.get("/api/handoff")
def handoff(region: str = "All", scope: Scope = Depends(scope_dep)):
    h = tool("get_shift_handoff", dict(region=region), scope)
    digest, mode = handoff_digest(h)
    s = h["sections"]
    bullets = [f"{m['number']}: {m['short_description'].replace('MAJOR: ', '')} — {m['stores']} stores" for m in s["majors"][:1]]
    bullets.append(f"{s['p1_p2_open_count']} P1/P2 open · {s['sla_risk_count']} at SLA risk · {s['on_hold_24h_count']} on hold > 24h")
    if s["changes_next_24h"]:
        bullets.append(f"Next 24h: {s['changes_next_24h'][0]['number']} {s['changes_next_24h'][0]['short_description']}")
    imp = dict(usd_so_far=s["majors"][0]["usd_so_far"], per_hour_now=s["majors"][0]["per_hour_now"]) if s["majors"] else None
    card = adaptive_card(f"Shift handoff — {h['region']}", bullets, imp, "http://localhost:8000/ops",
                         s["majors"][0]["number"] if s["majors"] else None, "watch")
    md = f"# Shift handoff — {h['region']}\n\n{digest}\n"
    return dict(**h, digest=digest, digest_mode=mode, adaptive_card=card, markdown=md)


class CardReq(BaseModel):
    headline: str
    bullets: list[str] = []
    impact: dict | None = None
    status: str = "info"
    parent: str | None = None
    store_id: int | None = None


@app.post("/api/teams/card")
def teams_card(req: CardReq, scope: Scope = Depends(scope_dep)):
    url = f"http://localhost:8000/stores/{req.store_id}" if req.store_id else "http://localhost:8000/"
    return mask(adaptive_card(req.headline, req.bullets, req.impact, url, req.parent, req.status), scope)


@app.post("/api/pir/{num}")
def pir(num: str, scope: Scope = Depends(scope_dep)):
    f = tool("get_pir_facts", dict(number=num), scope)
    md, mode = pir_markdown(f)
    return dict(markdown=mask(md, scope), mode=mode, facts=f, title=f["doc_title"])


class NLQReq(BaseModel):
    question: str


@app.post("/api/nlq")
def nlq_route(req: NLQReq, scope: Scope = Depends(scope_dep)):
    return tool("run_analytics_query", dict(question=req.question), scope)


@app.get("/api/value-model")
def value_model(request: Request):
    ov = {}
    for k, v in request.query_params.items():
        try:
            ov[k] = json.loads(v)
        except json.JSONDecodeError:
            pass
    return value.value_model(get_data(), ov)


@app.get("/api/audit")
def audit_log(limit: int = 200, scope: Scope = Depends(scope_dep)):
    if scope.persona not in ("engineer", "exec"):
        raise HTTPException(403, "The audit log is visible to engineers and executives only.")
    return audit.read(limit)


# ---------------------------------------------------------------- chat (SSE)
class ChatReq(BaseModel):
    question: str
    store_id: int | None = None
    incident_number: str | None = None
    history: list[dict] = []
    mode: str | None = None


@app.post("/api/chat")
async def chat(req: ChatReq, scope: Scope = Depends(scope_dep)):
    q: queue.Queue = queue.Queue()
    mode = req.mode if req.mode in ("template", "replay", "anthropic", "openai", "azure_openai", "bedrock") else None

    def work():
        try:
            for ev in run_chat(req.question, scope, req.store_id, req.incident_number, req.history, force_mode=mode):
                q.put(ev)
        except Exception as e:  # noqa: BLE001
            q.put({"event": "error", "data": {"error": f"{type(e).__name__}: {e}"}})
        q.put(None)

    threading.Thread(target=work, daemon=True).start()

    async def gen():
        while True:
            ev = await asyncio.get_event_loop().run_in_executor(None, q.get)
            if ev is None:
                break
            yield {"event": ev["event"], "data": json.dumps(ev["data"], default=str)}
    return EventSourceResponse(gen())


@app.get("/api/health")
def healthz():
    return dict(ok=True)


# ---------------------------------------------------------------- static frontend (production build)
DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        p = DIST / path
        if path and p.exists() and p.is_file():
            return FileResponse(p)
        return FileResponse(DIST / "index.html")
