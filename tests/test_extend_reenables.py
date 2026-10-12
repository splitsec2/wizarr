"""
"Extend everyone" on a family invite switches disabled accounts back on.

Fork only: family invites (``extend_group``) are not upstream. The renewal rule
itself, ``reenable_if_renewed``, is tested in ``test_reenable_on_renewal.py``.
"""

import datetime
from unittest.mock import Mock

import pytest

from app.extensions import db
from app.models import Invitation, MediaServer, User
from app.services import invites
from app.services.media import service

FUTURE = datetime.timedelta(days=30)
PAST = datetime.timedelta(days=-3)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@pytest.fixture
def client_mock(monkeypatch):
    client = Mock()
    client.enable_user.return_value = True
    monkeypatch.setattr(service, "get_client_for_media_server", lambda _s: client)
    return client


def _load(user_id: int) -> User:
    user = db.session.get(User, user_id)
    assert user is not None
    return user


def _user(session, server, *, disabled, name):
    user = User(
        token=f"remote-{name}",
        username=name,
        email=f"{name}@example.com",
        code="FAMILY",
        expires=_now() + PAST,
        is_disabled=disabled,
        server_id=server.id,
    )
    session.add(user)
    session.commit()
    return user.id


def test_extend_everyone_enables_only_the_disabled_accounts(session, client_mock):
    server = MediaServer(
        name="ABS", server_type="audiobookshelf", url="http://abs.local", api_key="k"
    )
    invitation = Invitation(code="FAMILY", unlimited=True)
    invitation.servers.append(server)
    session.add_all([server, invitation])
    session.commit()
    off = _user(session, server, disabled=True, name="pat")
    on = _user(session, server, disabled=False, name="lee")

    assert invites.extend_group(invitation, (_now() + FUTURE).date()) == 2

    client_mock.enable_user.assert_called_once_with("remote-pat")
    assert _load(off).is_disabled is False
    assert _load(on).is_disabled is False
