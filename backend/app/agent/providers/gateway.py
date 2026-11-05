"""Hackathon AI gateway (OpenAI-compatible). Env: GATEWAY_BASE_URL, GATEWAY_API_KEY, GATEWAY_MODEL,
optional GATEWAY_CA_BUNDLE. Config: llm.provider = gateway (llm.model overrides GATEWAY_MODEL if set)."""
from .openai import OpenAIProvider


def GatewayProvider(model=None):  # noqa: N802
    return OpenAIProvider(model, gateway=True)
