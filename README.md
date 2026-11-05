# StoreLens — *Type a store number. Get the whole story.*

```bash
make setup     # uv + npm deps
make data      # mock data (seed 42, 1,500 stores) -> data/raw
make demo      # production build on http://localhost:8000 (replay → template fallback, works offline)
make test      # 60+ tests incl. §7 acceptance, RBAC, injection
make deck      # deck/out/StoreLens.pptx (+ PDF)
```

AI layer over ServiceNow-style data: store health, stitched parent/child timelines, blast radius with likely common
cause, change correlation with rollout evidence, "which fix holds", $ impact, role-aware cited answers.
**AI decides what to look at and how to say it; deterministic code computes every number.**

- Live LLM: set `llm.provider` in `config/app.yaml` (`gateway` for the hackathon AI gateway — see `docs/llm_gateway.md`; also `anthropic` | `openai` | `azure_openai` | `bedrock` | `databricks`) and
  `LLM_API_KEY` (or the provider's own env var). Then `make record` to refresh the replay cache.
- Personas: switch in the top-right; RBAC/masking is enforced in the data layer (`backend/app/rbac.py`).
- Demo script: `demo/demo_script.md` (`make demo-script` refreshes the numbers). Q&A prep: `demo/qa_prep.md`.
- Before the demo: fill `config/validation.yaml` (time trial + quotes) and `config/value_model.yaml` placeholders.
