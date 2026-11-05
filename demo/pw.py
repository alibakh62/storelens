"""Shared Playwright launcher: uses the bundled browser, or the pre-installed one at /opt/pw-browsers/chromium."""
import os


def launch(p, **kw):
    exe = os.getenv("PW_CHROMIUM") or ("/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None)
    try:
        return p.chromium.launch(**kw)
    except Exception:
        if not exe:
            raise
        return p.chromium.launch(executable_path=exe, args=["--no-sandbox"], **kw)
