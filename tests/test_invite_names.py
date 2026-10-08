"""
An invite can say who it is for, and records who created it.

Accounts joined from a single-person invite are named after it (one shared
Identity per person), the invite card leads with the name and shows
"Created by", and the user card shows "Invited by".
"""

import pytest

from app.extensions import db
from app.models import AdminAccount, Identity, Invitation, MediaServer, User
from app.services.invites import create_invite, mark_server_used
from tests.test_cloudflare_access_auth import (  # noqa: F401
    _headers,
    _token,
    access_mode,
)
from tests.test_invite_library_selection import HX, _login


def _server(session, name="Movies", server_type="jellyfin"):
    server = MediaServer(
        name=name, server_type=server_type, url="http://x", api_key="k"
    )
    session.add(server)
    session.commit()
    return server


def _join(invite, server, username, email):
    user = User(
        token=f"t-{username}",
        username=username,
        email=email,
        code=invite.code,
        server_id=server.id,
    )
    db.session.add(user)
    db.session.commit()
    mark_server_used(invite, server.id, user)
    return user


def test_invite_keeps_the_name_tidied(session):
    server = _server(session)
    invite = create_invite(
        {"server_ids": [str(server.id)], "invitee_name": "  Sam   Lee "}
    )
    assert invite.invitee_name == "Sam Lee"


def test_unlimited_invite_cannot_carry_a_name(session):
    server = _server(session)
    with pytest.raises(ValueError, match="unlimited"):
        create_invite(
            {"server_ids": [str(server.id)], "invitee_name": "Sam", "unlimited": "true"}
        )
    assert Invitation.query.count() == 0


def test_every_account_from_the_invite_gets_the_name(session):
    movies = _server(session)
    books = _server(session, "Books", "audiobookshelf")
    invite = create_invite(
        {"server_ids": [str(movies.id), str(books.id)], "invitee_name": "Sam"}
    )

    first = _join(invite, movies, "sam1", "sam@example.com")
    second = _join(invite, books, "sam2", "sam@example.com")

    assert first.identity is not None
    assert first.identity.nickname == "Sam"
    assert second.identity_id == first.identity_id


def test_a_name_already_set_is_kept(session):
    server = _server(session)
    invite = create_invite({"server_ids": [str(server.id)], "invitee_name": "Sam"})
    user = User(token="t", username="sam", email="sam@example.com", code=invite.code)
    user.server_id = server.id
    user.identity = Identity(primary_email="sam@example.com", nickname="Samantha")
    db.session.add(user)
    db.session.commit()

    mark_server_used(invite, server.id, user)
    assert user.identity.nickname == "Samantha"


def test_invite_without_a_name_leaves_accounts_alone(session):
    server = _server(session)
    invite = create_invite({"server_ids": [str(server.id)]})
    user = _join(invite, server, "pat", "pat@example.com")
    assert user.identity_id is None


def test_created_by_is_the_signed_in_admin(client, session):
    _login(client, session)
    server = _server(session)
    client.post("/invite", data={"server_ids": [str(server.id)]}, headers=HX)
    invite = Invitation.query.one()
    assert invite.created_by is not None
    assert invite.created_by.username == "testadmin"


def test_created_by_is_the_access_admin(client, session, access_mode):  # noqa: F811
    admin = AdminAccount(
        username="rob",
        auth_source="cloudflare_access",
        external_id="admin@example.com",
        display_name="Rob",
    )
    session.add(admin)
    session.commit()
    token = _token()
    client.get("/login", headers=_headers(token))
    server = _server(session)
    client.post(
        "/invite", data={"server_ids": [str(server.id)]}, headers=_headers(token)
    )
    invite = Invitation.query.one()
    assert invite.created_by == admin
    assert invite.created_by.shown_name == "Rob"


def test_cards_show_the_name_and_who_invited(client, session):
    _login(client, session)
    server = _server(session)
    client.post(
        "/invite",
        data={"server_ids": [str(server.id)], "invitee_name": "Sam"},
        headers=HX,
    )
    invite = Invitation.query.one()
    _join(invite, server, "sam1", "sam@example.com")

    invites = client.post("/invite/table").get_data(as_text=True)
    assert "Sam" in invites
    assert "Created by testadmin, " in invites

    users = client.get("/users/table").get_data(as_text=True)
    assert "Invited by testadmin, " in users
