"""Switch off / Switch on on the Users card.

An account on a server that can disable (Audiobookshelf, the provisioning hook)
can be switched off by hand and back on again. Switching on works like a
renewal: only a disabled account is sent the enable (an active one is never sent
a second one), and an account whose expiry has already passed stays off with a
message, since the next expiry run would only disable it again.
"""

import datetime
from unittest.mock import Mock

import pytest

from app.extensions import db
from app.models import AdminAccount, MediaServer, User
from app.services.media import service

HX = {"HX-Request": "true"}


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@pytest.fixture
def admin_client(client, session):
    admin = AdminAccount(username="testadmin")
    admin.set_password("TestPass123")
    session.add(admin)
    session.commit()
    client.post("/login", data={"username": "testadmin", "password": "TestPass123"})
    return client


@pytest.fixture
def remote(monkeypatch):
    fake = Mock()
    fake.enable_user.return_value = True
    fake.disable_user.return_value = True
    monkeypatch.setattr(service, "get_client_for_media_server", lambda _s: fake)
    return fake


def _load(user_id: int) -> User:
    user = db.session.get(User, user_id)
    assert user is not None
    return user


def _server(session, server_type="audiobookshelf", name="ABS"):
    row = MediaServer(
        name=name, server_type=server_type, url="http://x.local", api_key="k"
    )
    session.add(row)
    session.commit()
    return row


def _user(session, server, *, disabled=False, expires=None, name="sam"):
    user = User(
        token=f"remote-{name}-{server.id}",
        username=name,
        email=f"{name}@example.com",
        code="CODE",
        expires=expires,
        server_id=server.id,
    )
    user.is_disabled = disabled
    session.add(user)
    session.commit()
    return user


def _switch(client, state, *ids):
    return client.post(
        "/users/switch",
        data={"state": state, "uids": [str(i) for i in ids]},
        headers=HX,
    )


def test_switch_off_disables_an_active_account(admin_client, session, remote):
    abs_ = _server(session)
    user = _user(session, abs_)

    resp = _switch(admin_client, "off", user.id)

    assert resp.status_code == 200
    remote.disable_user.assert_called_once()
    assert _load(user.id).is_disabled is True


def test_a_clean_switch_closes_the_dialog_area(admin_client, session, remote):
    """The users page shows its grey overlay whenever anything is swapped into
    the dialog area, so a switch with nothing to say must also close it."""
    abs_ = _server(session)
    user = _user(session, abs_)

    resp = _switch(admin_client, "off", user.id)

    assert resp.get_data(as_text=True) == ""
    triggers = resp.headers["HX-Trigger"]
    assert "refreshUserTable" in triggers
    assert "closeModal" in triggers


def test_a_switch_with_a_problem_keeps_the_dialog_open(admin_client, session, remote):
    remote.disable_user.return_value = False
    abs_ = _server(session)
    user = _user(session, abs_)

    resp = _switch(admin_client, "off", user.id)

    assert "closeModal" not in resp.headers["HX-Trigger"]


def test_switch_off_leaves_an_already_disabled_account_alone(
    admin_client, session, remote
):
    abs_ = _server(session)
    user = _user(session, abs_, disabled=True)

    _switch(admin_client, "off", user.id)

    remote.disable_user.assert_not_called()


def test_switch_on_enables_a_disabled_account(admin_client, session, remote):
    abs_ = _server(session)
    user = _user(
        session, abs_, disabled=True, expires=_now() + datetime.timedelta(days=9)
    )

    _switch(admin_client, "on", user.id)

    remote.enable_user.assert_called_once()
    assert _load(user.id).is_disabled is False


def test_switch_on_never_sends_an_enable_to_an_active_account(
    admin_client, session, remote
):
    abs_ = _server(session)
    user = _user(session, abs_, disabled=False)

    _switch(admin_client, "on", user.id)

    remote.enable_user.assert_not_called()


def test_switch_on_after_the_expiry_stays_off_and_says_why(
    admin_client, session, remote
):
    abs_ = _server(session)
    user = _user(
        session, abs_, disabled=True, expires=_now() - datetime.timedelta(days=2)
    )

    resp = _switch(admin_client, "on", user.id)

    remote.enable_user.assert_not_called()
    assert _load(user.id).is_disabled is True
    body = resp.get_data(as_text=True)
    assert "sam" in body
    assert "access ended" in body


def test_a_failed_remote_switch_keeps_the_account_and_reports_it(
    admin_client, session, remote
):
    remote.disable_user.return_value = False
    abs_ = _server(session)
    user = _user(session, abs_)

    resp = _switch(admin_client, "off", user.id)

    assert _load(user.id).is_disabled is False
    body = resp.get_data(as_text=True)
    assert "could not be switched off" in body
    assert "ABS" in body


def test_a_server_that_cannot_disable_is_left_alone(admin_client, session, remote):
    plex = _server(session, "plex", "Plex")
    user = _user(session, plex)

    resp = _switch(admin_client, "off", user.id)

    remote.disable_user.assert_not_called()
    assert _load(user.id).is_disabled is False
    assert "cannot be switched off" in resp.get_data(as_text=True)


def test_the_users_card_offers_the_switch_for_servers_that_can_disable(
    admin_client, session, remote
):
    abs_ = _server(session)
    plex = _server(session, "plex", "Plex")
    _user(session, abs_, name="sam")
    _user(session, plex, name="pat")

    html = admin_client.get("/users/table", headers=HX).get_data(as_text=True)

    assert html.count("Switch off") == 1
    assert "Switch on" not in html


def test_the_switch_shows_a_spinner_and_blocks_a_second_click(
    admin_client, session, remote
):
    """A switch takes a few seconds (server calls), so the button says so."""
    import re

    abs_ = _server(session)
    _user(session, abs_)

    html = admin_client.get("/users/table", headers=HX).get_data(as_text=True)

    button = re.search(r"<button[^>]*Switch off\"[^>]*>.*?</button>", html, re.S)
    assert button
    markup = button.group(0)
    assert 'hx-disabled-elt="this"' in markup
    assert "animate-spin" in markup


def test_a_switched_off_account_has_a_struck_through_pill(
    admin_client, session, remote
):
    import re

    abs_ = _server(session)
    plex = _server(session, "plex", "Plex")
    _user(session, abs_, disabled=True, name="sam")
    _user(session, plex, name="pat")

    html = admin_client.get("/users/table", headers=HX).get_data(as_text=True)

    pills = {
        name: style
        for style, name in re.findall(
            r'<span class="[^"]*" style="([^"]*)">(ABS|Plex)</span>', html
        )
    }
    assert "line-through" in pills["ABS"]
    assert "line-through" not in pills["Plex"]


def test_the_card_offers_switch_on_when_every_such_account_is_off(
    admin_client, session, remote
):
    abs_ = _server(session)
    _user(session, abs_, disabled=True)

    html = admin_client.get("/users/table", headers=HX).get_data(as_text=True)

    assert "Switch on" in html
    assert "Switch off" not in html
