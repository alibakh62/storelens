"""Databricks Model Serving / Foundation Model APIs (OpenAI-compatible chat completions + tool calling).

Env: DATABRICKS_HOST (https://<workspace>.cloud.databricks.com), DATABRICKS_TOKEN (or LLM_API_KEY).
Config: llm.provider = databricks, llm.model = the serving endpoint name.
"""
from .openai import OpenAIProvider


def DatabricksProvider(model=None):  # noqa: N802
    return OpenAIProvider(model, databricks=True)
