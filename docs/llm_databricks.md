# Using a Databricks-hosted LLM with StoreLens

Databricks Model Serving (Foundation Model APIs, external models, or your own endpoint) exposes an
OpenAI-compatible chat API at `https://<workspace-host>/serving-endpoints`. StoreLens's `databricks` provider uses it.

## 1. Pick an endpoint
Workspace → **Serving**. Pick a chat endpoint that supports **function/tool calling** (StoreLens's agent calls tools).
Copy its exact **name** (e.g. something like `databricks-claude-…` or `databricks-meta-llama-3-3-70b-instruct`; names vary
by workspace/region). Use whichever model your hackathon rules approve.

## 2. Create a token
User Settings → Developer → Access tokens → Generate (or a service principal's OAuth token). Never commit it.

## 3. Configure
```bash
export DATABRICKS_HOST="https://<your-workspace>.cloud.databricks.com"   # or the Azure/GCP workspace URL
export DATABRICKS_TOKEN="dapi..."                                         # or LLM_API_KEY
```
`config/app.yaml`:
```yaml
llm:
  provider: databricks
  model: "<serving-endpoint-name>"
  fallback_chain: [replay, template]
  timeout_s: 20            # serving endpoints can be slower than 12s on a cold start
```

## 4. Verify, then refresh the demo assets
```bash
make llm-check          # one text reply + one tool call; must print a tool call and no errors
make record             # re-record the replay cache from the live model (every demo question × persona)
make eval               # template + live golden-set runs -> eval/out/summary.json
make preflight          # must be GREEN
make demo-script screenshots deck backup-video
```

## Troubleshooting
- **401/403**: token or workspace host wrong, or no `CAN QUERY` permission on the endpoint.
- **404 / "endpoint not found"**: `llm.model` must be the serving endpoint *name*, not the underlying model id.
- **No tool call in `make llm-check`**: the endpoint doesn't support function calling — pick another.
- **Answers lose bullets** (see Trace drawer → "bullets removed"): the model wrote numbers or IDs not present in tool
  results; the validator strips them by design. Tighten `backend/app/agent/prompts/system.md` or try a stronger model.
- If the live call fails or times out, the chat silently falls back to replay, then template. The trace drawer shows
  the mode (`Live` / `Replay` / `Offline`) and the fallback reason.
