"""Provisioning hook: grant access through a small signed HTTP endpoint.

For a service where access is a list membership rather than an account Wizarr
can create directly (a group on an identity provider, an allow-list in front
of an app), the operator runs a small endpoint that does the change, and
Wizarr calls it. The invitee gives only an email address; that address is what
gets granted.

The contract:

- ``POST <server url>`` with JSON ``{"verb": "grant" | "disable" | "status",
  "email": "<address>"}``.
- Signed with HMAC-SHA256 over ``f"{timestamp}.{body}"`` using the server's API
  key as the shared secret, in the headers ``<prefix>Timestamp`` (unix seconds)
  and ``<prefix>Signature`` (hex). The prefix is ``X-Wizarr-`` unless
  ``PROVISIONING_HOOK_HEADER_PREFIX`` says otherwise. Every request carries a
  fresh timestamp, so the endpoint can refuse a replayed signature.
- The endpoint answers JSON with ``"ok": true`` on success. Any non-2xx status
  or ``"ok": false`` is a failure.
- ``GET /healthz`` on the same origin answers 200 for the connection check.

Expiry never deletes: ``delete_user`` sends ``disable``, which the endpoint is
expected to make reversible. There is no remote user list, so the users shown
are Wizarr's own rows and a sync never prunes them.
"""

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any
from urllib.parse import urlsplit

import requests

from app.extensions import db
from app.models import Invitation, User
from app.services.invites import is_invite_valid
from app.services.media.client_base import (
    EMAIL_RE,
    ClientCapabilities,
    RestApiMixin,
    register_media_client,
)

DEFAULT_HEADER_PREFIX = "X-Wizarr-"
# The endpoint may run a slow command behind it (the reference one caps at 90 s).
REQUEST_TIMEOUT = 100


def _header_prefix() -> str:
    return os.getenv("PROVISIONING_HOOK_HEADER_PREFIX", "").strip() or (
        DEFAULT_HEADER_PREFIX
    )


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """Hex HMAC-SHA256 of ``f"{timestamp}.{body}"`` with the shared secret."""
    message = timestamp.encode() + b"." + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


@register_media_client("provisioning_hook")
class ProvisioningHookClient(RestApiMixin):
    """Grants and removes access by calling an operator-run signed endpoint."""

    # The invitee gives only an address; expiry disables, never deletes.
    capabilities = ClientCapabilities(join_fields=("email",), disable=True)

    # ------------------------------------------------------------------
    # The endpoint
    # ------------------------------------------------------------------

    def _call(self, verb: str, email: str) -> tuple[bool, dict[str, Any]]:
        """POST one signed request. Returns (ok, reply); never raises."""
        if not self.url or not self.token:
            return False, {"error": "Provisioning hook URL or secret is not set"}

        body = json.dumps({"verb": verb, "email": email}).encode()
        timestamp = str(int(time.time()))
        prefix = _header_prefix()
        headers = {
            "Content-Type": "application/json",
            f"{prefix}Timestamp": timestamp,
            f"{prefix}Signature": sign(self.token, timestamp, body),
        }
        try:
            response = requests.post(
                self.url, data=body, headers=headers, timeout=REQUEST_TIMEOUT
            )
        except requests.RequestException as exc:
            logging.warning("Provisioning hook %s %s failed: %s", verb, email, exc)
            return False, {"error": str(exc)}

        try:
            reply = response.json()
        except ValueError:
            reply = {"error": response.text[:200]}
        if not isinstance(reply, dict):
            reply = {"error": "unexpected reply"}

        ok = response.ok and reply.get("ok") is True
        if not ok:
            logging.warning(
                "Provisioning hook %s %s refused: HTTP %s %s",
                verb,
                email,
                response.status_code,
                reply.get("error") or reply.get("output") or "",
            )
        return ok, reply

    @classmethod
    def check_connection(cls, url: str, token: str) -> tuple[bool, str]:
        """GET /healthz on the endpoint's origin."""
        if not token:
            return False, "The shared secret (API key) is required"
        parts = urlsplit(url or "")
        if not parts.scheme or not parts.netloc:
            return False, "Enter the full endpoint URL, e.g. http://host:port/v1/access"
        try:
            response = requests.get(
                f"{parts.scheme}://{parts.netloc}/healthz", timeout=10
            )
        except requests.RequestException as exc:
            return False, f"Could not reach the provisioning hook: {exc}"
        if response.status_code != 200:
            return (
                False,
                f"Provisioning hook health check returned {response.status_code}",
            )
        return True, ""

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def create_user(self, email: str, *_args, **_kwargs) -> str:
        ok, reply = self._call("grant", email)
        if not ok:
            raise RuntimeError(reply.get("error") or "Provisioning hook refused grant")
        return email

    def update_user(self, *_args, **_kwargs) -> dict[str, Any]:
        return {}

    def enable_user(self, user_id: str) -> bool:
        return self._call("grant", user_id)[0]

    def disable_user(self, user_id: str) -> bool:
        return self._call("disable", user_id)[0]

    def delete_user(self, user_id: str) -> None:
        """Expiry and removal disable; the endpoint keeps it reversible."""
        ok, reply = self._call("disable", user_id)
        if not ok:
            raise RuntimeError(
                reply.get("error") or "Provisioning hook refused disable"
            )

    def get_user(self, user_id: str) -> dict[str, Any]:
        ok, reply = self._call("status", user_id)
        return {"email": user_id, "ok": ok, "status": reply.get("output", "")}

    def list_users(self) -> list[User]:
        """Wizarr's own rows: the endpoint has no user list to sync from."""
        return User.query.filter_by(server_id=getattr(self, "server_id", None)).all()

    # ------------------------------------------------------------------
    # Nothing to browse or stream
    # ------------------------------------------------------------------

    def libraries(self) -> dict[str, str]:
        return {}

    def scan_libraries(
        self,
        url: str | None = None,  # noqa: ARG002
        token: str | None = None,  # noqa: ARG002
    ) -> dict[str, str]:
        return {}

    def now_playing(self) -> list[dict]:
        return []

    def statistics(self) -> dict[str, Any]:
        return {
            "library_stats": {},
            "user_stats": {"total_users": self.get_user_count(), "active_sessions": 0},
            "server_stats": {},
            "content_stats": {},
        }

    # ------------------------------------------------------------------
    # Public sign-up via invites
    # ------------------------------------------------------------------

    def _do_join(
        self,
        username: str,  # noqa: ARG002
        password: str,  # noqa: ARG002
        confirm: str,  # noqa: ARG002
        email: str,
        code: str,
    ) -> tuple[bool, str]:
        email = (email or "").strip()
        if not EMAIL_RE.fullmatch(email):
            return False, "Invalid e-mail address."

        ok, msg = is_invite_valid(code)
        if not ok:
            return False, msg

        server_id = getattr(self, "server_id", None)
        if User.query.filter_by(email=email, server_id=server_id).first():
            return False, "This address already has access."

        granted, _reply = self._call("grant", email)
        if not granted:
            return False, "Access could not be granted. Please contact the admin."

        try:
            self._record_invited_user(
                username=email,
                email=email,
                token=email,
                code=code,
                invitation=Invitation.query.filter_by(code=code).first(),
            )
        except Exception:
            logging.error(
                "Provisioning hook: granted %s but saving failed", email, exc_info=True
            )
            db.session.rollback()
            # Undo the grant so access never exists without an expiry behind it
            self._call("disable", email)
            return False, "An unexpected error occurred."

        return True, ""
