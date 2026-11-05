"""make llm-check: one real round-trip to the configured live provider (plain text + one tool call).

Prints exactly what came back, so a failure says why (no tool support, an API error, or a text-only reply).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.agent.providers import chain, live_provider  # noqa: E402
from app.config import app_config  # noqa: E402

name = app_config().get("llm", {}).get("provider")
p = live_provider(name)
if p is None:
    sys.exit(f"Provider '{name}' is not available: check env vars / llm.model / installed SDK. Chain: {chain()}")
print(f"provider: {getattr(p, 'name', name)} · model: {getattr(p, 'model', '?')}")

t = time.time()
print("1) text:", repr(p.complete("Reply with exactly: OK", "ping")[:80]), f"({time.time() - t:.1f}s)")

tool = {"name": "get_store_health", "description": "Returns the health card (open incidents, score) for one store.",
        "parameters": {"type": "object", "properties": {"store_id": {"type": "integer"}}, "required": ["store_id"]}}
msgs = [{"role": "system", "content": "You answer only by calling tools. Always call a tool before answering."},
        {"role": "user", "content": "Call the get_store_health tool for store 34512."}]
t = time.time()
evs = list(p.chat(msgs, [tool], temperature=0, timeout_s=60, stream=False))
dt = time.time() - t
calls = [e for e in evs if e["type"] == "tool_call"]
errs = [e["error"] for e in evs if e["type"] == "error"]
text = "".join(e["text"] for e in evs if e["type"] == "text_delta")

if errs:
    print(f"2) tool call: API ERROR ({dt:.1f}s):", *errs, sep="\n   ")
    sys.exit(1)
if not calls:
    print(f"2) tool call: NONE — the model replied with text instead ({dt:.1f}s):\n   {text[:400]!r}")
    print("   StoreLens's agent needs tool calling. Try another chat model id from GET /models "
          "(e.g. a GPT-5.6 one), then re-run make llm-check.")
    sys.exit(1)
print(f"2) tool call: {calls[0]['name']} {calls[0]['args']} ({dt:.1f}s) — OK")
