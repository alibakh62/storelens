"""Shared Playwright launcher: uses the bundled browser, or the pre-installed one at /opt/pw-browsers/chromium."""
import os


HELP = "Playwright/Chromium missing. Run:  uv sync --extra dev --extra llm && uv run playwright install chromium"


def launch(p, **kw):
    exe = os.getenv("PW_CHROMIUM") or ("/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None)
    try:
        return p.chromium.launch(**kw)
    except Exception:
        if not exe:
            raise SystemExit(HELP)
        return p.chromium.launch(executable_path=exe, args=["--no-sandbox"], **kw)
