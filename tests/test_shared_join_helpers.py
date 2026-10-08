"""
Every client saves an invited user the same way and checks e-mail addresses
with the same pattern, so the rules live in client_base once.
"""

import datetime

import pytest

from app.extensions import db
from app.models import Invitation, MediaServer, User
from app.services.media.client_base import CLIENTS, EMAIL_RE
from app.services.media.service import EMAIL_RE as SERVICE_EMAIL_RE


@pytest.mark.parametrize(
    ("address", "ok"),
    [
        ("reader@example.com", True),
        ("first.last+tag@sub.example.org", True),
        ("reader@studio.photography", True),  # TLDs longer than 7 letters
        ("a b@example.com", False),  # spaces
        ("reader@example", False),
        ("@example.com", False),
        ("", False),
    ],
)
def test_one_email_pattern(address, ok):
    assert bool(EMAIL_RE.fullmatch(address)) is ok


def test_service_uses_the_same_pattern():
    assert SERVICE_EMAIL_RE is EMAIL_RE


def _setup(server_expires=None):
    server = MediaServer(
        name="J", server_type="jellyfin", url="http://j.local", api_key="k"
    )
    invitation = Invitation(code="SHARED001", used=False, duration="30")
    invitation.servers.append(server)
    db.session.add_all([server, invitation])
    db.session.commit()
    if server_expires is not None:
        from app.services.expiry import set_server_specific_expiry

        set_server_specific_expiry(invitation.id, server.id, server_expires)
    return server, invitation


def _client(server):
    return CLIENTS["jellyfin"](media_server=server)


def test_record_invited_user_uses_the_invite_duration(session):
    server, invitation = _setup()

    user = _client(server)._record_invited_user(
        username="reader",
        email="reader@example.com",
        token="remote-id",
        code="SHARED001",
        invitation=invitation,
    )

    saved = db.session.get(User, user.id)
    assert saved is not None
    assert (saved.username, saved.token, saved.server_id) == (
        "reader",
        "remote-id",
        server.id,
    )
    expected = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=30)
    assert saved.expires is not None
    assert abs(
        saved.expires.replace(tzinfo=datetime.UTC) - expected
    ) < datetime.timedelta(minutes=1)


def test_record_invited_user_prefers_the_per_server_end_date(session):
    end = datetime.datetime(2027, 2, 15, 19, 0, tzinfo=datetime.UTC)
    server, invitation = _setup(server_expires=end)

    user = _client(server)._record_invited_user(
        username="reader",
        email="reader@example.com",
        token="remote-id",
        code="SHARED001",
        invitation=invitation,
    )

    saved = db.session.get(User, user.id)
    assert saved is not None
    assert saved.expires is not None
    assert saved.expires.replace(tzinfo=datetime.UTC) == end


def test_record_invited_user_without_an_invite_has_no_expiry(session):
    server, _invitation = _setup()

    user = _client(server)._record_invited_user(
        username="reader",
        email="reader@example.com",
        token="remote-id",
        code="SHARED001",
    )

    saved = db.session.get(User, user.id)
    assert saved is not None
    assert saved.expires is None
