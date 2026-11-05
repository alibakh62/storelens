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

    def __init__(self, model: str | None = None, azure: bool = False, databricks: bool = False, gateway: bool = False):
        try:
            import openai
        except ImportError as e:
            raise ProviderUnavailable("openai SDK not installed") from e
        if gateway:
            # Corporate AI gateway (OpenAI-compatible): GATEWAY_BASE_URL, GATEWAY_API_KEY, GATEWAY_MODEL
            base = os.getenv("GATEWAY_BASE_URL")
            key = os.getenv("GATEWAY_API_KEY") or api_key()
            model = model or os.getenv("GATEWAY_MODEL")
            if not (base and key and model):
                raise ProviderUnavailable("GATEWAY_BASE_URL / GATEWAY_API_KEY / GATEWAY_MODEL (or llm.model) missing")
            kw = {}
            ca = os.getenv("GATEWAY_CA_BUNDLE")  # internal CA chain, for CERTIFICATE_VERIFY_FAILED errors
            if ca:
                import ssl

                import httpx
                kw["http_client"] = httpx.Client(verify=ssl.create_default_context(cafile=ca))
            self.client = openai.OpenAI(base_url=base.rstrip("/"), api_key=key, **kw)
            self.name = "gateway"
        elif databricks:
            host = (os.getenv("DATABRICKS_HOST") or "").rstrip("/")
            key = os.getenv("DATABRICKS_TOKEN") or api_key()
            if not (host and key):
                raise ProviderUnavailable("DATABRICKS_HOST / DATABRICKS_TOKEN missing")
            if not host.startswith("http"):
                host = "https://" + host
            # Databricks Foundation Model APIs / Model Serving are OpenAI-compatible at <workspace>/serving-endpoints
            self.client = openai.OpenAI(base_url=f"{host}/serving-endpoints", api_key=key)
            self.name = "databricks"
            if not model:
                raise ProviderUnavailable("set llm.model to the Databricks serving endpoint name")
        elif azure:
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

    _no_temperature = False
    _reasoning: str | None = None   # e.g. "none"; from llm.reasoning_effort, or learned from a gateway error

    def _create(self, **kw):
        """Adapt to model quirks, retrying once per quirk and remembering it:
        - models that only accept the default temperature;
        - reasoning models that only allow function tools with reasoning_effort='none'."""
        if self._reasoning is None:
            from ...config import app_config
            self._reasoning = app_config().get("llm", {}).get("reasoning_effort") or ""
        for _ in range(3):
            if self._no_temperature:
                kw.pop("temperature", None)
            if self._reasoning:
                kw["extra_body"] = {**kw.get("extra_body", {}), "reasoning_effort": self._reasoning}
            try:
                return self.client.chat.completions.create(**kw)
            except Exception as e:  # noqa: BLE001
                msg = str(e).lower()
                if "reasoning_effort" in msg and self._reasoning != "none" and "/v1/responses" not in msg \
                        and "does not support 'none'" not in msg:
                    self._reasoning = "none"
                    continue
                if "temperature" in msg and "temperature" in kw and not self._no_temperature:
                    self._no_temperature = True
                    continue
                raise
        return self.client.chat.completions.create(**kw)

    _api: str | None = None   # "chat" | "responses"; from llm.api, or switched to "responses" by gateway errors

    def chat(self, messages, tools, *, temperature=0, timeout_s=12, stream=False):
        if self._api is None:
            from ...config import app_config
            self._api = app_config().get("llm", {}).get("api") or "chat"
        if self._api == "responses":
            yield from self._chat_responses(messages, tools, temperature, timeout_s)
            return
        tl = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                "parameters": t["parameters"]}} for t in tools]
        try:
            resp = self._create(model=self.model, messages=_convert(messages), tools=tl, tool_choice="auto",
                                temperature=temperature, timeout=timeout_s)
        except Exception as e:  # noqa: BLE001
            m = str(e).lower()
            # e.g. "Function tools with reasoning_effort are not supported ... use /v1/responses", or the model
            # refusing reasoning_effort='none': this model can only use tools through the Responses API.
            if "/v1/responses" in m or ("reasoning_effort" in m and "'none'" in m):
                self._api = "responses"
                self._reasoning = ""
                yield from self._chat_responses(messages, tools, temperature, timeout_s)
                return
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

    # ------------------------------------------------------------ Responses API (/v1/responses)
    def _chat_responses(self, messages, tools, temperature, timeout_s):
        instructions = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        items = []
        for m in messages:
            if m["role"] in ("user",):
                items.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                if m.get("content"):
                    items.append({"role": "assistant", "content": m["content"]})
                for tc in m.get("tool_calls", []):
                    items.append({"type": "function_call", "call_id": tc["id"], "name": tc["name"],
                                  "arguments": json.dumps(tc["args"])})
            elif m["role"] == "tool":
                items.append({"type": "function_call_output", "call_id": m["tool_call_id"], "output": m["content"]})
        tl = [{"type": "function", "name": t["name"], "description": t["description"], "parameters": t["parameters"]}
              for t in tools]
        kw = dict(model=self.model, instructions=instructions or None, input=items, tools=tl, tool_choice="auto",
                  timeout=timeout_s, store=False)
        from ...config import app_config
        eff = app_config().get("llm", {}).get("reasoning_effort")
        if eff and eff != "none":
            kw["reasoning"] = {"effort": eff}
        if not self._no_temperature:
            kw["temperature"] = temperature
        resp = None
        for _ in range(3):
            try:
                resp = self.client.responses.create(**kw)
                break
            except Exception as e:  # noqa: BLE001
                m = str(e).lower()
                if "temperature" in m and "temperature" in kw:
                    self._no_temperature = True
                    kw.pop("temperature")
                    continue
                if "store" in m and "store" in kw:
                    kw.pop("store")
                    continue
                yield {"type": "error", "error": f"{type(e).__name__} (responses API): {e}"}
                return
        for it in resp.output or []:
            t = getattr(it, "type", None)
            if t == "message":
                txt = "".join(getattr(c, "text", "") or "" for c in (it.content or []))
                if txt:
                    yield {"type": "text_delta", "text": txt}
            elif t == "function_call":
                try:
                    args = json.loads(it.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                yield {"type": "tool_call", "id": it.call_id, "name": it.name, "args": args}
        u = getattr(resp, "usage", None)
        yield {"type": "done", "usage": {"in": getattr(u, "input_tokens", 0) or 0, "out": getattr(u, "output_tokens", 0) or 0}}

    def complete(self, system: str, user: str, timeout_s: float = 12) -> str:
        resp = self._create(model=self.model, temperature=0, timeout=timeout_s,
                                                   messages=[{"role": "system", "content": system},
                                                             {"role": "user", "content": user}])
        return resp.choices[0].message.content or ""
