# Connecting StoreLens to the hackathon AI gateway

The gateway is OpenAI-compatible. StoreLens's `gateway` provider reads the same three values as the hackathon guide.
Needs the corporate network or VPN.

## 1. Set the values (never commit the key)
```bash
export GATEWAY_BASE_URL="https://sevvy-dev.7-eleven.com/ai-gw-aws/v1"
export GATEWAY_API_KEY="sk-7ai-…"          # from the hackathon team; keep it in a password manager
```
Optional: put these in a `.env` you `source` before `make demo` — `.env` is git-ignored.

## 2. Pick a chat model
```bash
curl "$GATEWAY_BASE_URL/models" -H "Authorization: Bearer $GATEWAY_API_KEY"
export GATEWAY_MODEL="<exact id from the list>"   # a chat model (GPT-5.6 Luna / Sol or Nova 2 Lite); not embeddings
```
StoreLens needs **tool calling**, so prefer the GPT-5.6 models; `make llm-check` tells you if a model can't do it.

## 3. Point StoreLens at it — `config/app.yaml`
```yaml
llm:
  provider: gateway
  model: ""                # "" = use $GATEWAY_MODEL
  fallback_chain: [replay, template]
  timeout_s: 30            # reasoning models can take longer than the default 12s
```
(or one-off: `STORELENS_PROVIDER=gateway make demo`)

## 4. Verify and refresh the demo assets
```bash
make llm-check           # prints a text reply AND a tool call, no errors
make record              # replay cache re-recorded from the live model (all demo questions × 3 personas)
make eval                # template + live golden set -> eval/out/summary.json (deck slide 7)
make preflight           # must be GREEN
make demo-script screenshots deck backup-video
```

## Troubleshooting
| You see | Do |
|---|---|
| 401 `invalid_api_key` | Re-copy the key or ask the hackathon team for a new one |
| 403 `model_not_granted` | Use an id from `GET /models` |
| `CERTIFICATE_VERIFY_FAILED` / `SELF_SIGNED_CERT_IN_CHAIN` | `export GATEWAY_CA_BUNDLE=/path/to/corp-ca-chain.pem` (per the guide's "Certificate errors" page) |
| No tool call in `make llm-check` | Model can't call tools — pick another chat model |
| Answers lose bullets (Trace drawer → bullets removed) | The model wrote numbers/IDs not in tool results; the validator strips them by design. Try the larger model or tighten `backend/app/agent/prompts/system.md` |
| Timeouts | Raise `llm.timeout_s`; failed calls fall back to replay, then template, automatically |

Models that only accept the default temperature are handled automatically (StoreLens retries without it).
