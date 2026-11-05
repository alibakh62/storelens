"""Provider abstraction (SPEC §8.1).

Messages use a neutral format:
  {"role": "system"|"user"|"assistant"|"tool", "content": str,
   "tool_calls": [{"id", "name", "args"}] (assistant), "tool_call_id"/"name" (tool)}
Events yielded by chat(): {"type": "text_delta", "text"} | {"type": "tool_call", "id", "name", "args"} |
                          {"type": "done", "usage": {...}} | {"type": "error", "error"}
"""
from __future__ import annotations

import os
from typing import Iterator, Protocol

Msg = dict
ToolSpec = dict
Event = dict


class ProviderUnavailable(Exception):
    pass


class LLMProvider(Protocol):
    name: str

    def chat(self, messages: list[Msg], tools: list[ToolSpec], *, temperature: float, timeout_s: float,
             stream: bool) -> Iterator[Event]: ...


def api_key(*names: str) -> str | None:
    for n in ("LLM_API_KEY",) + names:
        if os.getenv(n):
            return os.environ[n]
    return None
