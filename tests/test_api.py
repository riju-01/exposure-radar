"""API smoke tests against a two-account fixture database (offline LLM mode)."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app_db):
    from salesintel.api import main
    main._facets_cache = None
    with TestClient(main.app) as c:
        yield c


def test_list_defaults_to_prospects_only(client):
    r = client.get("/api/accounts").json()
    assert r["total"] == 1 and r["items"][0]["name"] == "acme-dental.com"
    assert client.get("/api/accounts", params={"prospects_only": False}).json()["total"] == 2


def test_filters_combine(client):
    assert client.get("/api/accounts", params={"tier": "A", "signal": "kev_vuln"}).json()["total"] == 1
    assert client.get("/api/accounts", params={"tier": "B"}).json()["total"] == 0
    assert client.get("/api/accounts", params={"q": "dental"}).json()["total"] == 1


def test_detail_has_evidence_refs(client):
    d = client.get("/api/accounts/1").json()
    assert [e["ref"] for e in d["evidence"]] == ["E1", "E2"]
    assert client.get("/api/accounts/999").status_code == 404


def test_ai_actions_work_offline_and_cache(client):
    first = client.post("/api/accounts/1/outreach").json()
    assert first["provider"] == "offline" and first["checks"]["passed"] and not first["cached"]
    assert client.post("/api/accounts/1/outreach").json()["cached"] is True
    cls = client.post("/api/accounts/1/classify").json()
    assert cls["output"]["entity_type"]
    ops = client.get("/api/ops").json()
    assert ops["llm_mode"] == "offline" and len(ops["recent_calls"]) == 2


def test_shortlist_and_export(client):
    client.put("/api/shortlist/1")
    assert client.get("/api/accounts", params={"shortlisted": True}).json()["total"] == 1
    csv = client.get("/api/export.csv", params={"shortlisted": True}).text
    assert csv.splitlines()[0].startswith("name,domain,tier") and "acme-dental.com" in csv
    client.delete("/api/shortlist/1")
    assert client.get("/api/accounts", params={"shortlisted": True}).json()["total"] == 0
