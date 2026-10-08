"""Require a valid Cloudflare Access token for admin requests.

When ``CF_ACCESS_TEAM_DOMAIN`` and ``CF_ACCESS_AUD`` are both set, an admin
session only counts on a request that carries a valid Access token in the
``Cf-Access-Jwt-Assertion`` header: signed by the team's key, for this
application's audience, issued by the team domain and not expired. A session
cookie on its own grants nothing, so a request that reaches Wizarr without
passing through Access is never treated as an admin.

Access is an auth source like LDAP: the token's email signs in as the admin
account linked to it (``auth_source`` ``cloudflare_access``, ``external_id``
the email). An email no account is linked to is not an admin, and a session
only counts while the request's token still maps to that same account.

Validation follows Cloudflare's guidance: read the header rather than the
``CF_Authorization`` cookie, and pick the signing key by the token's ``kid``
from ``https://<team>/cdn-cgi/access/certs``.
"""

import logging
import os
import threading
from typing import Any

import jwt
from flask import g, request

JWT_HEADER = "Cf-Access-Jwt-Assertion"
AUTH_SOURCE = "cloudflare_access"

_clients: dict[str, jwt.PyJWKClient] = {}
_clients_lock = threading.Lock()
_UNSET = object()
_CLAIMS_KEY = "wizarr.cf_access_claims"


def _config() -> tuple[str, str] | None:
    team = os.getenv("CF_ACCESS_TEAM_DOMAIN", "").strip()
    team = team.removeprefix("https://").removeprefix("http://").rstrip("/")
    audience = os.getenv("CF_ACCESS_AUD", "").strip()
    if not team or not audience:
        return None
    return team, audience


def enabled() -> bool:
    """True when admin access requires a Cloudflare Access token."""
    return _config() is not None


def init_app(app) -> None:
    @app.before_request
    def _reload_user_each_request():
        # Flask-Login caches the user on g, which lives as long as the app
        # context. Drop it so every request re-runs the user loader and so
        # re-checks this request's token, however long the context lives.
        if enabled():
            g.pop("_login_user", None)


def _jwks_client(team: str) -> jwt.PyJWKClient:
    with _clients_lock:
        client = _clients.get(team)
        if client is None:
            client = jwt.PyJWKClient(f"https://{team}/cdn-cgi/access/certs", timeout=5)
            _clients[team] = client
        return client


def _verify(token: str, team: str, audience: str) -> dict[str, Any] | None:
    try:
        signing_key = _jwks_client(team).get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=f"https://{team}",
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        logging.warning("Rejected Cloudflare Access token: %s", exc)
        return None


def verified_claims() -> dict[str, Any] | None:
    """Claims of this request's valid Access token, or None.

    The result is kept on the request itself (its WSGI environ), so the session
    check and the login route don't verify the same token twice. It must not
    live on ``g``, which belongs to the app context and can outlive a request.
    """
    cached = request.environ.get(_CLAIMS_KEY, _UNSET)
    if cached is not _UNSET:
        return cached

    claims = None
    config = _config()
    token = request.headers.get(JWT_HEADER)
    if config is not None and token:
        claims = _verify(token, *config)
    request.environ[_CLAIMS_KEY] = claims
    return claims


def verified_email() -> str | None:
    """The valid token's email, lowercased. None for a service token, which
    carries no email and so can't be matched to an admin."""
    email = (verified_claims() or {}).get("email")
    if not isinstance(email, str) or not email.strip():
        return None
    return email.strip().lower()


def account_for_request():
    """The admin account linked to this request's Access identity, or None."""
    email = verified_email()
    if email is None:
        return None
    from app.models import AdminAccount

    return AdminAccount.query.filter_by(
        auth_source=AUTH_SOURCE, external_id=email
    ).first()


def status() -> dict[str, Any]:
    """What Settings shows about Access sign-in. Read-only by design: the two
    environment variables are the only switch, so an admin session (or a
    stolen one) can't turn the check off from the app."""
    from app.models import AdminAccount

    config = _config()
    linked = AdminAccount.query.filter_by(auth_source=AUTH_SOURCE).count()
    return {
        "enabled": config is not None,
        "team": config[0] if config else None,
        "signed_in_as": verified_email() if config else None,
        "linked": linked,
        "unlinked": AdminAccount.query.count() - linked,
        "builtin_auth_disabled": os.getenv("DISABLE_BUILTIN_AUTH", "").lower()
        == "true",
    }
