"""Offline tests for the Google SSO auth module (duvo/api/auth.py).

No network: Google's OAuth endpoints and id_token verification are mocked, so the
whole suite runs without contacting Google. Covers state-based login, id_token
verification (issuer/audience/expiry/signature delegated to a mocked verifier),
Workspace ``hd`` / allowlist enforcement, signed httpOnly+Secure+SameSite session
cookie issuance, ``GET /me``, ``POST /auth/logout``, the current-user dependency
guarding routes, CSRF on state-changing requests, and the real-run authorization gate.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

# Deterministic, non-secret config for the whole module — set before importing auth.
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-please-change")
os.environ.setdefault("AUTH_ALLOWED_DOMAINS", "duvo.com")
os.environ.setdefault("AUTH_ALLOWED_EMAILS", "outsider@gmail.com")
os.environ.setdefault("AUTH_REAL_RUN_ROLES", "admin,operator")
# TestClient speaks plain HTTP (http://testserver) so Secure cookies are never echoed
# back. Opt into insecure cookies for the offline suite; production keeps Secure on.
os.environ.setdefault("AUTH_COOKIE_INSECURE", "true")

from duvo.api import auth  # noqa: E402


# --------------------------------------------------------------------------- #
# Helpers / fixtures
# --------------------------------------------------------------------------- #
def _make_app() -> FastAPI:
    """A minimal app that mounts the auth router and one guarded route."""
    app = FastAPI()
    app.include_router(auth.router)

    @app.get("/protected")
    def protected(user: auth.User = Depends(auth.get_current_user)) -> dict[str, str]:
        return {"email": user.email}

    @app.post("/state-change")
    def state_change(
        user: auth.User = Depends(auth.get_current_user),
        _csrf: None = Depends(auth.require_csrf),
    ) -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    # raise_server_exceptions=False so HTTPException(...) becomes a real response.
    with TestClient(_make_app(), raise_server_exceptions=False) as c:
        yield c


def _login_cookie(client: TestClient, email: str, hd: str | None, role: str = "operator") -> None:
    """Drive a full login (mocked Google) so the client holds a valid session cookie."""
    resp = client.get("/auth/login", follow_redirects=False)
    assert resp.status_code in (302, 307)
    # The login response must persist the OAuth ``state`` somewhere we can echo back.
    location = resp.headers["location"]
    assert "state=" in location
    state = location.split("state=")[1].split("&")[0]

    claims = {
        "iss": "https://accounts.google.com",
        "aud": os.environ["GOOGLE_CLIENT_ID"],
        "email": email,
        "email_verified": True,
        "sub": "123",
        "name": "Test User",
    }
    if hd is not None:
        claims["hd"] = hd

    with patch.object(auth, "_exchange_and_verify", return_value=claims):
        cb = client.get(
            f"/auth/callback?state={state}&code=fake-auth-code",
            follow_redirects=False,
        )
    assert cb.status_code in (302, 307), cb.text


# --------------------------------------------------------------------------- #
# Login — state
# --------------------------------------------------------------------------- #
def test_login_redirects_to_google_with_state(client: TestClient) -> None:
    resp = client.get("/auth/login", follow_redirects=False)
    assert resp.status_code in (302, 307)
    loc = resp.headers["location"]
    assert "accounts.google.com" in loc
    assert "state=" in loc
    assert "client_id=" in loc


def test_callback_rejects_bad_state(client: TestClient) -> None:
    client.get("/auth/login", follow_redirects=False)
    with patch.object(auth, "_exchange_and_verify", return_value={"email": "x@duvo.com"}):
        resp = client.get("/auth/callback?state=forged-state&code=c", follow_redirects=False)
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Callback — allowlist enforcement (hd domain + email allowlist)
# --------------------------------------------------------------------------- #
def test_callback_allows_workspace_hd(client: TestClient) -> None:
    _login_cookie(client, "jakub@duvo.com", hd="duvo.com")
    me = client.get("/me")
    assert me.status_code == 200
    assert me.json()["email"] == "jakub@duvo.com"


def test_me_exposes_can_real_run_for_allowlisted_domain(client: TestClient) -> None:
    # An allowlisted-domain user gets a real-run-capable role → can_real_run True (#14).
    _login_cookie(client, "jakub@duvo.com", hd="duvo.com")
    body = client.get("/me").json()
    assert body["can_real_run"] is True


def test_me_can_real_run_false_for_identity_only_user(client: TestClient) -> None:
    # An allowlisted-by-email outsider is identity-only (viewer) → cannot launch a real run.
    _login_cookie(client, "outsider@gmail.com", hd=None)
    body = client.get("/me").json()
    assert body["can_real_run"] is False


def test_callback_allows_explicit_email_allowlist(client: TestClient) -> None:
    # No hd (consumer gmail), but the email is on AUTH_ALLOWED_EMAILS.
    _login_cookie(client, "outsider@gmail.com", hd=None)
    me = client.get("/me")
    assert me.status_code == 200
    assert me.json()["email"] == "outsider@gmail.com"


def test_callback_rejects_non_allowlisted(client: TestClient) -> None:
    resp = client.get("/auth/login", follow_redirects=False)
    state = resp.headers["location"].split("state=")[1].split("&")[0]
    claims = {
        "iss": "https://accounts.google.com",
        "aud": os.environ["GOOGLE_CLIENT_ID"],
        "email": "stranger@evil.com",
        "email_verified": True,
        "hd": "evil.com",
    }
    with patch.object(auth, "_exchange_and_verify", return_value=claims):
        cb = client.get(f"/auth/callback?state={state}&code=c", follow_redirects=False)
    assert cb.status_code == 403


def test_callback_rejects_unverified_email(client: TestClient) -> None:
    resp = client.get("/auth/login", follow_redirects=False)
    state = resp.headers["location"].split("state=")[1].split("&")[0]
    claims = {
        "iss": "https://accounts.google.com",
        "aud": os.environ["GOOGLE_CLIENT_ID"],
        "email": "jakub@duvo.com",
        "email_verified": False,
        "hd": "duvo.com",
    }
    with patch.object(auth, "_exchange_and_verify", return_value=claims):
        cb = client.get(f"/auth/callback?state={state}&code=c", follow_redirects=False)
    assert cb.status_code == 403


# --------------------------------------------------------------------------- #
# Session cookie flags
# --------------------------------------------------------------------------- #
def test_session_cookie_flags(client: TestClient) -> None:
    resp = client.get("/auth/login", follow_redirects=False)
    state = resp.headers["location"].split("state=")[1].split("&")[0]
    claims = {
        "iss": "https://accounts.google.com",
        "aud": os.environ["GOOGLE_CLIENT_ID"],
        "email": "jakub@duvo.com",
        "email_verified": True,
        "hd": "duvo.com",
    }
    # Force production cookie mode so we can assert the Secure flag is emitted, even
    # though the rest of the suite runs with AUTH_COOKIE_INSECURE for the HTTP TestClient.
    with (
        patch.object(auth, "_exchange_and_verify", return_value=claims),
        patch.object(auth, "_cookie_secure", return_value=True),
    ):
        cb = client.get(f"/auth/callback?state={state}&code=c", follow_redirects=False)
    set_cookie = cb.headers.get("set-cookie", "")
    assert auth.SESSION_COOKIE in set_cookie
    low = set_cookie.lower()
    assert "httponly" in low
    assert "secure" in low
    assert "samesite" in low


def test_session_cookie_is_signed_tamper_rejected(client: TestClient) -> None:
    """A forged/altered session cookie must not authenticate."""
    client.cookies.set(auth.SESSION_COOKIE, "not-a-valid-signed-token")
    resp = client.get("/protected")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Guard dependency
# --------------------------------------------------------------------------- #
def test_protected_route_requires_auth(client: TestClient) -> None:
    assert client.get("/protected").status_code == 401


def test_protected_route_allows_authenticated(client: TestClient) -> None:
    _login_cookie(client, "jakub@duvo.com", hd="duvo.com")
    resp = client.get("/protected")
    assert resp.status_code == 200
    assert resp.json()["email"] == "jakub@duvo.com"


# --------------------------------------------------------------------------- #
# Logout
# --------------------------------------------------------------------------- #
def test_logout_clears_session(client: TestClient) -> None:
    _login_cookie(client, "jakub@duvo.com", hd="duvo.com")
    assert client.get("/me").status_code == 200
    csrf = client.cookies.get(auth.CSRF_COOKIE)
    out = client.post("/auth/logout", headers={auth.CSRF_HEADER: csrf or ""})
    assert out.status_code in (200, 204)
    # Cookie cleared → /me now unauthorized.
    client.cookies.delete(auth.SESSION_COOKIE)
    assert client.get("/me").status_code == 401


# --------------------------------------------------------------------------- #
# CSRF on state-changing requests
# --------------------------------------------------------------------------- #
def test_csrf_cookie_set_on_login(client: TestClient) -> None:
    _login_cookie(client, "jakub@duvo.com", hd="duvo.com")
    assert client.cookies.get(auth.CSRF_COOKIE) is not None


def test_state_change_without_csrf_rejected(client: TestClient) -> None:
    _login_cookie(client, "jakub@duvo.com", hd="duvo.com")
    resp = client.post("/state-change")  # no CSRF header
    assert resp.status_code == 403


def test_state_change_with_mismatched_csrf_rejected(client: TestClient) -> None:
    _login_cookie(client, "jakub@duvo.com", hd="duvo.com")
    resp = client.post("/state-change", headers={auth.CSRF_HEADER: "wrong-token"})
    assert resp.status_code == 403


def test_state_change_with_matching_csrf_allowed(client: TestClient) -> None:
    _login_cookie(client, "jakub@duvo.com", hd="duvo.com")
    csrf = client.cookies.get(auth.CSRF_COOKIE)
    assert csrf
    resp = client.post("/state-change", headers={auth.CSRF_HEADER: csrf})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# --------------------------------------------------------------------------- #
# Real-run authorization gate (#14)
# --------------------------------------------------------------------------- #
def test_require_real_run_authorization_blocks_without_confirmation() -> None:
    user = auth.User(email="jakub@duvo.com", name="J", role="admin")
    with pytest.raises(auth.AuthorizationError):
        auth.require_real_run_authorization(user, confirm=False)


def test_require_real_run_authorization_blocks_non_allowlisted_role() -> None:
    user = auth.User(email="viewer@duvo.com", name="V", role="viewer")
    with pytest.raises(auth.AuthorizationError):
        auth.require_real_run_authorization(user, confirm=True)


def test_require_real_run_authorization_allows_role_plus_confirmation() -> None:
    user = auth.User(email="jakub@duvo.com", name="J", role="admin")
    # Returns truthy / does not raise.
    auth.require_real_run_authorization(user, confirm=True)


def test_role_assigned_from_allowlisted_domain() -> None:
    """Users from an allowlisted Workspace domain get a real-run-capable role."""
    role = auth._role_for("jakub@duvo.com", hd="duvo.com")
    assert role in auth._real_run_roles()


def test_role_for_email_allowlisted_outsider_is_viewer() -> None:
    """An explicitly allowlisted outside email is identity-only (cannot fire real runs)."""
    role = auth._role_for("outsider@gmail.com", hd=None)
    assert role not in auth._real_run_roles()
