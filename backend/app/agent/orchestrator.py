"""Agent orchestrator (SPEC §8.2): hand-rolled tool loop, fallback chain, validation, SSE events, audit."""
from __future__ import annotations

import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterator

from .. import audit
from ..config import app_config
from ..db import get_data
from ..rbac import Scope, ScopeError, check_store
from . import templates
from .providers import chain, live_provider
from .providers import replay as replay_mod
from .tools import ACTIVITY, TOOL_SPECS, execute
from .validate import validate

PROMPTS = Path(__file__).resolve().parent / "prompts"
PANELS = ["health", "timeline", "similar", "blast", "changes", "impact", "pir", "handoff", "nlq"]
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "≤ 120 chars"},
        "status": {"type": "string", "enum": ["critical", "watch", "healthy", "info"]},
        "bullets": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"}, "citations": {"type": "array", "items": {"type": "string"}},
            "sources": {"type": "array", "items": {"type": "string"}}}, "required": ["text", "citations"]}},
        "sections": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "bullets": {"type": "array", "items": {"type": "object", "properties": {
                "text": {"type": "string"}, "citations": {"type": "array", "items": {"type": "string"}}}}}}}},
        "impact": {"type": "object", "properties": {"usd_so_far": {"type": ["number", "null"]},
                                                    "per_hour_now": {"type": ["number", "null"]},
                                                    "as_of": {"type": "string"}, "restricted": {"type": "boolean"}}},
        "recommended_actions": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"}, "owner": {"type": "string"},
            "citations": {"type": "array", "items": {"type": "string"}}}}},
        "followups": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "panels": {"type": "array", "items": {"type": "string", "enum": PANELS}},
    },
    "required": ["headline", "status", "bullets", "confidence", "panels"],
}
SUBMIT = dict(name="submit_answer", description="Submit the final answer as AnswerSchema JSON. Call exactly once, last.",
              parameters=ANSWER_SCHEMA)


def _ev(event: str, data) -> dict:
    return {"event": event, "data": data}


def _pace() -> float:
    return float(os.getenv("STORELENS_PACING", "1"))


def system_prompt(scope: Scope) -> str:
    role = {"exec": "exec.md", "manager": "manager.md", "engineer": "engineer.md"}[scope.persona]
    restr = []
    if not scope.can_view_network:
        restr.append("network details (IPs, circuit IDs) are masked")
    if not scope.can_view_financials:
        restr.append("$ financials are restricted")
    who = (f"User: {scope.name}, {scope.title} (role {scope.role}); region scope: {scope.region_scope}. "
           f"Restrictions: {', '.join(restr) or 'none'}.")
    return "\n\n".join([(PROMPTS / "system.md").read_text(), (PROMPTS / role).read_text(), who])


def _wrap(name: str, res) -> str:
    s = json.dumps(res, default=str, separators=(",", ":"))
    if len(s) > 16000:
        s = s[:16000] + "…(truncated)"
    return f'<tool_result name="{name}" trusted="false">{s}</tool_result>'


def _normalize(a: dict) -> dict:
    a = dict(a or {})
    a.setdefault("headline", "Here's what I found")
    a["headline"] = str(a["headline"])[:160]
    if a.get("status") not in ("critical", "watch", "healthy", "info"):
        a["status"] = "info"
    for k in ("bullets", "sections", "recommended_actions", "followups", "panels"):
        if not isinstance(a.get(k), list):
            a[k] = []
    a["bullets"] = [x if isinstance(x, dict) else dict(text=str(x), citations=[]) for x in a["bullets"]]
    for x in a["bullets"] + a["recommended_actions"]:
        x.setdefault("citations", [])
    a["panels"] = [p for p in a["panels"] if p in PANELS]
    if a.get("confidence") not in ("high", "medium", "low"):
        a["confidence"] = "medium"
    a.setdefault("impact", None)
    return a


def _stream_answer(a: dict) -> Iterator[dict]:
    """Stream the validated answer with realistic pacing (~40 chars / 20 ms)."""
    pace = _pace()

    def chunks(text):
        for i in range(0, len(text), 40):
            yield text[i:i + 40]
            if pace:
                time.sleep(0.02 * pace)
    for c in chunks(a.get("headline", "")):
        yield _ev("delta", dict(kind="headline", text=c))
    for i, bl in enumerate(a.get("bullets", [])):
        for c in chunks(bl["text"]):
            yield _ev("delta", dict(kind="bullet", i=i, text=c, citations=bl.get("citations", [])))


class Turn:
    def __init__(self, question: str, scope: Scope, store_id=None, incident_number=None, history=None,
                 force_mode: str | None = None, record: bool = False):
        self.q, self.scope = question.strip(), scope
        self.store_id = int(store_id) if store_id not in (None, "") else None
        self.incident_number = incident_number or None
        self.history = (history or [])[-6:]
        self.force_mode, self.record = force_mode, record
        self.tool_trace: list[dict] = []
        self.tool_results: list = []
        self.results_by_name: dict = {}
        self.calls: list[dict] = []
        self.usage = {"in": 0, "out": 0}
        self.id = uuid.uuid4().hex[:12]

    # ------------------------------------------------------------ tools
    def run_tool(self, name: str, args: dict) -> Iterator[dict]:
        yield _ev("tool_start", dict(name=name, label=ACTIVITY.get(name, name), args=args))
        res, tr = execute(name, args, self.scope)
        self.tool_trace.append(tr)
        self.tool_results.append(res)
        self.results_by_name[name] = res
        self.calls.append(dict(name=name, args=args))
        yield _ev("tool_end", tr)
        self._last = res

    def run_tools_parallel(self, calls: list[tuple[str, dict, str]]) -> Iterator[dict]:
        for name, args, _ in calls:
            yield _ev("tool_start", dict(name=name, label=ACTIVITY.get(name, name), args=args))
        with ThreadPoolExecutor(max_workers=4) as ex:
            outs = list(ex.map(lambda c: execute(c[0], c[1], self.scope), calls))
        self._par = []
        for (name, args, cid), (res, tr) in zip(calls, outs):
            self.tool_trace.append(tr)
            self.tool_results.append(res)
            self.results_by_name[name] = res
            self.calls.append(dict(name=name, args=args))
            self._par.append((cid, name, res))
            yield _ev("tool_end", tr)

    # ------------------------------------------------------------ main
    def run(self) -> Iterator[dict]:
        t0 = time.perf_counter()
        data = get_data()
        mode_used, answer, rep, err = None, None, None, None
        try:
            if self.store_id is not None:
                check_store(self.scope, self.store_id)
        except ScopeError as e:
            answer = _normalize(dict(headline=str(e), status="info", bullets=[], confidence="high", panels=[],
                                     followups=[]))
            rep = dict(citations_checked=0, removed_bullets=[], ungrounded_numbers=[], rbac_redactions=0,
                       removed_count=0, ungrounded_count=0)
            mode_used = "scope"
            yield from _stream_answer(answer)
            yield _ev("answer", dict(answer=answer, validation=rep, mode=mode_used))
            yield from self._finish(t0, mode_used, answer, rep, None)
            return
        self.ctx = templates.resolve_context(self.q, self.store_id, self.incident_number, self.scope)
        if self.store_id is None and self.ctx.get("store_id") is not None:
            try:
                check_store(self.scope, self.ctx["store_id"])
                self.store_id = self.ctx["store_id"]
            except ScopeError as e:
                answer = _normalize(dict(headline=str(e), status="info", bullets=[], confidence="high", panels=[]))
                rep = dict(citations_checked=0, removed_bullets=[], ungrounded_numbers=[], rbac_redactions=0,
                           removed_count=0, ungrounded_count=0)
                yield from _stream_answer(answer)
                yield _ev("answer", dict(answer=answer, validation=rep, mode="scope"))
                yield from self._finish(t0, "scope", answer, rep, None)
                return
        # store context: inject get_store_health as the first tool result
        if self.store_id is not None:
            yield from self.run_tool("get_store_health", dict(store_id=self.store_id))
        digest = ",".join(sorted(o["number"] for o in (self.results_by_name.get("get_store_health") or {})
                                 .get("open_incidents", []) or []))
        rkey = replay_mod.key(self.scope.persona, self.q, self.store_id, self.incident_number, digest)
        modes = [self.force_mode] if self.force_mode else chain()
        for mode in modes:
            try:
                if mode == "template":
                    answer, rep = yield from self._template()
                elif mode == "replay":
                    rec = replay_mod.load(rkey)
                    if rec is None:
                        continue
                    answer, rep = yield from self._replay(rec)
                else:
                    prov = live_provider(mode)
                    if prov is None:
                        continue
                    try:
                        answer, rep = yield from self._live(prov)
                    except _Fallback:
                        raise
                    except Exception as e:  # noqa: BLE001
                        raise _Fallback(f"{type(e).__name__}: {e}")
                mode_used = mode
                break
            except _Fallback as e:
                err = f"{mode}: {e}"
                continue
        if answer is None:
            answer, rep = yield from self._template()
            mode_used = "template"
        answer = _with_standard_followups(answer, self.scope.persona, self.q)
        yield from _stream_answer(answer)
        yield _ev("answer", dict(answer=answer, validation=rep, mode=mode_used))
        if (self.record or (mode_used not in ("replay", "template", "scope")
                            and app_config().get("llm", {}).get("record_on_live", True))) and mode_used != "replay":
            replay_mod.save(rkey, dict(question=self.q, persona=self.scope.persona, store_id=self.store_id,
                                       incident_number=self.incident_number, recorded_from=mode_used,
                                       calls=[c for c in self.calls if c["name"] != "get_store_health"],
                                       answer=self.raw_answer, tool_results=self.tool_results,
                                       recorded_at_mock=data.now().isoformat()))
        yield from self._finish(t0, mode_used, answer, rep, err)

    def _finish(self, t0, mode, answer, rep, err):
        lat = round((time.perf_counter() - t0) * 1000, 1)
        trace = dict(turn_id=self.id, provider_mode=mode, tools=self.tool_trace, validation=rep, latency_ms=lat,
                     usage=self.usage, fallback_reason=err, masked_fields_total=sum(t["masked_fields"] for t in self.tool_trace))
        yield _ev("trace", trace)
        audit.write(dict(persona=self.scope.user_id, persona_name=self.scope.name, role=self.scope.role,
                         question=self.q, store_id=self.store_id, incident_number=self.incident_number,
                         tools=[dict(name=t["name"], args=t["args"], ms=t["ms"], rows=t["rows"],
                                     masked_fields=t["masked_fields"]) for t in self.tool_trace],
                         provider_mode=mode, validation={k: (len(v) if isinstance(v, list) else v)
                                                         for k, v in (rep or {}).items()},
                         latency_ms=lat, rbac_p1=bool(rep and rep.get("rbac_redactions"))))

    # ------------------------------------------------------------ template mode
    def _template(self):
        intent = templates.intent_of(self.q, self.ctx.get("store_id") is not None)
        self.intent = intent
        calls = templates.plan(intent, self.ctx, self.q, self.scope.persona, self.scope)
        # run this plan's own calls (a failed live attempt may have left results with other arguments behind)
        todo = [(n, a, f"t{i}") for i, (n, a) in enumerate(calls) if n != "get_store_health"]
        mine = {"get_store_health": self.results_by_name["get_store_health"]} if "get_store_health" in self.results_by_name else {}
        if todo:
            yield from self.run_tools_parallel(todo)
            mine.update({name: res for _, name, res in self._par})
        raw = templates.render(intent, self.ctx, mine, self.scope.persona, self.scope, self.q)
        raw = _normalize(raw)
        self.raw_answer = raw
        ans, rep = validate(raw, self.tool_results, self.scope, tools_called=set(self.results_by_name))
        rep["intent"] = intent
        return ans, rep

    # ------------------------------------------------------------ replay
    def _replay(self, rec: dict):
        calls = [(c["name"], c["args"], f"r{i}") for i, c in enumerate(rec.get("calls", []))]
        if calls:
            yield from self.run_tools_parallel(calls)
        raw = _normalize(rec["answer"])
        self.raw_answer = raw
        ans, rep = validate(raw, self.tool_results, self.scope, extra_results=rec.get("tool_results", []),
                            tools_called=set(self.results_by_name))
        rep["recorded_from"] = rec.get("recorded_from")
        return ans, rep

    # ------------------------------------------------------------ live LLM loop
    def _live(self, prov):
        cfg = app_config().get("llm", {})
        msgs = [dict(role="system", content=system_prompt(self.scope))]
        for h in self.history:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                msgs.append(dict(role=h["role"], content=str(h["content"])[:2000]))
        ctx_txt = []
        if self.store_id is not None:
            ctx_txt.append(f"Selected store: {self.store_id}.")
        if self.incident_number:
            ctx_txt.append(f"Selected incident: {self.incident_number}.")
        msgs.append(dict(role="user", content=" ".join(ctx_txt + [self.q])))
        if "get_store_health" in self.results_by_name:
            msgs.append(dict(role="assistant", content="", tool_calls=[dict(id="ctx0", name="get_store_health",
                                                                             args=dict(store_id=self.store_id))]))
            msgs.append(dict(role="tool", tool_call_id="ctx0", name="get_store_health",
                             content=_wrap("get_store_health", self.results_by_name["get_store_health"])))
        tools = TOOL_SPECS + [SUBMIT]
        final = None
        for _round in range(int(cfg.get("max_tool_rounds", 6)) + 1):
            text, tcs, error = "", [], None
            for ev in prov.chat(msgs, tools, temperature=cfg.get("temperature", 0), timeout_s=cfg.get("timeout_s", 12),
                                stream=False):
                if ev["type"] == "text_delta":
                    text += ev["text"]
                elif ev["type"] == "tool_call":
                    tcs.append(ev)
                elif ev["type"] == "error":
                    error = ev["error"]
                elif ev["type"] == "done":
                    self.usage["in"] += ev["usage"].get("in", 0)
                    self.usage["out"] += ev["usage"].get("out", 0)
            if error:
                raise _Fallback(error)
            sub = [t for t in tcs if t["name"] == "submit_answer"]
            if sub:
                final = sub[0]["args"]
                break
            if not tcs:
                try:
                    final = json.loads(text[text.index("{"):text.rindex("}") + 1])
                    break
                except (ValueError, json.JSONDecodeError):
                    if _round >= 1:
                        raise _Fallback("model did not return AnswerSchema JSON")
                    msgs.append(dict(role="assistant", content=text))
                    msgs.append(dict(role="user", content="Return the answer by calling submit_answer with AnswerSchema JSON."))
                    continue
            msgs.append(dict(role="assistant", content=text, tool_calls=[dict(id=t["id"], name=t["name"], args=t["args"])
                                                                         for t in tcs]))
            todo = [(t["name"], t["args"], t["id"]) for t in tcs]
            yield from self.run_tools_parallel(todo)
            for cid, name, res in self._par:
                msgs.append(dict(role="tool", tool_call_id=cid, name=name, content=_wrap(name, res)))
        if final is None:
            raise _Fallback("max tool rounds exceeded")
        raw = _normalize(final)
        self.raw_answer = raw
        ans, rep = validate(raw, self.tool_results, self.scope, tools_called=set(self.results_by_name))
        return ans, rep


def _with_standard_followups(a: dict, persona: str, question: str) -> dict:
    """The persona's standard follow-up chips always come first (the demo script relies on them, whatever the model
    or the replay cache suggested); the model's own follow-ups fill the remaining slots."""
    asked = question.strip().lower()
    std = [f for f in templates.FOLLOWUPS.get(persona, []) if f.lower() != asked][:3]
    rest = [f for f in a.get("followups", []) if f.lower() not in {x.lower() for x in std} and f.lower() != asked]
    return {**a, "followups": (std + rest)[:4]}


class _Fallback(Exception):
    pass


def run_chat(question, scope, store_id=None, incident_number=None, history=None, force_mode=None, record=False):
    return Turn(question, scope, store_id, incident_number, history, force_mode, record).run()


def ask(question, scope, **kw) -> dict:
    """Synchronous helper (tests / eval / recording): returns {answer, validation, mode, trace, tools}."""
    os.environ.setdefault("STORELENS_PACING", "1")
    out = dict(events=[])
    for ev in run_chat(question, scope, **kw):
        if ev["event"] == "answer":
            out.update(ev["data"])
        elif ev["event"] == "trace":
            out["trace"] = ev["data"]
        out["events"].append(ev["event"])
    return out
