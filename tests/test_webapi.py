from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.events import EventLog
from webapi import create_api

TOKEN = "t" * 32
WEB = Path(__file__).resolve().parent.parent / "web"


@pytest.fixture
def setup():
    store, launched = {}, []
    api = create_api(store, TOKEN, WEB, lambda *a: launched.append(a))
    return TestClient(api), store, launched


def test_index_and_security_headers(setup):
    client, _, _ = setup
    r = client.get("/")
    assert r.status_code == 200
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert client.get("/static/app.js").status_code == 200


def test_static_is_allow_listed(setup):
    client, _, _ = setup
    assert client.get("/static/..%2Fapp.py").status_code == 404
    assert client.get("/static/index.html").status_code == 404


def test_start_requires_token(setup):
    client, _, launched = setup
    body = {"task": "Build a small site please", "brain": "spark"}
    assert client.post("/api/runs", json=body).status_code == 401
    assert client.post("/api/runs", json=body, headers={"X-Access-Token": "wrong"}).status_code == 401
    r = client.post("/api/runs", json=body, headers={"X-Access-Token": TOKEN})
    assert r.status_code == 200 and launched and launched[0][0] == r.json()["id"]
    # only one build at a time
    assert client.post("/api/runs", json=body, headers={"X-Access-Token": TOKEN}).status_code == 429


def test_start_validates_input(setup):
    client, _, _ = setup
    h = {"X-Access-Token": TOKEN}
    assert client.post("/api/runs", json={"task": "short", "brain": "spark"}, headers=h).status_code == 422
    assert client.post("/api/runs", json={"task": "x" * 50, "brain": "gpt"}, headers=h).status_code == 422


def test_events_and_run_id_validation(setup):
    client, store, _ = setup
    run = "20261031-120000-abcdef"
    EventLog(store, run).emit("note", message="hi")
    assert client.get(f"/api/runs/{run}/events").json()[0]["message"] == "hi"
    assert client.get("/api/runs/bad-id/events").status_code == 400
    assert client.get(f"/api/runs/{run}/events?since=-1").status_code == 400


def test_profile_endpoint(setup):
    client, _, _ = setup
    data = client.get("/api/profile").json()
    assert data["profile"]["synthetic"] is True and len(data["decisions"]) >= 40


def test_short_token_rejected():
    with pytest.raises(ValueError):
        create_api({}, "short", WEB, lambda *a: None)
