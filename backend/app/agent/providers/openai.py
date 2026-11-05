"""OpenAI / Azure OpenAI chat-completions adapter (function calling)."""
from __future__ import annotations

import json
import os

from .base import ProviderUnavailable, api_key


def _convert(messages):
    out = []
    for m in messages:
        if m["role"] in ("system", "user"):
            out.append({"role": m["role"], "content": m["content"]})
        elif m["role"] == "assistant":
            d = {"role": "assistant", "content": m.get("content") or None}
            if m.get("tool_calls"):
                d["tool_calls"] = [{"id": t["id"], "type": "function",
                                    "function": {"name": t["name"], "arguments": json.dumps(t["args"])}}
                                   for t in m["tool_calls"]]
            out.append(d)
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
    return out


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str | None = None, azure: bool = False):
        try:
            import openai
        except ImportError as e:
            raise ProviderUnavailable("openai SDK not installed") from e
        if azure:
            ep = os.getenv("AZURE_OPENAI_ENDPOINT")
            key = api_key("AZURE_OPENAI_API_KEY")
            if not (ep and key):
                raise ProviderUnavailable("AZURE_OPENAI_ENDPOINT / key missing")
            self.client = openai.AzureOpenAI(azure_endpoint=ep, api_key=key,
                                             api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"))
            self.name = "azure_openai"
        else:
            key = api_key("OPENAI_API_KEY")
            if not key:
                raise ProviderUnavailable("no API key (LLM_API_KEY / OPENAI_API_KEY)")
            self.client = openai.OpenAI(api_key=key)
        self.model = model or os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

    def chat(self, messages, tools, *, temperature=0, timeout_s=12, stream=False):
        tl = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                "parameters": t["parameters"]}} for t in tools]
        try:
            resp = self.client.chat.completions.create(model=self.model, messages=_convert(messages), tools=tl,
                                                       temperature=temperature, timeout=timeout_s)
        except Exception as e:  # noqa: BLE001
            yield {"type": "error", "error": f"{type(e).__name__}: {e}"}
            return
        msg = resp.choices[0].message
        if msg.content:
            yield {"type": "text_delta", "text": msg.content}
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            yield {"type": "tool_call", "id": tc.id, "name": tc.function.name, "args": args}
        u = resp.usage
        yield {"type": "done", "usage": {"in": getattr(u, "prompt_tokens", 0), "out": getattr(u, "completion_tokens", 0)}}

    def complete(self, system: str, user: str, timeout_s: float = 12) -> str:
        resp = self.client.chat.completions.create(model=self.model, temperature=0, timeout=timeout_s,
                                                   messages=[{"role": "system", "content": system},
                                                             {"role": "user", "content": user}])
        return resp.choices[0].message.content or ""
