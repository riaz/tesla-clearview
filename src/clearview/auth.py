"""Tesla Fleet API OAuth: user login (authorization code), token refresh, partner registration."""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .config import Settings

AUTHORIZE_URL = "https://auth.tesla.com/oauth2/v3/authorize"
TOKEN_URL = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"


class AuthError(RuntimeError):
    pass


def _save_tokens(settings: Settings, data: dict) -> None:
    data = dict(data)
    data["expires_at"] = time.time() + int(data.get("expires_in", 0)) - 60
    settings.token_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings.token_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(settings.token_file)


def _load_tokens(settings: Settings) -> dict:
    if not settings.token_file.exists():
        raise AuthError("Not logged in. Run `clearview auth login` first.")
    return json.loads(settings.token_file.read_text())


def make_state(return_to: str | None = None) -> tuple[str, str]:
    """Return (nonce, state). When `return_to` is set, it rides along in the state so the
    static callback page on your Pages domain knows which home-server URL to bounce to."""
    nonce = secrets.token_urlsafe(16)
    if not return_to:
        return nonce, nonce
    enc = base64.urlsafe_b64encode(return_to.encode()).decode().rstrip("=")
    return nonce, f"{nonce}.{enc}"


def state_nonce(state: str) -> str:
    return state.split(".", 1)[0]


def authorize_url(settings: Settings, state: str) -> str:
    if not settings.tesla_client_id or not settings.tesla_client_secret:
        raise AuthError("Set TESLA_CLIENT_ID and TESLA_CLIENT_SECRET in .env first.")
    if not settings.tesla_redirect_uri:
        raise AuthError("Set TESLA_REDIRECT_URI in .env (e.g. https://<user>.github.io/callback/).")
    return AUTHORIZE_URL + "?" + urlencode(
        {
            "response_type": "code",
            "client_id": settings.tesla_client_id,
            "redirect_uri": settings.tesla_redirect_uri,
            "scope": settings.tesla_scopes,
            "state": state,
            "locale": "en-US",
            "prompt": "login",
        }
    )


def exchange_code(settings: Settings, code: str) -> None:
    """Swap an authorization code for user tokens and save them."""
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": settings.tesla_client_id,
            "client_secret": settings.tesla_client_secret,
            "code": code,
            "audience": settings.fleet_base,
            "redirect_uri": settings.tesla_redirect_uri,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise AuthError(f"Token exchange failed ({resp.status_code}): {resp.text}")
    _save_tokens(settings, resp.json())


def token_info(settings: Settings) -> dict:
    """Login status for the dashboard (never returns the tokens themselves)."""
    if not settings.token_file.exists():
        return {"logged_in": False}
    try:
        t = _load_tokens(settings)
    except (AuthError, ValueError):
        return {"logged_in": False}
    return {"logged_in": bool(t.get("refresh_token")), "access_expires_at": t.get("expires_at")}


def logout(settings: Settings) -> None:
    settings.token_file.unlink(missing_ok=True)


def _capture_code(listen_uri: str, expected_nonce: str, timeout_s: int = 300) -> str:
    """Run a one-shot local HTTP server to receive the OAuth redirect."""
    parsed = urlparse(listen_uri)
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = parse_qs(urlparse(self.path).query)
            result["code"] = qs.get("code", [""])[0]
            result["state"] = qs.get("state", [""])[0]
            result["error"] = qs.get("error", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"clearview: login received, you can close this tab.")

        def log_message(self, *args):
            pass

    server = HTTPServer((parsed.hostname or "localhost", parsed.port or 80), Handler)
    server.timeout = timeout_s
    server.handle_request()
    server.server_close()
    if result.get("error"):
        raise AuthError(f"Tesla returned error: {result['error']}")
    if state_nonce(result.get("state", "")) != expected_nonce:
        raise AuthError("OAuth state mismatch; aborting.")
    if not result.get("code"):
        raise AuthError("No authorization code received.")
    return result["code"]


CLI_CALLBACK = "http://localhost:8765/callback"


def login(settings: Settings, open_browser: bool = True) -> None:
    """Interactive third-party (user) token login from the CLI.

    With a Pages redirect URI, the callback page bounces the browser to a one-shot
    listener on localhost:8765, so this works when the browser runs on this machine.
    Otherwise paste the URL the browser ends up on.
    """
    redirect_host = urlparse(settings.tesla_redirect_uri).hostname
    direct_local = redirect_host in ("localhost", "127.0.0.1")
    nonce, state = make_state(None if direct_local else CLI_CALLBACK)
    url = authorize_url(settings, state)
    print(f"Open this URL to authorise clearview:\n\n  {url}\n")
    if open_browser:
        webbrowser.open(url)
        code = _capture_code(settings.tesla_redirect_uri if direct_local else CLI_CALLBACK, nonce)
    else:
        pasted = input("Paste the full URL you were redirected to: ").strip()
        qs = parse_qs(urlparse(pasted).query)
        if state_nonce(qs.get("state", [""])[0]) != nonce:
            raise AuthError("OAuth state mismatch; aborting.")
        code = qs.get("code", [""])[0]
    exchange_code(settings, code)
    print(f"Logged in. Tokens saved to {settings.token_file}")


def access_token(settings: Settings) -> str:
    """Return a valid user access token, refreshing it if needed."""
    tokens = _load_tokens(settings)
    if time.time() < tokens.get("expires_at", 0):
        return tokens["access_token"]
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": settings.tesla_client_id,
            "refresh_token": tokens["refresh_token"],
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise AuthError(f"Token refresh failed ({resp.status_code}): {resp.text}. Run `clearview auth login`.")
    new = resp.json()
    new.setdefault("refresh_token", tokens["refresh_token"])
    _save_tokens(settings, new)
    return new["access_token"]


def register_partner(settings: Settings) -> dict:
    """One-time: register your app's domain/public key with the Fleet API region."""
    if not settings.tesla_domain:
        raise AuthError("Set TESLA_DOMAIN in .env first.")
    tok = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": settings.tesla_client_id,
            "client_secret": settings.tesla_client_secret,
            "scope": settings.tesla_scopes,
            "audience": settings.fleet_base,
        },
        timeout=30,
    )
    if tok.status_code != 200:
        raise AuthError(f"Partner token failed ({tok.status_code}): {tok.text}")
    resp = httpx.post(
        f"{settings.fleet_base}/api/1/partner_accounts",
        headers={"Authorization": f"Bearer {tok.json()['access_token']}"},
        json={"domain": settings.tesla_domain},
        timeout=30,
    )
    if resp.status_code >= 300:
        raise AuthError(f"Partner registration failed ({resp.status_code}): {resp.text}")
    return resp.json()
