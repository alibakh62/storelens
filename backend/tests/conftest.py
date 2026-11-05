import os
import tempfile

os.environ["STORELENS_PACING"] = "0"
os.environ["STORELENS_CLOCK"] = "frozen"
os.environ.setdefault("STORELENS_AUDIT_DIR", tempfile.mkdtemp(prefix="sl_audit_"))

import pytest  # noqa: E402

from app.db import get_data  # noqa: E402
from app.rbac import default_personas, scope_for  # noqa: E402
from app.startup import warm  # noqa: E402


@pytest.fixture(scope="session")
def data():
    warm()
    d = get_data()
    d.clock.set_mode("frozen")
    return d


@pytest.fixture(scope="session")
def man(data):
    return data.manifest


@pytest.fixture(scope="session")
def scen(man):
    return {s["id"]: s for s in man["scenarios"]}


@pytest.fixture(scope="session")
def personas(data):
    return {k: scope_for(v) for k, v in default_personas().items()}
