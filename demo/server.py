"""Start a throwaway StoreLens server for scripted runs (e2e, screenshots, video)."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def server(port: int = 8765, provider: str | None = None, clock: str = "running", pacing: str = "1"):
    base = f"http://127.0.0.1:{port}"
    try:
        urllib.request.urlopen(base + "/api/health", timeout=1)
        yield base  # already running
        return
    except Exception:
        pass
    env = dict(os.environ, STORELENS_CLOCK=clock, STORELENS_PACING=pacing)
    if provider:
        env["STORELENS_PROVIDER"] = provider
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
                            cwd=ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(base + "/api/meta", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("server did not start")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
