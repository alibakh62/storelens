import pytest

from app.agent.tools import execute
from app.analytics import nlq
from app.rbac import IPV4, CIRCUIT, NETWORK_KEYS, RESTRICTED, ScopeError, check_store, mask


def _walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def test_exec_masks_network(personas, man):
    ex = personas["exec"]
    res, tr = execute("get_store_profile", {"store_id": man["demo_store_id"]}, ex)
    for k, v in _walk(res):
        if k in NETWORK_KEYS:
            assert v == RESTRICTED, k
    import json
    txt = json.dumps(res)
    assert not IPV4.search(txt) and not CIRCUIT.search(txt)
    assert tr["masked_fields"] > 0


def test_engineer_sees_network_but_not_money(personas, man):
    en = personas["engineer"]
    res, _ = execute("get_store_profile", {"store_id": man["demo_store_id"]}, en)
    assert IPV4.search(res["network"]["router_lan_ip"])
    h, _ = execute("get_store_health", {"store_id": man["demo_store_id"]}, en)
    assert h["open_incidents"][0]["est_impact_usd"] is None and h["open_incidents"][0]["restricted"] is True


def test_free_text_redaction(personas):
    m = mask({"text": "router 10.20.0.1 circuit SPEC-1709-518227"}, personas["exec"])
    assert "10.20.0.1" not in m["text"] and "SPEC-1709-518227" not in m["text"]


def test_region_scope(data, personas):
    mgr = personas["manager"]
    ca = int(data.stores[data.stores["state"] == "CA"]["store_id"].iloc[0])
    with pytest.raises(ScopeError):
        check_store(mgr, ca)
    res, _ = execute("get_store_health", {"store_id": ca}, mgr)
    assert res.get("restricted") and "outside" in res["error"]
    lst, _ = execute("list_incidents", {"limit": 50}, mgr)
    assert all(i["region"] == mgr.region_scope for i in lst["items"])


@pytest.mark.parametrize("sql", ["DELETE FROM incident", "SELECT * FROM read_csv('x')", "SELECT * FROM store_network",
                                 "SELECT * FROM v_store_network_sensitive", "COPY v_incident TO 'x.csv'",
                                 "SELECT 1; DROP TABLE stores", "ATTACH 'x.db'", "PRAGMA version"])
def test_nlq_rejects(personas, sql):
    with pytest.raises(nlq.SQLRejected):
        nlq.validate_sql(sql, personas["exec"])


def test_nlq_engineer_can_use_sensitive_view(personas):
    assert "LIMIT 500" in nlq.validate_sql("SELECT * FROM v_store_network_sensitive", personas["engineer"])


def test_nlq_scoped_runs(data, personas):
    r = nlq.answer(data, personas["manager"], "Stores with 3 or more open incidents")
    assert r["ok"]
    sql = "SELECT DISTINCT region FROM v_incident WHERE region IS NOT NULL"
    df = nlq.run_sql(data, personas["manager"], nlq.validate_sql(sql, personas["manager"]))
    assert set(df["region"]) == {"Texas"}
