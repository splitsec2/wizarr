"""
Admins can have a nickname and a sign-in email. "Created by" and "Invited by"
show the nickname of the admin whose email or username an invite recorded.
"""

from app.extensions import db
from app.models import AdminAccount, admin_display_name
from tests.test_invite_library_selection import HX, _login
from tests.test_invite_names import _join, _server


def _admin(session, username, nickname=None, email=None):
    account = AdminAccount(username=username, nickname=nickname, email=email)
    account.set_password("TestPass123")
    session.add(account)
    session.commit()
    return account


def test_nickname_by_email_ignores_case(session):
    _admin(session, "rob", nickname="Rob", email="rob@example.com")
    assert admin_display_name("Rob@Example.com") == "Rob"


def test_nickname_by_username(session):
    _admin(session, "alex", nickname="Alex")
    assert admin_display_name("alex") == "Alex"


def test_unknown_or_unnamed_admin_shows_as_recorded(session):
    _admin(session, "pat")
    assert admin_display_name("pat") == "pat"
    assert admin_display_name("sam@example.com") == "sam@example.com"
    assert admin_display_name(None) is None


def test_edit_form_saves_nickname_and_lowercased_email(client, session):
    _login(client, session)
    account = AdminAccount.query.filter_by(username="testadmin").one()
    client.post(
        f"/settings/admins/{account.id}/edit",
        data={"username": "testadmin", "nickname": "Rob", "email": "Rob@Example.com"},
        headers=HX,
    )
    db.session.refresh(account)
    assert account.nickname == "Rob"
    assert account.email == "rob@example.com"


def test_cards_show_the_nickname(client, session):
    _login(client, session)
    account = AdminAccount.query.filter_by(username="testadmin").one()
    account.nickname = "Rob"
    session.commit()
    server = _server(session)
    client.post(
        "/invite",
        data={"server_ids": [str(server.id)], "invitee_name": "Sam"},
        headers=HX,
    )
    from app.models import Invitation

    invite = Invitation.query.one()
    _join(invite, server, "sam1", "sam@example.com")

    assert "Created by Rob, " in client.post("/invite/table").get_data(as_text=True)
    assert "Invited by Rob, " in client.get("/users/table").get_data(as_text=True)
