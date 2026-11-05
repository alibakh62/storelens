"""Anthropic Messages API adapter (tool use)."""
from __future__ import annotations

import json

from .base import ProviderUnavailable, api_key


def _convert(messages):
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    out = []
    for m in messages:
        if m["role"] == "system":
            continue
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            blocks = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls", []):
                blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["args"]})
            out.append({"role": "assistant", "content": blocks})
        elif m["role"] == "tool":
            blk = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(blk)
            else:
                out.append({"role": "user", "content": [blk]})
    return system, out


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str | None = None, bedrock: bool = False):
        try:
            import anthropic
        except ImportError as e:
            raise ProviderUnavailable("anthropic SDK not installed") from e
        self.bedrock = bedrock
        if bedrock:
            self.client = anthropic.AnthropicBedrock()
            self.name = "bedrock"
        else:
            key = api_key("ANTHROPIC_API_KEY")
            if not key:
                raise ProviderUnavailable("no API key (LLM_API_KEY / ANTHROPIC_API_KEY)")
            self.client = anthropic.Anthropic(api_key=key)
        self.model = model or "claude-opus-5-5"

    def chat(self, messages, tools, *, temperature=0, timeout_s=12, stream=False):
        system, msgs = _convert(messages)
        tl = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
        try:
            resp = self.client.messages.create(model=self.model, system=system, messages=msgs, tools=tl,
                                               max_tokens=2500, temperature=temperature, timeout=timeout_s)
        except Exception as e:  # noqa: BLE001
            yield {"type": "error", "error": f"{type(e).__name__}: {e}"}
            return
        for blk in resp.content:
            if blk.type == "text" and blk.text:
                yield {"type": "text_delta", "text": blk.text}
            elif blk.type == "tool_use":
                yield {"type": "tool_call", "id": blk.id, "name": blk.name, "args": blk.input}
        u = getattr(resp, "usage", None)
        yield {"type": "done", "usage": {"in": getattr(u, "input_tokens", 0), "out": getattr(u, "output_tokens", 0)}}

    def complete(self, system: str, user: str, timeout_s: float = 12) -> str:
        resp = self.client.messages.create(model=self.model, system=system, max_tokens=1500, temperature=0,
                                           messages=[{"role": "user", "content": user}], timeout=timeout_s)
        return "".join(b.text for b in resp.content if b.type == "text")


def as_json(x) -> str:
    return json.dumps(x, default=str)
