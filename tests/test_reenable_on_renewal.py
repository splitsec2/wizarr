"""
Renewing a disabled account switches it back on.

An expired account is disabled, not deleted. Setting a new expiry on it in the
user modal used to change the date only, and the account stayed switched off on
the server. The only way back on was the API. Now a renewal that puts the
expiry in the future also enables the account, and only a disabled one: an
active account is never sent a second enable.
"""

import datetime
from unittest.mock import Mock

import pytest
from flask import url_for

from app.extensions import db
from app.jinja_filters import to_local
from app.models import AdminAccount, MediaServer, User
from app.services import expiry
from app.services.media import service

PAST = datetime.timedelta(days=-3)
FUTURE = datetime.timedelta(days=30)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@pytest.fixture
def server(session):
    row = MediaServer(
        name="ABS", server_type="audiobookshelf", url="http://abs.local", api_key="k"
    )
    session.add(row)
    session.commit()
    return row


@pytest.fixture
def admin_client(client, session):
    admin = AdminAccount(username="testadmin")
    admin.set_password("TestPass123")
    session.add(admin)
    session.commit()
    client.post("/login", data={"username": "testadmin", "password": "TestPass123"})
    return client


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


def _user(session, server, *, disabled, offset, code="CODE", name="sam"):
    user = User(
        token=f"remote-{name}",
        username=name,
        email=f"{name}@example.com",
        code=code,
        expires=_now() + offset,
        is_disabled=disabled,
        server_id=server.id,
    )
    session.add(user)
    session.commit()
    return user.id


def test_a_renewed_disabled_account_is_switched_back_on(session, server, client_mock):
    uid = _user(session, server, disabled=True, offset=FUTURE)

    assert expiry.reenable_if_renewed(_load(uid)) is True

    client_mock.enable_user.assert_called_once_with("remote-sam")
    assert _load(uid).is_disabled is False


def test_an_active_account_is_never_sent_an_enable(session, server, client_mock):
    uid = _user(session, server, disabled=False, offset=FUTURE)

    assert expiry.reenable_if_renewed(_load(uid)) is False

    client_mock.enable_user.assert_not_called()


def test_a_disabled_account_whose_expiry_is_still_past_stays_off(
    session, server, client_mock
):
    uid = _user(session, server, disabled=True, offset=PAST)

    assert expiry.reenable_if_renewed(_load(uid)) is False

    client_mock.enable_user.assert_not_called()
    assert _load(uid).is_disabled is True


def test_clearing_the_expiry_switches_a_disabled_account_on(
    session, server, client_mock
):
    uid = _user(session, server, disabled=True, offset=FUTURE)
    user = _load(uid)
    user.expires = None
    db.session.commit()

    assert expiry.reenable_if_renewed(user) is True


def test_a_failed_enable_leaves_it_disabled(session, server, client_mock):
    client_mock.enable_user.return_value = False
    uid = _user(session, server, disabled=True, offset=FUTURE)

    assert expiry.reenable_if_renewed(_load(uid)) is False
    assert _load(uid).is_disabled is True


def test_saving_a_future_expiry_in_the_user_modal_enables_the_account(
    app, admin_client, session, server, client_mock
):
    uid = _user(session, server, disabled=True, offset=PAST)
    with app.test_request_context():
        url = url_for("admin.user_detail", db_id=uid)
    new_end = (_now() + FUTURE).replace(tzinfo=None).isoformat(timespec="minutes")

    assert admin_client.post(url, data={"expires": new_end}).status_code == 200

    client_mock.enable_user.assert_called_once_with("remote-sam")
    assert _load(uid).is_disabled is False


def test_saving_notes_does_not_switch_a_switched_off_account_back_on(
    app, admin_client, session, server, client_mock
):
    uid = _user(session, server, disabled=True, offset=FUTURE)
    with app.test_request_context():
        url = url_for("admin.user_detail", db_id=uid)

    assert (
        admin_client.post(url, data={"notes": "away for the term"}).status_code == 200
    )

    client_mock.enable_user.assert_not_called()
    assert _load(uid).is_disabled is True


def test_saving_the_same_expiry_again_is_not_a_renewal(
    app, admin_client, session, server, client_mock
):
    uid = _user(session, server, disabled=True, offset=FUTURE)
    with app.test_request_context():
        url = url_for("admin.user_detail", db_id=uid)
    same = _load(uid).expires
    assert same is not None
    shown = to_local(same.replace(tzinfo=datetime.UTC))
    same_local = shown.replace(tzinfo=None).isoformat(timespec="minutes")

    admin_client.post(url, data={"expires": same_local})

    client_mock.enable_user.assert_not_called()
