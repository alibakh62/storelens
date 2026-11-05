"""Provider factory and fallback chain: configured provider → replay → template."""
from __future__ import annotations

from ...config import app_config
from .base import ProviderUnavailable

_LIVE: dict = {}


def live_provider(name: str | None = None):
    cfg = app_config().get("llm", {})
    name = name or cfg.get("provider", "replay")
    if name in ("replay", "template"):
        return None
    if name in _LIVE:
        return _LIVE[name]
    model = cfg.get("model") or None
    try:
        if name == "anthropic":
            from .anthropic import AnthropicProvider
            p = AnthropicProvider(model)
        elif name == "openai":
            from .openai import OpenAIProvider
            p = OpenAIProvider(model)
        elif name == "azure_openai":
            from .azure_openai import AzureOpenAIProvider
            p = AzureOpenAIProvider(model)
        elif name == "gateway":
            from .gateway import GatewayProvider
            p = GatewayProvider(model)
        elif name == "databricks":
            from .databricks import DatabricksProvider
            p = DatabricksProvider(model)
        elif name == "bedrock":
            from .bedrock import BedrockProvider
            p = BedrockProvider(model)
        else:
            return None
    except ProviderUnavailable:
        p = None
    except Exception:  # noqa: BLE001
        p = None
    _LIVE[name] = p
    return p


def chain() -> list[str]:
    cfg = app_config().get("llm", {})
    first = cfg.get("provider", "replay")
    out = [first] + [x for x in cfg.get("fallback_chain", ["replay", "template"]) if x != first]
    if "template" not in out:
        out.append("template")
    return out


def nlq_sql_fn(scope):
    """A callable(question, error) -> SQL using the live provider, or None for template NLQ."""
    p = None
    for name in chain():
        if name in ("replay", "template"):
            break
        p = live_provider(name)
        if p:
            break
    if p is None:
        return None
    from pathlib import Path
    prompt = (Path(__file__).resolve().parents[1] / "prompts" / "nlq.md").read_text()
    if not scope.can_view_network:
        prompt = "\n".join(ln for ln in prompt.splitlines() if "sensitive" not in ln)

    def fn(question, error):
        user = question if not error else f"{question}\n\nThe previous SQL was rejected: {error}. Fix it."
        return p.complete(prompt, user, timeout_s=app_config().get("llm", {}).get("timeout_s", 12))
    return fn
