import base64
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from clearview import auth, server
from clearview.config import Settings


def test_state_carries_return_url():
    nonce, state = auth.make_state("http://homeserver.local:8787/auth/callback")
    assert auth.state_nonce(state) == nonce
    enc = state.split(".", 1)[1]
    assert base64.urlsafe_b64decode(enc + "=" * (-len(enc) % 4)).decode() == "http://homeserver.local:8787/auth/callback"


def test_state_without_return_url():
    nonce, state = auth.make_state(None)
    assert nonce == state == auth.state_nonce(state)


@pytest.fixture
def client(tmp_path, monkeypatch):
    s = Settings(
        CLEARVIEW_API_TOKEN="secret-token",
        tesla_client_id="cid",
        tesla_client_secret="csecret",
        tesla_redirect_uri="https://me.github.io/callback/",
        tesla_domain="me.github.io",
        token_file=tmp_path / "tokens.json",
        _env_file=None,
    )
    monkeypatch.setattr(server, "get_settings", lambda: s)
    server._oauth_nonces.clear()
    return TestClient(server.app), s


def test_dashboard_requires_login(client):
    c, _ = client
    assert c.get("/").status_code == 200  # the page itself is public; its data isn't
    assert c.get("/api/overview").status_code == 401
    assert c.post("/api/login", json={"token": "nope"}).status_code == 401
    assert c.post("/api/login", json={"token": "secret-token"}).status_code == 200
    r = c.get("/api/overview")
    assert r.status_code == 200
    assert r.json()["tesla"] == {"logged_in": False}
    assert r.json()["config"]["pair_url"] == "https://tesla.com/_ak/me.github.io"


def test_bearer_still_works(client):
    c, _ = client
    assert c.get("/sessions", headers={"Authorization": "Bearer secret-token"}).status_code == 200


def test_oauth_round_trip(client, monkeypatch):
    c, s = client
    c.post("/api/login", json={"token": "secret-token"})
    r = c.get("/auth/login", follow_redirects=False)
    assert r.status_code == 303
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["redirect_uri"] == ["https://me.github.io/callback/"]
    state = q["state"][0]

    got = {}
    monkeypatch.setattr(auth, "exchange_code", lambda settings, code: got.setdefault("code", code))
    fresh = TestClient(server.app)  # the bounce arrives without our cookie being required
    r = fresh.get(f"/auth/callback?code=abc&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/?login=ok"
    assert got["code"] == "abc"

    # nonce is single-use
    r = fresh.get(f"/auth/callback?code=abc&state={state}", follow_redirects=False)
    assert "login_error" in r.headers["location"]


def test_callback_rejects_unknown_state(client):
    c, _ = client
    r = c.get("/auth/callback?code=abc&state=forged.aGk", follow_redirects=False)
    assert "login_error" in r.headers["location"]


def test_auth_login_needs_dashboard_session(client):
    c, _ = client
    assert c.get("/auth/login", follow_redirects=False).status_code == 401


def test_location_preference(tmp_path):
    from clearview import location

    s = Settings(token_file=tmp_path / "tokens.json", home_lat=1.0, home_lon=2.0, _env_file=None)
    assert location.resolve(s).source == "home"
    location.save_car_location(s, 48.85, 2.35)
    loc = location.resolve(s)
    assert (loc.source, loc.lat, loc.lon) == ("car_last_known", 48.85, 2.35)
    assert location.resolve(s, 40.7, -74.0).source == "phone"
    location.save_car_location(s, None, None)  # no GPS in the reading: keep the old one
    assert location.resolve(s).lat == 48.85


def test_login_from_0000_returns_to_localhost(client):
    _, s = client
    c = TestClient(server.app, base_url="http://0.0.0.0:8787")
    c.post("/api/login", json={"token": "secret-token"})
    state = parse_qs(urlparse(c.get("/auth/login", follow_redirects=False).headers["location"]).query)["state"][0]
    enc = state.split(".", 1)[1]
    assert base64.urlsafe_b64decode(enc + "=" * (-len(enc) % 4)).decode() == "http://localhost:8787/auth/callback"


def test_alerts_endpoint_flags_camera(client, monkeypatch):
    c, _ = client
    c.post("/api/login", json={"token": "secret-token"})

    async def fake_alerts(self):
        return [
            {"name": "APP_w304_pillarCameraBlocked", "user_text": "Camera blocked or blinded", "time": "2026-11-10T08:00:00Z"},
            {"name": "VCFRONT_a180_lowVoltage", "user_text": "Low 12V battery", "time": "2026-11-11T08:00:00Z"},
        ]

    monkeypatch.setattr(server.FleetClient, "recent_alerts", fake_alerts)
    r = c.post("/api/vehicle/alerts", json={}).json()
    assert [a["camera"] for a in r] == [False, True]  # newest first
    assert r[1]["text"] == "Camera blocked or blinded"
