"""People prove their email with a code before Wizarr uses it.

Codes are stored only as a keyed hash, expire, allow five attempts and are
rate limited per address and per invite. A shared invite keeps each verified
person's progress separately; the account step uses the proven address.
"""

import datetime
import re

import pytest

from app.extensions import db
from app.models import (
    AdminAccount,
    EmailCode,
    Invitation,
    InvitationProgress,
    MediaServer,
    Settings,
    User,
)
from app.services import email_codes, invite_steps


@pytest.fixture
def mail(session, monkeypatch):
    """Settings > Email set up, with sending captured instead of done."""
    db.session.add_all(
        [
            AdminAccount(username="admin"),
            Settings(key="admin_username", value="admin"),
            Settings(key="email_host", value="smtp.example.com"),
            Settings(key="email_from", value="wizarr@example.com"),
        ]
    )
    db.session.commit()
    outbox = []

    def send(to, subject, body, config=None):
        outbox.append((to, subject, body))
        return True, ""

    monkeypatch.setattr("app.services.mailer.send", send)
    return outbox


def _server(name, server_type):
    server = MediaServer(
        name=name, server_type=server_type, url="http://x", api_key="k"
    )
    db.session.add(server)
    db.session.flush()
    return server


def _invite(*servers, code="SHARED01", unlimited=True):
    invitation = Invitation(code=code, used=False, unlimited=unlimited)
    for server in servers:
        invitation.servers.append(server)
    db.session.add(invitation)
    db.session.commit()
    return invitation


def _code_in(outbox):
    found = re.search(r"\b(\d{6})\b", outbox[-1][2])
    assert found is not None
    return found.group(1)


# ── the codes ───────────────────────────────────────────────────────────────


def test_a_code_is_mailed_and_only_its_hash_is_kept(app, mail):
    invitation = _invite(_server("A", "audiobookshelf"))
    with app.test_request_context():
        assert email_codes.send_code(invitation, " Pat@Example.com ") == (True, "")
    code = _code_in(mail)
    assert mail[-1][0] == "pat@example.com"
    row = EmailCode.query.one()
    assert code not in row.code_hash
    with app.test_request_context():
        assert email_codes.check_code(invitation, "pat@example.com", code)
    assert EmailCode.query.count() == 0


def test_wrong_codes_count_and_lock_out(app, mail):
    invitation = _invite(_server("A", "audiobookshelf"))
    with app.test_request_context():
        email_codes.send_code(invitation, "pat@example.com")
        code = _code_in(mail)
        for _ in range(5):
            assert not email_codes.check_code(invitation, "pat@example.com", "000000")
        assert not email_codes.check_code(invitation, "pat@example.com", code)


def test_an_expired_code_fails(app, mail):
    invitation = _invite(_server("A", "audiobookshelf"))
    with app.test_request_context():
        email_codes.send_code(invitation, "pat@example.com")
        row = EmailCode.query.one()
        row.expires_at = datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC).replace(
            tzinfo=None
        )
        db.session.commit()
        assert not email_codes.check_code(invitation, "pat@example.com", _code_in(mail))


def test_codes_are_rate_limited_per_address(app, mail):
    invitation = _invite(_server("A", "audiobookshelf"))
    with app.test_request_context():
        for _ in range(3):
            assert email_codes.send_code(invitation, "pat@example.com")[0]
        sent, reason = email_codes.send_code(invitation, "pat@example.com")
    assert not sent
    assert "Too many codes for this address" in reason


def test_codes_are_rate_limited_per_invite(app, mail):
    invitation = _invite(_server("A", "audiobookshelf"))
    with app.test_request_context():
        for i in range(20):
            assert email_codes.send_code(invitation, f"p{i}@example.com")[0]
        assert not email_codes.send_code(invitation, "late@example.com")[0]


# ── the pages ───────────────────────────────────────────────────────────────


def test_shared_invite_starts_with_the_email_gate(client, mail):
    _invite(_server("A", "audiobookshelf"))
    resp = client.get("/j/SHARED01/steps")
    assert "/j/SHARED01/steps/email" in resp.headers["Location"]
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "6-digit code to confirm the address is yours" in page


def test_code_round_trip_unlocks_the_checklist(client, mail):
    _invite(_server("A", "audiobookshelf"))
    sent = client.post(
        "/j/SHARED01/steps/email",
        data={"email": "pat@example.com", "next": "/j/SHARED01/steps"},
    )
    assert "/j/SHARED01/steps/verify" in sent.headers["Location"]
    wrong = client.post(
        "/j/SHARED01/steps/verify", data={"code": "000000", "next": "/j/SHARED01/steps"}
    )
    assert "That code isn" in wrong.get_data(as_text=True)
    right = client.post(
        "/j/SHARED01/steps/verify",
        data={"code": _code_in(mail), "next": "/j/SHARED01/steps"},
    )
    assert right.headers["Location"].endswith("/j/SHARED01/steps")
    assert client.get("/j/SHARED01/steps").status_code == 200


def test_next_never_leaves_the_invite(client, mail):
    _invite(_server("A", "audiobookshelf"))
    client.post(
        "/j/SHARED01/steps/email",
        data={"email": "pat@example.com", "next": "https://evil.example/"},
    )
    resp = client.post(
        "/j/SHARED01/steps/verify",
        data={"code": _code_in(mail), "next": "https://evil.example/"},
    )
    assert resp.headers["Location"].endswith("/j/SHARED01/steps")


def test_without_email_set_up_a_shared_invite_keeps_the_old_flow(session):
    db.session.add(AdminAccount(username="admin"))
    db.session.commit()
    invitation = _invite(_server("A", "audiobookshelf"))
    assert not invite_steps.uses_steps(invitation)


# ── people on a shared invite ───────────────────────────────────────────────


class _Client:
    def __init__(self, server):
        self.server = server

    def join(self, username, password, confirm, email, code):
        db.session.add(
            User(
                token=f"t-{username}",
                username=username,
                email=email,
                code=code,
                server_id=self.server.id,
            )
        )
        db.session.commit()
        return True, "ok"


@pytest.fixture
def fake_media(monkeypatch):
    monkeypatch.setattr(
        "app.services.invitation_flow.workflows.get_client_for_media_server",
        _Client,
    )


def _as(client, email):
    with client.session_transaction() as sess:
        sess["invite_steps_verified"] = {"shared01": email}


def _sign_up(client, username):
    return client.post(
        "/j/SHARED01/steps/account",
        data={
            "username": username,
            "email": "someone-else@example.com",
            "password": "password123",
            "confirm_password": "password123",
        },
    )


def test_each_person_has_their_own_progress(app, mail, fake_media):
    abs_server = _server("A", "audiobookshelf")
    invitation = _invite(abs_server)
    pat, sam = app.test_client(), app.test_client()
    _as(pat, "pat@example.com")
    _as(sam, "sam@example.com")

    _sign_up(pat, "pat")

    assert invite_steps.people(invitation) == {
        "pat@example.com": {abs_server.id: "done"}
    }
    assert 'data-state="done"' in pat.get("/j/SHARED01/steps").get_data(as_text=True)
    assert 'data-state="todo"' in sam.get("/j/SHARED01/steps").get_data(as_text=True)


def test_the_account_uses_the_proven_address(app, mail, fake_media):
    abs_server = _server("A", "audiobookshelf")
    _invite(abs_server)
    pat = app.test_client()
    _as(pat, "pat@example.com")
    _sign_up(pat, "pat")
    assert User.query.filter_by(server_id=abs_server.id).one().email == (
        "pat@example.com"
    )
    row = InvitationProgress.query.one()
    assert row.person == "pat@example.com"
    assert row.user_id == User.query.one().id


def test_shared_invite_with_books_is_allowed_once_email_is_set_up(mail):
    from app.services.invites import create_invite

    books = _server("Books", "provisioning_hook")
    db.session.commit()
    assert create_invite({"server_ids": [str(books.id)], "unlimited": "1"})
