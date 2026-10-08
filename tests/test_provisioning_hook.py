"""
The provisioning hook client grants access by calling a signed HTTP endpoint,
and its invite asks only for an email. A fake endpoint checks each request's
signature the way a real one would.
"""

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import Invitation, MediaServer, User
from app.services.media import provisioning_hook
from app.services.media.service import get_client_for_media_server

SECRET = "s3cret-shared"
URL = "http://hook.local:5760/v1/access"


class FakeHook:
    """Answers like the reference endpoint and records verified calls."""

    def __init__(self, prefix="X-Wizarr-"):
        self.prefix = prefix
        self.calls = []
        self.passwords = []
        self.replies = {}

    def post(self, url, data, headers, timeout):
        assert url == URL
        assert timeout >= 90
        ts = headers[f"{self.prefix}Timestamp"]
        expected = hmac.new(
            SECRET.encode(), ts.encode() + b"." + data, hashlib.sha256
        ).hexdigest()
        assert hmac.compare_digest(headers[f"{self.prefix}Signature"], expected)
        body = json.loads(data)
        self.calls.append((body["verb"], body["email"]))
        if "password" in body:
            self.passwords.append(body["password"])
        status, reply = self.replies.get(body["verb"], (200, {"ok": True}))
        return SimpleNamespace(
            ok=200 <= status < 300,
            status_code=status,
            json=lambda: reply,
            text=json.dumps(reply),
        )

    def get(self, url, timeout):
        assert url == "http://hook.local:5760/healthz"
        return SimpleNamespace(status_code=self.replies.get("healthz", 200))


@pytest.fixture
def hook(monkeypatch):
    fake = FakeHook()
    monkeypatch.setattr(provisioning_hook.requests, "post", fake.post)
    monkeypatch.setattr(provisioning_hook.requests, "get", fake.get)
    monkeypatch.delenv("PROVISIONING_HOOK_HEADER_PREFIX", raising=False)
    return fake


def _server():
    server = MediaServer(
        name="Books", server_type="provisioning_hook", url=URL, api_key=SECRET
    )
    db.session.add(server)
    db.session.commit()
    return server


def _invite(server, code="BOOKS0001"):
    invitation = Invitation(code=code, used=False, unlimited=True, duration="30")
    invitation.servers.append(server)
    db.session.add(invitation)
    db.session.commit()
    return invitation


def _client(server):
    return get_client_for_media_server(server)


def test_join_grants_and_records_the_user(session, hook):
    server = _server()
    _invite(server)

    ok, msg = _client(server).join("", "", "", "reader@example.com", "BOOKS0001")

    assert (ok, msg) == (True, "")
    assert hook.calls == [("grant", "reader@example.com")]
    user = User.query.filter_by(server_id=server.id).one()
    assert user.email == "reader@example.com"
    assert user.token == "reader@example.com"
    assert user.expires is not None


def test_header_prefix_is_configurable(session, hook, monkeypatch):
    server = _server()
    _invite(server)
    hook.prefix = "X-BA-"
    monkeypatch.setenv("PROVISIONING_HOOK_HEADER_PREFIX", "X-BA-")

    assert _client(server).join("", "", "", "reader@example.com", "BOOKS0001")[0]


def test_refused_grant_saves_nothing(session, hook):
    server = _server()
    _invite(server)
    hook.replies["grant"] = (422, {"ok": False, "error": "refused"})

    ok, _msg = _client(server).join("", "", "", "admin@example.com", "BOOKS0001")

    assert ok is False
    assert User.query.filter_by(server_id=server.id).count() == 0


def test_grant_is_undone_when_saving_fails(session, hook, monkeypatch):
    server = _server()
    _invite(server)
    client = _client(server)

    def boom(_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(client, "_create_user_with_identity_linking", boom)

    ok, _msg = client.join("", "", "", "reader@example.com", "BOOKS0001")

    assert ok is False
    assert hook.calls == [
        ("grant", "reader@example.com"),
        ("disable", "reader@example.com"),
    ]


@pytest.mark.parametrize(
    ("email", "code"),
    [("not-an-address", "BOOKS0001"), ("reader@example.com", "NOSUCHCODE")],
)
def test_bad_email_or_invite_never_calls_the_hook(session, hook, email, code):
    server = _server()
    _invite(server)

    assert _client(server).join("", "", "", email, code)[0] is False
    assert hook.calls == []


def test_delete_disables_and_never_deletes(session, hook):
    server = _server()
    client = _client(server)

    client.delete_user("reader@example.com")
    assert hook.calls == [("disable", "reader@example.com")]

    hook.replies["disable"] = (502, {"ok": False, "output": "command failed"})
    with pytest.raises(RuntimeError):
        client.delete_user("reader@example.com")


def test_enable_and_status(session, hook):
    server = _server()
    client = _client(server)
    hook.replies["status"] = (200, {"ok": True, "output": "media-readers: ACTIVE"})

    assert client.enable_user("reader@example.com") is True
    assert client.get_user("reader@example.com")["status"] == "media-readers: ACTIVE"
    assert hook.calls == [
        ("grant", "reader@example.com"),
        ("status", "reader@example.com"),
    ]


def test_user_list_is_local_and_never_pruned(session, hook):
    server = _server()
    db.session.add(
        User(
            username="reader@example.com",
            email="reader@example.com",
            token="reader@example.com",
            code="BOOKS0001",
            server_id=server.id,
        )
    )
    db.session.commit()

    users = _client(server).list_users()

    assert [u.email for u in users] == ["reader@example.com"]
    assert hook.calls == []


@pytest.mark.parametrize(
    ("status", "token", "expected"),
    [(200, SECRET, True), (503, SECRET, False), (200, "", False)],
)
def test_connection_check(hook, status, token, expected):
    hook.replies["healthz"] = status
    ok, _msg = provisioning_hook.ProvisioningHookClient.check_connection(URL, token)
    assert ok is expected


def _verified(client, code, email):
    """As if this browser proved *email* with an emailed code for invite *code*."""
    with client.session_transaction() as sess:
        sess["invite_steps_verified"] = {code.lower(): email}


def _single_use_invite(server, code="BOOKS0001"):
    invitation = Invitation(code=code, used=False, duration="30")
    invitation.servers.append(server)
    db.session.add(invitation)
    db.session.commit()
    return invitation


def test_invite_asks_for_an_email_and_one_strong_password(client, session, hook):
    server = _server()
    _single_use_invite(server)

    assert client.get("/j/BOOKS0001").headers["Location"].endswith("/j/BOOKS0001/steps")
    gate = client.get("/j/BOOKS0001/steps/account")
    assert "/j/BOOKS0001/steps/email" in gate.headers["Location"]
    _verified(client, "BOOKS0001", "reader@example.com")
    body = client.get("/j/BOOKS0001/steps/account").get_data(as_text=True)

    assert 'name="email"' in body
    assert 'name="password"' in body
    assert 'name="username"' not in body
    assert "A short sentence works well." in body


def test_invite_submission_grants_with_the_chosen_password(client, session, hook):
    server = _server()
    _single_use_invite(server)
    _verified(client, "BOOKS0001", "reader@example.com")

    client.post(
        "/j/BOOKS0001/steps/account",
        data={
            "email": "reader@example.com",
            "password": "Robisagreatguyontuesdays",
            "confirm_password": "Robisagreatguyontuesdays",
        },
    )

    assert hook.calls == [("grant", "reader@example.com")]
    assert hook.passwords == ["Robisagreatguyontuesdays"]
    assert User.query.filter_by(server_id=server.id).count() == 1


def test_other_servers_still_ask_for_a_password(client, session, hook):
    server = MediaServer(
        name="Jelly", server_type="jellyfin", url="http://jf.local", api_key="k"
    )
    db.session.add(server)
    db.session.commit()
    _invite(server, code="JELLY0001")

    body = client.get("/j/JELLY0001").get_data(as_text=True)

    assert 'name="username"' in body
    assert 'name="password"' in body
    assert 'name="confirm_password"' in body
