"""
The Navidrome client creates, limits and removes users through Navidrome's
own API.

Navidrome answers 501 to Subsonic ``createUser``, ``deleteUser`` and
``updateUser`` (``server/subsonic/api.go``), so the client could never create
a user. The native API can: ``POST /auth/login`` for a token, ``POST /api/user``
to create, ``PUT /api/user/{id}/library`` to set which libraries the user sees,
``DELETE /api/user/{id}`` to remove. A new user starts with whatever libraries
are marked default for new users, so the invite's libraries are always set
explicitly. The admin sign-in comes from the server's saved credentials, not a
hard-coded name.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.models import Invitation, Library, MediaServer, User, invite_libraries
from app.services.media import client_base
from app.services.media.client_base import (
    capabilities_for,
    pack_credentials,
    unpack_credentials,
)
from app.services.media.navidrome import NavidromeClient

LIBRARIES = [
    {"id": 1, "name": "Music Library"},
    {"id": 2, "name": "Liked Songs"},
    {"id": 3, "name": "Yoga Class Music"},
]


class FakeNavidrome:
    """Just enough of Navidrome's native API, shaped like the real one."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.users: dict[str, dict] = {}
        self.libraries_of: dict[str, list[int]] = {}
        self.put_bodies: list[dict] = []

    def request(self, method, url, headers=None, timeout=None, **kwargs):
        headers = headers or {}
        path = "/" + url.split("//", 1)[1].split("/", 1)[1]
        self.calls.append((method, path, {"headers": headers, **kwargs}))
        body = kwargs.get("json") or {}
        if path == "/auth/login":
            return _reply(200, {"token": "jwt-1", "id": "admin-id"})
        if headers.get("X-ND-Authorization") != "Bearer jwt-1":
            return _reply(401, {"error": "unauthorized"})
        if (method, path) == ("GET", "/api/library"):
            return _reply(200, LIBRARIES)
        if (method, path) == ("POST", "/api/user"):
            uid = f"user-{len(self.users) + 1}"
            self.users[uid] = body
            self.libraries_of[uid] = [1]  # library 1 is "default for new users"
            return _reply(200, {"id": uid})
        if method == "PUT" and path.endswith("/library"):
            uid = path.split("/")[3]
            self.libraries_of[uid] = body["libraryIds"]
            return _reply(
                200, [lib for lib in LIBRARIES if lib["id"] in body["libraryIds"]]
            )
        if (
            method == "GET"
            and path.startswith("/api/user/")
            and not path.endswith("/library")
        ):
            uid = path.rsplit("/", 1)[1]
            return _reply(
                200,
                {
                    "id": uid,
                    "userName": "pat",
                    "name": "pat",
                    "email": self.users[uid]["email"],
                    "isAdmin": False,
                },
            )
        if (
            method == "PUT"
            and path.startswith("/api/user/")
            and not path.endswith("/library")
        ):
            uid = path.rsplit("/", 1)[1]
            self.users[uid].update(body)
            self.put_bodies.append(body)
            return _reply(200, {})
        if method == "DELETE" and path.startswith("/api/user/"):
            self.users.pop(path.rsplit("/", 1)[1], None)
            return _reply(200, {})
        return _reply(404, {})


def _reply(status: int, payload):
    response = Mock()
    response.status_code = status
    response.json.return_value = payload
    response.text = json.dumps(payload)

    def raise_for_status():
        if status >= 400:
            import requests

            raise requests.HTTPError(response=response)

    response.raise_for_status = raise_for_status
    return response


@pytest.fixture
def fake(monkeypatch):
    NavidromeClient._tokens.clear()
    nav = FakeNavidrome()
    monkeypatch.setattr(
        client_base, "get_http_session", lambda: SimpleNamespace(request=nav.request)
    )
    return nav


@pytest.fixture
def server(session):
    row = MediaServer(
        name="Music",
        server_type="navidrome",
        url="http://nav.local:4533",
        api_key=pack_credentials("wizarr", "s3cret"),
    )
    session.add(row)
    session.flush()
    for lib in LIBRARIES:
        session.add(
            Library(
                external_id=str(lib["id"]),
                name=lib["name"],
                server_id=row.id,
                enabled=True,
            )
        )
    session.commit()
    return row


def _invite(session, server, picked: list[str], code: str) -> Invitation:
    invitation = Invitation(code=code, unlimited=False)
    session.add(invitation)
    invitation.servers.append(server)
    session.flush()
    # An earlier test can leave invite_libraries rows behind for the same invite id
    session.execute(
        invite_libraries.delete().where(invite_libraries.c.invite_id == invitation.id)
    )
    invitation.libraries.extend(
        lib
        for lib in Library.query.filter_by(server_id=server.id).all()
        if lib.external_id in picked
    )
    session.add(invitation)
    session.commit()
    return invitation


def test_credentials_round_trip_and_match_the_romm_format():
    packed = pack_credentials("wizarr", "pa:ss")
    assert unpack_credentials(packed) == ("wizarr", "pa:ss")
    assert packed == "d2l6YXJyOnBhOnNz"  # base64 of "wizarr:pa:ss", as RomM stores it


@pytest.mark.parametrize("bad", ["", None, "not base64!", "bm9jb2xvbg=="])
def test_an_unpacked_key_is_refused(bad):
    with pytest.raises(ValueError, match="packed username and password"):
        unpack_credentials(bad)


def test_navidrome_declares_what_it_needs():
    caps = capabilities_for("navidrome")
    assert caps.credentials == ("username", "password")
    assert caps.library_selection is True
    assert caps.disable is False  # Navidrome has no disable, so expiry deletes


def test_sign_in_uses_the_saved_username(fake, server):
    NavidromeClient(server).create_user("pat", "pw-123456", email="pat@example.com")

    login = next(c for c in fake.calls if c[1] == "/auth/login")
    assert login[2]["json"] == {"username": "wizarr", "password": "s3cret"}


def test_create_user_uses_the_native_api_and_returns_the_id(fake, server):
    uid = NavidromeClient(server).create_user(
        "pat", "pw-123456", email="pat@example.com"
    )

    assert uid == "user-1"
    created = fake.users[uid]
    assert created["userName"] == "pat"
    assert created["email"] == "pat@example.com"
    assert created["isAdmin"] is False


def test_join_gives_only_the_libraries_the_invite_picks(session, fake, server):
    _invite(session, server, picked=["1", "2"], code="MUSICCODE1")

    ok, message = NavidromeClient(server)._do_join(
        "pat", "pw-123456", "pw-123456", "pat@example.com", "MUSICCODE1"
    )

    assert (ok, message) == (True, "")
    assert fake.libraries_of["user-1"] == [1, 2]
    user = User.query.filter_by(username="pat").one()
    assert user.token == "user-1"
    assert user.server_id == server.id


def test_join_that_picks_no_libraries_sets_every_library_explicitly(
    session, fake, server
):
    _invite(session, server, picked=[], code="MUSICCODE2")

    NavidromeClient(server)._do_join(
        "pat", "pw-123456", "pw-123456", "pat@example.com", "MUSICCODE2"
    )

    assert fake.libraries_of["user-1"] == [1, 2, 3]


def test_delete_removes_the_user_by_id(fake, server):
    client = NavidromeClient(server)
    uid = client.create_user("pat", "pw-123456", email="pat@example.com")

    client.delete_user(uid)

    assert uid not in fake.users


def _login_reply(status, payload=None):
    response = Mock()
    response.status_code = status
    response.url = "http://nav.local/auth/login"
    response.json.return_value = payload or {}
    return response


@pytest.mark.parametrize(
    ("reply", "ok", "words"),
    [
        (_login_reply(200, {"token": "t", "isAdmin": True}), True, ""),
        (_login_reply(200, {"token": "t", "isAdmin": False}), False, "not an admin"),
        (_login_reply(401), False, "refused"),
    ],
)
def test_the_server_check_signs_in_as_an_admin(monkeypatch, reply, ok, words):
    from app.services import servers

    post = Mock(return_value=reply)
    monkeypatch.setattr(servers.requests, "post", post)

    result, message = servers.check_navidrome(
        "http://nav.local", pack_credentials("wizarr", "s3cret")
    )

    assert result is ok
    assert words in message
    assert post.call_args.kwargs["json"] == {"username": "wizarr", "password": "s3cret"}


def test_the_server_check_asks_for_both_when_the_key_is_not_packed():
    from app.services import servers

    ok, message = servers.check_navidrome("http://nav.local", "plain")

    assert ok is False
    assert "username and password" in message


def test_a_key_saved_before_the_form_took_a_username_signs_in_as_admin(session, fake):
    row = MediaServer(
        name="Old",
        server_type="navidrome",
        url="http://nav.local:4533",
        api_key="plain",
    )
    session.add(row)
    session.commit()

    NavidromeClient(row).libraries()

    login = next(c for c in fake.calls if c[1] == "/auth/login")
    assert login[2]["json"] == {"username": "admin", "password": "plain"}
