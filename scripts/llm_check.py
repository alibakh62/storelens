"""make llm-check: one real round-trip to the configured live provider (plain text + one tool call)."""
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
t = time.time()
print("text:", p.complete("Reply with exactly: OK", "ping")[:80], f"({time.time() - t:.1f}s)")
tool = {"name": "get_store_health", "description": "Store health card for a store.",
        "parameters": {"type": "object", "properties": {"store_id": {"type": "integer"}}, "required": ["store_id"]}}
evs = list(p.chat([{"role": "system", "content": "Use tools."}, {"role": "user", "content": "How is store 34512?"}],
                  [tool], temperature=0, timeout_s=30, stream=False))
calls = [e for e in evs if e["type"] == "tool_call"]
errs = [e for e in evs if e["type"] == "error"]
print("tool call:", calls[0]["name"], calls[0]["args"] if calls else "", "| errors:", errs or "none")
sys.exit(0 if calls and not errs else 1)
