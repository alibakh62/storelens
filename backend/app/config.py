"""Configuration loading (config/app.yaml, value_model.yaml, validation.yaml)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


def _load(name: str) -> dict:
    p = CONFIG_DIR / name
    if not p.exists():
        return {}
    with open(p) as f:
        return yaml.safe_load(f) or {}


@lru_cache
def app_config() -> dict:
    cfg = _load("app.yaml")
    # env overrides (handy for demo / tests)
    if os.getenv("STORELENS_PROVIDER"):
        cfg.setdefault("llm", {})["provider"] = os.environ["STORELENS_PROVIDER"]
    if os.getenv("STORELENS_CLOCK"):
        cfg.setdefault("clock", {})["mode"] = os.environ["STORELENS_CLOCK"]
    if os.getenv("STORELENS_MODEL"):
        cfg.setdefault("llm", {})["model"] = os.environ["STORELENS_MODEL"]
    if os.getenv("FICTIONALIZE_VENDORS"):
        cfg["fictionalize_vendors"] = os.environ["FICTIONALIZE_VENDORS"].lower() in ("1", "true", "yes")
    return cfg


def value_model_config() -> dict:
    return _load("value_model.yaml")


def validation_config() -> dict:
    return _load("validation.yaml")


def data_dir() -> Path:
    d = os.getenv("STORELENS_DATA_DIR") or app_config().get("data_dir", "data/raw")
    p = Path(d)
    return p if p.is_absolute() else ROOT / p


def cache_dir() -> Path:
    p = ROOT / "data" / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def audit_dir() -> Path:
    p = Path(os.getenv("STORELENS_AUDIT_DIR", ROOT / "data" / "audit"))
    p.mkdir(parents=True, exist_ok=True)
    return p
