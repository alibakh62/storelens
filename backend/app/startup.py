"""Warm-up at startup: load data, precompute impact, build/load the TF-IDF index and caches."""
import time

from .analytics import chronic, impact, kb, similar
from .analytics.changes import _ensure_group
from .db import get_data


def warm() -> dict:
    t = time.time()
    data = get_data()
    impact.precompute(data)
    _ensure_group(data)
    similar.build_index(data)
    chronic.get_chronic(data)
    kb.search_kb(data, "warm")
    from .analytics.value import mock_metrics
    mock_metrics(data)
    return dict(load_s=data.load_seconds, warm_s=round(time.time() - t, 2))
