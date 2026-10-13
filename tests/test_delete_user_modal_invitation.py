"""The delete-user dialog offers the invitation the person joined with.

Removing every account that joined through an invitation left the invitation
behind in the Invitations table. The dialog now offers to delete it too when all
the accounts that joined with its code are being removed, and the server checks
that again before deleting: an invitation another person still joined with, or
one whose accounts were not all removed (a server refused one), stays.
"""

from unittest.mock import Mock

import pytest

from app.extensions import db
from app.models import AdminAccount, Identity, Invitation, MediaServer, User
from app.services.media import service

HX = {"HX-Request": "true"}


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
    behaviour = {}

    def client_for(server):
        fake = Mock()
        error = behaviour.get(server.name)
        if error is not None:
            fake.delete_user.side_effect = error
        return fake

    monkeypatch.setattr(service, "get_client_for_media_server", client_for)
    return behaviour


def _server(session, name):
    row = MediaServer(
        name=name, server_type="audiobookshelf", url="http://x.local", api_key="k"
    )
    session.add(row)
    session.commit()
    return row


def _user(session, server, name, *, code="JOINCODE1", identity=None):
    user = User(
        token=f"remote-{name}-{server.id}",
        username=name,
        email=f"{name}@example.com",
        code=code,
        server_id=server.id,
        identity_id=identity.id if identity else None,
    )
    session.add(user)
    session.commit()
    return user


def _invite(session, code="JOINCODE1", unlimited=False):
    row = Invitation(code=code, used=True, unlimited=unlimited)
    session.add(row)
    session.commit()
    return row


def _person(session):
    """One person with an account on two servers, from one invitation."""
    identity = Identity(primary_username="sam")
    session.add(identity)
    session.commit()
    a = _user(session, _server(session, "ABS"), "sam", identity=identity)
    b = _user(session, _server(session, "Books"), "sam", identity=identity)
    return a, b


def _modal(client, user):
    resp = client.get(f"/users/{user.id}/delete-modal", headers=HX)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_the_dialog_offers_the_invitation_the_person_joined_with(admin_client, session):
    a, _b = _person(session)
    invite = _invite(session)

    html = _modal(admin_client, a)

    assert 'id="invitation-offers"' in html
    assert f'value="{invite.id}"' in html
    assert "JOINCODE1" in html


def test_no_offer_when_another_person_joined_with_the_same_code(admin_client, session):
    a, _b = _person(session)
    _invite(session, unlimited=True)
    _user(session, _server(session, "Other"), "pat", code="JOINCODE1")

    assert 'id="invitation-offers"' not in _modal(admin_client, a)


def test_no_offer_for_an_account_without_a_real_code(admin_client, session):
    user = _user(session, _server(session, "ABS"), "sam", code="empty")
    _invite(session, code="empty")

    assert 'id="invitation-offers"' not in _modal(admin_client, user)


def _delete(client, accounts, invites=()):
    return client.post(
        "/users/process-deletion",
        data={
            "server_accounts": [str(u.id) for u in accounts],
            "delete_invitations": [str(i.id) for i in invites],
        },
        headers=HX,
    )


def test_removing_every_account_deletes_the_ticked_invitation(
    admin_client, session, remote
):
    a, b = _person(session)
    invite = _invite(session)

    _delete(admin_client, [a, b], [invite])

    assert db.session.get(Invitation, invite.id) is None
    assert User.query.count() == 0


def test_the_invitation_stays_when_it_is_not_ticked(admin_client, session, remote):
    a, b = _person(session)
    invite = _invite(session)

    _delete(admin_client, [a, b])

    assert db.session.get(Invitation, invite.id) is not None


def test_the_invitation_stays_when_only_some_accounts_are_removed(
    admin_client, session, remote
):
    a, b = _person(session)
    invite = _invite(session)

    _delete(admin_client, [a], [invite])

    assert db.session.get(Invitation, invite.id) is not None
    assert db.session.get(User, b.id) is not None


def test_the_invitation_stays_when_a_server_refused_one_delete(
    admin_client, session, remote
):
    a, b = _person(session)
    invite = _invite(session)
    remote["Books"] = RuntimeError("down")

    _delete(admin_client, [a, b], [invite])

    assert db.session.get(User, b.id) is not None
    assert db.session.get(Invitation, invite.id) is not None


def test_an_invitation_another_person_joined_with_is_never_deleted(
    admin_client, session, remote
):
    a, b = _person(session)
    invite = _invite(session, unlimited=True)
    other = _user(session, _server(session, "Other"), "pat")

    _delete(admin_client, [a, b], [invite])

    assert db.session.get(User, other.id) is not None
    assert db.session.get(Invitation, invite.id) is not None
