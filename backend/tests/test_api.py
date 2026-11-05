from fastapi.testclient import TestClient

from app.main import app
from app.rbac import default_personas


def test_api_smoke(data, man):
    c = TestClient(app)
    P = default_personas()
    h = {"X-Persona-Id": P["engineer"]}
    assert c.get("/api/meta").status_code == 200
    sid = man["demo_store_id"]
    for u in [f"/api/stores/{sid}/health", f"/api/stores/{sid}/profile", f"/api/stores/{sid}/incidents",
              f"/api/stores/{sid}/changes", f"/api/stores/{sid}/impact", "/api/majors", "/api/fleet/chronic",
              "/api/fleet/overview", "/api/handoff?region=Texas", "/api/value-model", "/api/audit",
              f"/api/incidents/{man['demo_store_open_incidents'][0]}/similar"]:
        assert c.get(u, headers=h).status_code == 200, u
    assert c.get(f"/api/stores/{sid}/health").status_code == 401
    assert c.post("/api/nlq", json={"question": "P1 count by month"}, headers=h).json()["ok"]
    assert c.get("/api/audit", headers={"X-Persona-Id": P["manager"]}).status_code == 403


def test_chat_sse(data, man):
    c = TestClient(app)
    h = {"X-Persona-Id": default_personas()["exec"]}
    with c.stream("POST", "/api/chat", json={"question": "Is this affecting other stores?", "store_id": man["demo_store_id"],
                                             "mode": "template"}, headers=h) as r:
        body = "".join(r.iter_text())
    for ev in ("tool_start", "tool_end", "delta", "answer", "trace"):
        assert f"event: {ev}" in body
