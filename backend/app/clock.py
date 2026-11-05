"""The mock world's clock. Never use the system clock for business logic.

frozen  -> mock now (scenario_manifest.generated_at_mock_now_utc)
running -> mock now + wall time elapsed since app start (so tickers tick)
"""
from __future__ import annotations

import time

import pandas as pd

from .config import app_config


class Clock:
    def __init__(self, mock_now: pd.Timestamp, mode: str | None = None):
        self.mock_now = pd.Timestamp(mock_now)
        self.mode = mode or app_config().get("clock", {}).get("mode", "running")
        self._t0 = time.monotonic()

    def now(self) -> pd.Timestamp:
        if self.mode == "frozen":
            return self.mock_now
        return self.mock_now + pd.Timedelta(seconds=time.monotonic() - self._t0)

    def reset(self):
        self._t0 = time.monotonic()

    def set_mode(self, mode: str):
        self.mode = mode
        self.reset()


DISPLAY_OFFSET_H = -6  # America/Chicago standard time (no DST, consistent with the generator)


def to_local(ts, offset_h: int) -> pd.Timestamp | None:
    if ts is None or pd.isna(ts):
        return None
    return pd.Timestamp(ts) + pd.Timedelta(hours=int(offset_h))


TZ_LABEL = {-5: "ET", -6: "CT", -7: "MT", -8: "PT"}


def fmt_local(ts, offset_h: int = DISPLAY_OFFSET_H, with_date: bool = False) -> str:
    t = to_local(ts, offset_h)
    if t is None:
        return "—"
    s = t.strftime("%a %b %-d, %-I:%M %p") if with_date else t.strftime("%-I:%M %p")
    return f"{s} {TZ_LABEL.get(int(offset_h), f'UTC{int(offset_h):+d}')}"
