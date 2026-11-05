# StoreLens — notes for Claude Code

Spec: `SPEC.md` (single source of truth). Run: `make setup && make data && make demo` (http://localhost:8000).
Dev: `make dev` (FastAPI :8000 + Vite :5173). Tests: `make test`. Eval: `make eval`. Deck: `make deck`.

Conventions
- Never use the system clock for business logic: `get_data().now()` (mock now + elapsed when `clock.mode=running`).
- Never hard-code scenario IDs: resolve via `scenario_manifest.json` (`backend/app/demo_refs.py`).
- Every analytics function returns `citations`; every tool call goes through `agent/tools.execute` → RBAC scope checks
  + `rbac.mask()`. REST routes use the same path, so masking is identical for the UI and the LLM.
- The LLM never computes numbers. Template answers only format numbers that a tool returned; `agent/validate.py`
  removes bullets with unknown IDs or ungrounded numbers. Keep it that way when adding renderers.
- Provider chain: configured → replay → template (`config/app.yaml`). No key → replay/template; demo works offline.
- `est_lost_sales_usd` is validation-only; the app computes impact itself (`analytics/impact.py`).
- Backend layout: `backend/app/{analytics,agent,api}`; frontend: `frontend/src/{components,pages,lib}`.
