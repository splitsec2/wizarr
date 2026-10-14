from __future__ import annotations

import hashlib
import logging
import random
import string
from typing import TYPE_CHECKING, Any, ClassVar

import structlog
from sqlalchemy import func, or_

from app.extensions import db
from app.models import Invitation, User
from app.services.invites import is_invite_valid
from app.services.media.utils import StandardizedPermissions

from .client_base import (
    ClientCapabilities,
    RestApiMixin,
    register_media_client,
    unpack_credentials,
)

if TYPE_CHECKING:
    from app.services.media.user_details import MediaUserDetails


@register_media_client("navidrome")
class NavidromeClient(RestApiMixin):
    """Navidrome wrapper: Navidrome's own API for users and libraries, Subsonic for playback."""

    # Navidrome has no disable, so expiry deletes. The admin sign-in is a username and
    # password (packed in the API key field), and a user can be limited to some libraries.
    capabilities = ClientCapabilities(
        credentials=("username", "password"), library_selection=True
    )

    @classmethod
    def check_connection(cls, url: str, token: str) -> tuple[bool, str]:
        from app.services.servers import check_navidrome

        return check_navidrome(url, token)

    #: API prefix for Subsonic/OpenSubsonic endpoints
    API_PREFIX = "/rest"

    def __init__(self, *args, **kwargs):
        # Provide defaults for legacy compatibility
        kwargs.setdefault("url_key", "server_url")
        kwargs.setdefault("token_key", "api_key")

        super().__init__(*args, **kwargs)

        # Normalize URL
        if self.url:
            self.url = self.url.rstrip("/")

    #: The username Wizarr signed in with before the server form took one
    LEGACY_USERNAME = "admin"

    def _credentials(self) -> tuple[str, str]:
        """The admin (username, password) saved for this server.

        A key that isn't a packed username and password is a server saved before
        the form took a username: the key is the password of the user ``admin``.
        """
        try:
            return unpack_credentials(self.token)
        except ValueError:
            return self.LEGACY_USERNAME, self.token or ""

    def _generate_auth_params(self) -> dict[str, str]:
        """Authentication parameters for Subsonic API calls (salted hash)."""
        username, password = self._credentials()
        salt = "".join(random.choices(string.ascii_letters + string.digits, k=6))
        token_hash = hashlib.md5((password + salt).encode()).hexdigest()  # noqa: S324  # Required by Subsonic API specification
        return {
            "u": username,
            "t": token_hash,
            "s": salt,
            "v": "1.16.1",  # Supported API version
            "c": "wizarr",  # Client identifier
            "f": "json",  # Response format
        }

    def _subsonic_request(self, endpoint: str, params: dict | None = None) -> dict:
        """Make a request to the Subsonic API with proper authentication."""
        auth_params = self._generate_auth_params()
        if params:
            auth_params.update(params)

        response = self.get(f"{self.API_PREFIX}/{endpoint}", params=auth_params)
        response.raise_for_status()

        data = response.json()

        # Check for Subsonic API errors
        subsonic_response = data.get("subsonic-response", {})
        if subsonic_response.get("status") != "ok":
            error = subsonic_response.get("error", {})
            raise Exception(
                f"Subsonic API error: {error.get('message', 'Unknown error')}"
            )

        return subsonic_response

    #: Navidrome's own API, the only one that can manage users and their libraries
    NATIVE_PREFIX = "/api"

    def _sign_in(self) -> str:
        """Sign in as the saved admin and return the API token."""
        username, password = self._credentials()
        response = self.post(
            "/auth/login",
            json={"username": username, "password": password},
            headers={"Content-Type": "application/json"},
        )
        return response.json()["token"]

    #: API tokens by (server URL, username). Wizarr makes a new client for each
    #: operation and Navidrome rate-limits sign-ins, so a token is reused until it fails.
    _tokens: ClassVar[dict[tuple[str, str], str]] = {}

    def _native(self, method: str, path: str, **kwargs):
        """Call Navidrome's own API as the admin, signing in again once if the token expired."""
        key = (self.url or "", self._credentials()[0])
        token = self._tokens.get(key) or self._sign_in()
        for attempt in (1, 2):
            try:
                self._tokens[key] = token
                return self._request(
                    method,
                    f"{self.NATIVE_PREFIX}{path}",
                    headers={
                        "Content-Type": "application/json",
                        "X-ND-Authorization": f"Bearer {token}",
                    },
                    **kwargs,
                )
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status != 401 or attempt == 2:
                    raise
                token = self._sign_in()
        raise RuntimeError("unreachable")  # pragma: no cover

    def validate_connection(self) -> tuple[bool, str]:
        """Validate connection to Navidrome server."""
        try:
            # Test connection with ping endpoint
            result = self._subsonic_request("ping")
            if result.get("status") == "ok":
                return True, "Connection successful"
            return False, "Server did not respond properly"
        except Exception as exc:
            logging.error("Navidrome: connection validation failed – %s", exc)
            return False, f"Connection failed: {exc!s}"

    def libraries(self) -> dict[str, str]:
        """Return mapping of library_id → display_name."""
        try:
            return {
                str(lib["id"]): lib["name"]
                for lib in self._native("GET", "/library").json()
            }
        except Exception as exc:
            logging.warning("Navidrome: failed to fetch libraries – %s", exc)
            return {}

    def scan_libraries(
        self, url: str | None = None, token: str | None = None
    ) -> dict[str, str]:
        """Scan available libraries on this Navidrome server."""
        try:
            if url and token:
                # Create temporary client for scanning
                temp_client = NavidromeClient()
                temp_client.url = url.rstrip("/")
                temp_client.token = token
                return {
                    name: lib_id for lib_id, name in temp_client.libraries().items()
                }
            # Use saved credentials
            return {name: lib_id for lib_id, name in self.libraries().items()}
        except Exception as exc:
            logging.warning("Navidrome: failed to scan libraries – %s", exc)
            return {}

    def list_users(self) -> list[User]:
        """Read users from Navidrome and reflect them locally."""
        server_id = getattr(self, "server_id", None)
        if server_id is None:
            return []

        try:
            users_data = self._native("GET", "/user").json()
        except Exception as exc:
            logging.warning("Navidrome: failed to list users – %s", exc)
            return []

        users_by_name = {u["userName"]: u for u in users_data}

        known_users = User.query.filter(User.server_id == server_id).all()
        if self._skip_prune_on_empty_remote(not users_by_name, known_users):
            return known_users

        try:
            # Use the rows that are already loaded, not one query for each
            # remote user. The first local row with a username is the match.
            users: list[User] = []
            rows_by_username: dict[str, User] = {}
            for local in known_users:
                if local.username in users_by_name:
                    users.append(local)
                    rows_by_username.setdefault(local.username, local)
                else:
                    # Remove users that no longer exist upstream
                    db.session.delete(local)

            # Add new users or update existing ones
            for username, remote in users_by_name.items():
                db_row = rows_by_username.get(username)
                if db_row is None:
                    db_row = User(
                        token=remote["id"],  # Navidrome's own user id
                        username=username,
                        email=remote.get("email", ""),
                        code="empty",
                        server_id=server_id,
                    )
                    db.session.add(db_row)
                    users.append(db_row)
                else:
                    db_row.email = remote.get("email", db_row.email)
                    db_row.token = remote["id"]

            # Enhance users with policy data from Navidrome
            for user in users:
                # Use standardized permissions helper for consistency
                permissions = StandardizedPermissions.for_navidrome(
                    {"adminRole": users_by_name[user.username].get("isAdmin", False)}
                )

                # Update standardized User model columns
                user.allow_downloads = permissions.allow_downloads
                user.allow_live_tv = permissions.allow_live_tv
                user.is_admin = permissions.is_admin

            # Single commit for all user and metadata updates
            db.session.commit()

        except Exception as exc:
            logging.error("Navidrome: failed to sync users – %s", exc)
            db.session.rollback()
            return []
        return users

    def create_user(
        self,
        username: str,
        password: str,
        email: str,
        *,
        is_admin: bool = False,
        allow_downloads: bool = True,  # noqa: ARG002  # Navidrome has no per-user download role
    ) -> str:
        """Create a user and return Navidrome's id for it.

        A new user starts with the libraries marked default for new users, so
        call ``grant_invite_libraries`` or ``update_user_libraries`` next.
        """
        try:
            response = self._native(
                "POST",
                "/user",
                json={
                    "userName": username,
                    "name": username,
                    "email": email,
                    "password": password,
                    "isAdmin": is_admin,
                },
            )
            return response.json()["id"]
        except Exception as exc:
            logging.error("Navidrome: failed to create user – %s", exc)
            raise

    def update_user(self, user_id: str, payload: dict[str, Any]):
        """Update a user's e-mail.

        Navidrome replaces the whole user on a PUT, so the current fields are
        read first and sent back with the change.
        """
        try:
            if "email" in payload:
                current = self._native("GET", f"/user/{user_id}").json()
                body = {
                    key: current[key]
                    for key in ("userName", "name", "isAdmin", "scrobbleFilter")
                    if key in current
                }
                body["email"] = payload["email"]
                self._native("PUT", f"/user/{user_id}", json=body)
        except Exception as exc:
            logging.error("Navidrome: failed to update user %s – %s", user_id, exc)
            raise

    def update_user_libraries(
        self, _user_identifier: str, _library_names: list[str] | None
    ) -> bool:
        """Limit a user to the named libraries, or give every library when None."""
        try:
            by_name = {name: lib_id for lib_id, name in self.libraries().items()}
            if _library_names is None:
                ids = list(by_name.values())
            else:
                ids = [by_name[name] for name in _library_names if name in by_name]
            return self._set_libraries(_user_identifier, ids)
        except Exception as exc:
            logging.error("Navidrome: failed to set libraries – %s", exc)
            return False

    def _set_libraries(self, user_id: str, library_ids: list[str]) -> bool:
        self._native(
            "PUT",
            f"/user/{user_id}/library",
            json={"libraryIds": [int(i) for i in library_ids]},
        )
        return True

    def grant_invite_libraries(self, user_id: str, invitation) -> None:
        """Give *user_id* the invitation's libraries on this server.

        Navidrome needs the list spelled out, so an invite that picks none, or
        every one, gets every library the server has.
        """
        ids = self.invite_library_ids(invitation) or list(self.libraries())
        logging.info("Navidrome: granting user %s libraries %s", user_id, ids)
        self._set_libraries(user_id, ids)

    def enable_user(self, user_id: str) -> bool:  # noqa: ARG002
        """Enable a user account on Navidrome.

        Args:
            user_id: The user's Navidrome ID (unused - Navidrome doesn't support enable/disable)

        Returns:
            bool: True if the user was successfully enabled, False otherwise
        """
        try:
            # Navidrome doesn't have a direct enable feature
            # Return False to indicate this operation is not supported
            structlog.get_logger().warning(
                "Navidrome does not support enabling users. They need to be given library access."
            )
            return False
        except Exception as e:
            structlog.get_logger().error(f"Failed to enable Navidrome user: {e}")
            return False

    def disable_user(self, user_id: str) -> bool:  # noqa: ARG002
        """Disable a user account on Navidrome.

        Args:
            user_id: The user's Navidrome usernameFailed to disable

        Returns:
            bool: True if the user was successfully disabled, False otherwise
        """
        try:
            # Navidrome doesn't have a disable feature, we can only delete users
            # Return False to indicate this operation is not supported
            structlog.get_logger().warning(
                "Navidrome does not support disabling users, only deletion"
            )
            return False
        except Exception as e:
            structlog.get_logger().error(f"Failed to disable Navidrome user: {e}")
            return False

    def delete_user(self, user_id: str):
        """Delete a user permanently from Navidrome."""
        try:
            self._native("DELETE", f"/user/{user_id}")
        except Exception as exc:
            logging.error("Navidrome: failed to delete user %s – %s", user_id, exc)
            raise

    def get_user(self, user_id: str):
        """Get user info in legacy format for backward compatibility."""
        details = self.get_user_details(user_id)
        return {
            "username": details.username,
            "email": details.email,
            "isActive": details.is_enabled,
            "createdAt": int(details.created_at.timestamp() * 1000)
            if details.created_at
            else None,
            "lastSeen": int(details.last_active.timestamp() * 1000)
            if details.last_active
            else None,
            "permissions": {"admin": details.is_admin},
        }

    def get_user_details(self, user_identifier: str | int) -> MediaUserDetails:
        """Get detailed user information in standardized format."""
        user_id = str(user_identifier)
        from app.services.media.utils import (
            LibraryAccessHelper,
            StandardizedPermissions,
            create_standardized_user_details,
        )

        raw_user = self._native("GET", f"/user/{user_id}").json()
        permissions = StandardizedPermissions.for_navidrome(
            {"adminRole": raw_user.get("isAdmin", False)}
        )
        libraries = raw_user.get("libraries") or []
        if raw_user.get("isAdmin") or not libraries:
            library_access = LibraryAccessHelper.create_full_access()
        else:
            library_access = LibraryAccessHelper.create_restricted_access(
                [str(lib["id"]) for lib in libraries], getattr(self, "server_id", 0)
            )

        return create_standardized_user_details(
            user_id=user_id,
            username=raw_user.get("userName", user_id),
            email=raw_user.get("email"),
            permissions=permissions,
            library_access=library_access,
            created_at=None,  # Navidrome doesn't expose creation date
            last_active=None,  # Navidrome doesn't expose last seen
            is_enabled=True,  # Navidrome doesn't have disabled users concept
        )

    def _do_join(
        self,
        username: str,
        password: str,
        confirm: str,
        email: str,
        code: str,
    ):
        """Public invite flow for Navidrome users."""
        if not 1 <= len(username) <= 50:
            return False, "Username must be 1-50 characters."
        if not 8 <= len(password) <= 128:
            return False, "Password must be 8-128 characters."
        if password != confirm:
            return False, "Passwords do not match."

        ok, msg = is_invite_valid(code)
        if not ok:
            return False, msg

        server_id = getattr(self, "server_id", None)
        if server_id is None:
            return False, "Server configuration error"

        existing = User.query.filter(
            or_(User.username == username, func.lower(User.email) == (email or "").lower()),
            User.server_id == server_id,
        ).first()
        if existing:
            return False, "User or e-mail already exists."

        try:
            inv = Invitation.query.filter_by(code=code).first()

            # Get download permission from invitation
            allow_downloads = getattr(inv, "allow_downloads", True)
            if allow_downloads is None:
                allow_downloads = True

            # Create user on Navidrome
            user_id = self.create_user(
                username, password, email=email, allow_downloads=allow_downloads
            )
            if not user_id:
                return False, "Failed to create user on server"
            if inv:
                self.grant_invite_libraries(user_id, inv)

            # Calculate expiry
            from app.services.expiry import calculate_user_expiry

            expires = (
                calculate_user_expiry(inv, getattr(self, "server_id", None))
                if inv
                else None
            )

            # Store locally
            local = User(
                token=user_id,
                username=username,
                email=email,
                code=code,
                expires=expires,
                server_id=server_id,
            )
            db.session.add(local)
            db.session.commit()

            return True, ""

        except Exception as exc:
            logging.error("Navidrome join failed: %s", exc)
            db.session.rollback()
            return False, "Failed to create user"

    def now_playing(self) -> list[dict]:
        """Return active sessions from Navidrome.

        Raises when the session list cannot be fetched or read. Then a caller
        can tell a failed poll from a server with no sessions. Returns [] only
        when the server reports no sessions.
        """
        result = self._subsonic_request("getNowPlaying")
        now_playing_data = result.get("nowPlaying") or {}
        if not isinstance(now_playing_data, dict):
            raise ValueError("Navidrome returned an invalid now playing list")
        entries = now_playing_data.get("entry", [])

        # Handle single entry response (not in array)
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            raise ValueError("Navidrome returned an invalid now playing list")

        active = []
        for entry in entries:
            try:
                session_data = self._now_playing_entry(entry)
            except Exception as exc:
                # One bad entry must not hide the other sessions.
                logging.warning("Navidrome: failed to read a session – %s", exc)
                continue
            active.append(session_data)

        return active

    def _now_playing_entry(self, entry: dict) -> dict:
        """Convert one getNowPlaying entry to a now-playing dict."""
        session_data = {
            "user_name": entry.get("username", "Unknown"),
            "media_title": entry.get("title", "Unknown Track"),
            "media_type": "track",
            "progress": 0.0,  # Navidrome doesn't provide progress in now playing
            "state": "playing",
            "session_id": entry.get("id", ""),
            "client": entry.get("playerName", "Unknown"),
            "device_name": entry.get("playerName", "Unknown Device"),
            "position_ms": 0,
            "duration_ms": (entry.get("duration", 0) * 1000)
            if entry.get("duration")
            else 0,
            "artwork_url": f"{self.url}{self.API_PREFIX}/getCoverArt?id={entry.get('coverArt', '')}&"
            + "&".join(f"{k}={v}" for k, v in self._generate_auth_params().items())
            if entry.get("coverArt")
            else None,
            "thumbnail_url": None,
            "transcoding": {"is_transcoding": False, "direct_play": True},
        }

        # Add artist and album info if available
        if entry.get("artist"):
            session_data["artist"] = entry["artist"]
        if entry.get("album"):
            session_data["album"] = entry["album"]

        return session_data

    def statistics(self):
        """Return server statistics for Navidrome."""
        try:
            stats = {
                "library_stats": {},
                "user_stats": {},
                "server_stats": {},
                "content_stats": {},
            }

            # Get user count
            try:
                total_users = len(self._native("GET", "/user").json())
            except Exception as exc:
                logging.error("Navidrome statistics: failed to fetch users – %s", exc)
                total_users = 0

            # Get active sessions
            try:
                active_sessions = self.now_playing()
            except Exception as exc:
                logging.error(
                    "Navidrome statistics: failed to calculate active sessions – %s",
                    exc,
                )
                active_sessions = []

            stats["user_stats"] = {
                "total_users": total_users,
                "active_sessions": len(active_sessions),
            }

            stats["server_stats"] = {
                "version": "Unknown",  # Navidrome doesn't expose version in Subsonic API
                "transcoding_sessions": 0,  # Not applicable for music streaming
            }

            return stats

        except Exception as e:
            logging.error(f"Failed to get Navidrome statistics: {e}")
            return {
                "library_stats": {},
                "user_stats": {},
                "server_stats": {},
                "content_stats": {},
                "error": str(e),
            }

    def get_user_count(self) -> int:
        """Get lightweight user count from database without triggering sync."""
        try:
            from app.models import MediaServer, User

            if hasattr(self, "server_id") and self.server_id:
                count = User.query.filter_by(server_id=self.server_id).count()
            else:
                # Fallback for legacy settings: find MediaServer for this server type
                servers = MediaServer.query.filter_by(server_type="navidrome").all()
                if servers:
                    server_ids = [s.id for s in servers]
                    count = User.query.filter(User.server_id.in_(server_ids)).count()
                else:
                    # Ultimate fallback: no easy API for user count in Navidrome
                    count = 0
            return count
        except Exception as e:
            logging.error(f"Failed to get Navidrome user count from database: {e}")
            return 0

    def get_server_info(self) -> dict:
        """Get lightweight server information without triggering user sync."""
        try:
            # Navidrome doesn't have traditional sessions/transcoding
            return {
                "version": "Unknown",  # Would need API call to get version
                "transcoding_sessions": 0,  # Navidrome doesn't transcode
                "active_sessions": 0,  # Would need to implement session tracking
            }
        except Exception as e:
            logging.error(f"Failed to get Navidrome server info: {e}")
            return {
                "version": "Unknown",
                "transcoding_sessions": 0,
                "active_sessions": 0,
            }

    def get_readonly_statistics(self) -> dict:
        """Get lightweight statistics without triggering user synchronization."""
        try:
            user_count = self.get_user_count()
            server_info = self.get_server_info()

            return {
                "user_stats": {
                    "total_users": user_count,
                    "active_sessions": server_info.get("active_sessions", 0),
                },
                "server_stats": {
                    "version": server_info.get("version", "Unknown"),
                    "transcoding_sessions": server_info.get("transcoding_sessions", 0),
                },
                "library_stats": {},
                "content_stats": {},
            }
        except Exception as e:
            logging.error(f"Failed to get Navidrome readonly statistics: {e}")
            return {
                "user_stats": {"total_users": 0, "active_sessions": 0},
                "server_stats": {"version": "Unknown", "transcoding_sessions": 0},
                "library_stats": {},
                "content_stats": {},
                "error": str(e),
            }

    def _headers(self) -> dict[str, str]:
        """Return default headers for requests."""
        return {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
