"""Google SSO (OAuth2) + session/CSRF guards + the real-run authorization gate.

Implements plan items #13 and #14 for the FastAPI layer:

* ``GET /auth/login`` → Google consent **with a signed ``state``** (CSRF on the OAuth
  round-trip).
* ``GET /auth/callback`` → exchanges the code, **verifies the id_token** (issuer,
  audience, expiry, signature — delegated to ``authlib`` in :func:`_exchange_and_verify`,
  the seam offline tests mock), enforces ``email_verified`` + the Workspace ``hd`` /
  email allowlist, then issues an **httpOnly + Secure + SameSite** session cookie
  signed with the env ``SESSION_SECRET`` (``itsdangerous``). A double-submit CSRF token
  cookie is issued alongside it.
* ``GET /me`` / ``POST /auth/logout``.
* :func:`get_current_user` — a dependency that guards every non-auth route.
* :func:`require_csrf` — a dependency enforcing the double-submit CSRF token on
  state-changing requests.
* :func:`require_real_run_authorization` — the #14 gate: a real (non-dry) run needs an
  **allowlisted role + an explicit confirmation**.

Config is read from ``os.environ`` with safe defaults (no edit to ``config.py``); the
session/CSRF signing key and Google client secret are read lazily so the module imports
without secrets and the offline tests run network-free.
"""

from __future__ import annotations

import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from duvo.infra.logging_setup import get_logger

_log = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Constants — cookie / header names
# --------------------------------------------------------------------------- #
SESSION_COOKIE = "duvo_session"
CSRF_COOKIE = "duvo_csrf"
CSRF_HEADER = "x-csrf-token"
_STATE_COOKIE = "duvo_oauth_state"

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

_SESSION_MAX_AGE = 60 * 60 * 12  # 12h session lifetime
_STATE_MAX_AGE = 60 * 10  # 10 min to complete the OAuth round-trip
_SESSION_SALT = "duvo-session-v1"
_STATE_SALT = "duvo-oauth-state-v1"


# --------------------------------------------------------------------------- #
# Config (env, lazy) — no edit to config.py, safe defaults, secrets read lazily
# --------------------------------------------------------------------------- #
def _session_secret() -> str:
    """The signing key for the session and state cookies (required for live use)."""
    secret = os.environ.get("SESSION_SECRET", "")
    if not secret:
        raise RuntimeError("Missing required env var: SESSION_SECRET. Add it to .env")
    return secret


def _google_client_id() -> str:
    return os.environ.get("GOOGLE_CLIENT_ID", "")


def _google_client_secret() -> str:
    return os.environ.get("GOOGLE_CLIENT_SECRET", "")


def _redirect_uri() -> str:
    return os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback")


def _allowed_domains() -> set[str]:
    raw = os.environ.get("AUTH_ALLOWED_DOMAINS", "")
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


def _allowed_emails() -> set[str]:
    raw = os.environ.get("AUTH_ALLOWED_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _real_run_roles() -> set[str]:
    """Roles permitted to launch a real (side-effecting) run (#14)."""
    raw = os.environ.get("AUTH_REAL_RUN_ROLES", "admin,operator")
    return {r.strip().lower() for r in raw.split(",") if r.strip()}


def _cookie_secure() -> bool:
    """Secure flag on cookies. Defaults to True (httpOnly+Secure+SameSite per #13).

    Set ``AUTH_COOKIE_INSECURE=true`` only for plain-HTTP local dev.
    """
    return os.environ.get("AUTH_COOKIE_INSECURE", "").strip().lower() != "true"


def _post_login_redirect() -> str:
    return os.environ.get("AUTH_POST_LOGIN_REDIRECT", "/")


# --------------------------------------------------------------------------- #
# Domain types
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class User:
    """The authenticated principal carried in the signed session cookie."""

    email: str
    name: str
    role: str


class AuthorizationError(Exception):
    """Raised by :func:`require_real_run_authorization` when the gate is not satisfied."""


# --------------------------------------------------------------------------- #
# Serializers (lazy singletons — built per call from the lazily-read secret)
# --------------------------------------------------------------------------- #
def _session_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_session_secret(), salt=_SESSION_SALT)


def _state_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_session_secret(), salt=_STATE_SALT)


def _issue_session(user: User) -> str:
    return _session_serializer().dumps({"email": user.email, "name": user.name, "role": user.role})


def _read_session(token: str) -> User | None:
    """Verify the signed (and unexpired) session token → :class:`User`, else ``None``."""
    try:
        data = _session_serializer().loads(token, max_age=_SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or "email" not in data:
        return None
    return User(
        email=str(data["email"]),
        name=str(data.get("name", "")),
        role=str(data.get("role", "viewer")),
    )


# --------------------------------------------------------------------------- #
# Allowlist + role assignment
# --------------------------------------------------------------------------- #
def _is_allowed(email: str, hd: str | None) -> bool:
    """A user is allowed if their Workspace ``hd`` is allowlisted, OR their email is."""
    email = email.lower()
    if hd and hd.lower() in _allowed_domains():
        return True
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    if domain and domain in _allowed_domains():
        return True
    return email in _allowed_emails()


def _role_for(email: str, hd: str | None) -> str:
    """Assign a role: allowlisted-domain users are real-run-capable; allowlisted-by-email
    outsiders are identity-only (``viewer``) so an SSO grant alone can't fire side effects.
    """
    email = email.lower()
    domain = (hd or (email.rsplit("@", 1)[-1] if "@" in email else "")).lower()
    if domain and domain in _allowed_domains():
        # First allowlisted role is the default operator role for trusted-domain users.
        roles = _real_run_roles()
        return "admin" if "admin" in roles else (next(iter(roles), "viewer"))
    return "viewer"


# --------------------------------------------------------------------------- #
# Token exchange + verification (the offline-mocked network seam)
# --------------------------------------------------------------------------- #
def _exchange_and_verify(code: str) -> dict:
    """Exchange the auth ``code`` for tokens and return the **verified** id_token claims.

    Verification (issuer, audience, expiry, **signature** against Google's JWKS) is done
    here via ``authlib``; callers receive only validated claims. This is the single
    network seam — offline tests patch this function so no request reaches Google.

    Args:
        code: The one-time authorization code from Google's redirect.

    Returns:
        The verified id_token claims (``iss``, ``aud``, ``exp``, ``email``,
        ``email_verified``, optionally ``hd`` …).

    Raises:
        HTTPException: 401 if the token cannot be exchanged or fails verification.
    """
    import httpx
    from authlib.jose import jwt
    from authlib.jose.errors import JoseError

    client_id = _google_client_id()
    try:
        # Discover endpoints + JWKS (network — mocked in tests).
        with httpx.Client(timeout=10.0) as http:
            conf = http.get("https://accounts.google.com/.well-known/openid-configuration")
            conf.raise_for_status()
            meta = conf.json()
            token_resp = http.post(
                meta["token_endpoint"],
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": _google_client_secret(),
                    "redirect_uri": _redirect_uri(),
                    "grant_type": "authorization_code",
                },
            )
            token_resp.raise_for_status()
            id_token = token_resp.json()["id_token"]
            jwks = http.get(meta["jwks_uri"]).json()

        claims = jwt.decode(
            id_token,
            jwks,
            claims_options={
                "iss": {"essential": True, "values": list(_GOOGLE_ISSUERS)},
                "aud": {"essential": True, "value": client_id},
                "exp": {"essential": True},
            },
        )
        claims.validate(now=int(time.time()))
        return dict(claims)
    except (JoseError, KeyError, httpx.HTTPError) as exc:
        _log.warning("id_token verification failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid id_token"
        ) from exc


# --------------------------------------------------------------------------- #
# Dependencies — the route guards
# --------------------------------------------------------------------------- #
def get_current_user(
    duvo_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> User:
    """FastAPI dependency: authenticate the request from the signed session cookie.

    Returns:
        The authenticated :class:`User`.

    Raises:
        HTTPException: 401 when the cookie is absent, forged, or expired.
    """
    if not duvo_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    user = _read_session(duvo_session)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session")
    return user


def require_csrf(
    duvo_csrf: str | None = Cookie(default=None, alias=CSRF_COOKIE),
    csrf_header: str | None = Header(default=None, alias=CSRF_HEADER),
) -> None:
    """Double-submit CSRF guard for state-changing requests.

    The CSRF token issued at login lives in a (non-httpOnly) cookie; the SPA echoes it in
    the ``X-CSRF-Token`` header. Both must be present and match (constant-time compare).

    Raises:
        HTTPException: 403 when the header is missing or does not match the cookie.
    """
    if not duvo_csrf or not csrf_header or not secrets.compare_digest(duvo_csrf, csrf_header):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF check failed")


# --------------------------------------------------------------------------- #
# #14 — real-run authorization gate
# --------------------------------------------------------------------------- #
def require_real_run_authorization(user: User, confirm: bool) -> User:
    """Gate a real (non-dry) run: an allowlisted role **and** an explicit confirmation.

    Identity ≠ permission to cause side effects: until the Phase 2 approval cockpit
    exists, a real run (CRM writes + Slack alerts) requires both conditions.

    Args:
        user: The authenticated principal.
        confirm: The explicit, deliberate confirmation flag from the request.

    Returns:
        *user* unchanged when authorized (convenience for use as a dependency).

    Raises:
        AuthorizationError: When the role is not allowlisted or confirmation is missing.
    """
    if user.role.lower() not in _real_run_roles():
        raise AuthorizationError(f"role '{user.role}' is not permitted to launch a real run")
    if not confirm:
        raise AuthorizationError("a real (non-dry) run requires an explicit confirmation")
    return user


# --------------------------------------------------------------------------- #
# Cookie helpers
# --------------------------------------------------------------------------- #
def _set_auth_cookies(response: Response, user: User) -> None:
    """Set the signed httpOnly session cookie + the (readable) double-submit CSRF cookie."""
    secure = _cookie_secure()
    response.set_cookie(
        SESSION_COOKIE,
        _issue_session(user),
        max_age=_SESSION_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        secrets.token_urlsafe(32),
        max_age=_SESSION_MAX_AGE,
        httponly=False,  # the SPA must read it to echo it back in the header
        secure=secure,
        samesite="lax",
        path="/",
    )


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
router = APIRouter()


@router.get("/auth/login")
def login() -> RedirectResponse:
    """Redirect to Google's consent screen with a signed, time-boxed ``state``."""
    nonce = secrets.token_urlsafe(16)
    state = _state_serializer().dumps({"n": nonce})
    params = {
        "client_id": _google_client_id(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    url = f"{_GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    resp = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    resp.set_cookie(
        _STATE_COOKIE,
        state,
        max_age=_STATE_MAX_AGE,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )
    return resp


@router.get("/auth/callback")
def callback(
    request: Request,
    code: str,
    state: str,
    oauth_state: str | None = Cookie(default=None, alias=_STATE_COOKIE),
) -> RedirectResponse:
    """Validate ``state``, verify the id_token, enforce the allowlist, set the session."""
    # 1) CSRF on the OAuth round-trip: the returned state must equal our signed cookie and
    #    must itself be a valid, unexpired signature.
    if not oauth_state or not secrets.compare_digest(oauth_state, state):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="bad oauth state")
    try:
        _state_serializer().loads(state, max_age=_STATE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid oauth state"
        ) from None

    # 2) Exchange + verify (issuer/aud/exp/signature) — the mocked seam.
    claims = _exchange_and_verify(code)

    email = str(claims.get("email", "")).lower()
    hd = claims.get("hd")
    if not email or not claims.get("email_verified", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="email not present or unverified"
        )
    if not _is_allowed(email, hd):
        _log.warning("rejected non-allowlisted login for %s (hd=%s)", email, hd)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not allowed")

    user = User(email=email, name=str(claims.get("name", "")), role=_role_for(email, hd))
    resp = RedirectResponse(_post_login_redirect(), status_code=status.HTTP_302_FOUND)
    _set_auth_cookies(resp, user)
    resp.delete_cookie(_STATE_COOKIE, path="/")
    _log.info("authenticated %s as %s", email, user.role)
    return resp


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict[str, object]:
    """Return the current authenticated principal.

    ``can_real_run`` tells the SPA whether this user's role may launch a real (non-dry) run
    (#14), so the UI can present/disable the real-run path without hard-coding the role set.
    """
    return {
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "can_real_run": user.role.lower() in _real_run_roles(),
    }


@router.post("/auth/logout")
def logout(
    user: User = Depends(get_current_user),
    _csrf: None = Depends(require_csrf),
) -> Response:
    """Clear the session + CSRF cookies (state-changing → CSRF-guarded)."""
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    resp.delete_cookie(CSRF_COOKIE, path="/")
    return resp
