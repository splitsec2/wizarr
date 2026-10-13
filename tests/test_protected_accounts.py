"""Protected accounts are never expired or removed by the expiry job.

The addresses are the operator's own (the Plex owner and the admins): an
expiry on one of them, from a throwaway invite, would disable or remove the
operator's real access. Settings > General holds a list (one address per line,
any case); an admin's Cloudflare Access address counts too. The expiry sweeps
leave a protected account alone, and a protected address that redeems an invite
gets an account that never expires.
"""

import datetime
from unittest.mock import Mock

import pytest

from app.extensions import db
from app.models import AdminAccount, Invitation, MediaServer, Settings, User
from app.services import expiry, protected


@pytest.fixture
def server(session):
    row = MediaServer(
        name="ABS", server_type="audiobookshelf", url="http://abs.local", api_key="k"
    )
    session.add(row)
    session.commit()
    return row


def _expired(session, server, name, email):
    user = User(
        token=f"remote-{name}",
        username=name,
        email=email,
        code="EXPIRED",
        expires=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1),
        server_id=server.id,
    )
    session.add(user)
    session.commit()
    return user.id


def _list(session, value):
    session.add(Settings(key=protected.KEY, value=value))
    session.commit()


def test_the_list_takes_lines_commas_and_any_case(session):
    _list(
        session,
        "Owner@Example.com\n# the plex owner\nsecond@example.com, THIRD@example.com",
    )
    assert protected.protected_emails() == {
        "owner@example.com",
        "second@example.com",
        "third@example.com",
    }


def test_an_admins_access_address_is_protected(session):
    admin = AdminAccount(username="rob")
    admin.auth_source = "cloudflare_access"
    admin.external_id = "Rob@Example.com"
    session.add(admin)
    session.commit()
    assert protected.is_protected_email(" rob@example.com ")
    assert not protected.is_protected_email("alex@example.com")
    assert not protected.is_protected_email("")


def test_the_delete_sweep_leaves_a_protected_account(session, server, monkeypatch):
    monkeypatch.setattr(expiry.time, "sleep", lambda _s: None)
    _list(session, "owner@example.com")
    keep = _expired(session, server, "owner", "OWNER@example.com")
    gone = _expired(session, server, "sam", "sam@example.com")
    deleted = Mock()
    monkeypatch.setattr(expiry, "delete_user", deleted)

    assert expiry.delete_user_if_expired() == [gone]

    assert [call.args[0] for call in deleted.call_args_list] == [gone]
    assert db.session.get(User, keep) is not None


@pytest.mark.parametrize("action", ["disable", "delete"])
def test_the_disable_sweep_leaves_a_protected_account(
    session, server, monkeypatch, action
):
    monkeypatch.setattr(expiry.time, "sleep", lambda _s: None)
    session.add(Settings(key="expiry_action", value=action))
    _list(session, "owner@example.com")
    keep = _expired(session, server, "owner", "owner@example.com")
    other = _expired(session, server, "sam", "sam@example.com")
    monkeypatch.setattr(expiry, "disable_user", Mock(return_value=True))
    monkeypatch.setattr(expiry, "delete_user", Mock())

    assert expiry.disable_or_delete_user_if_expired() == [other]

    kept = db.session.get(User, keep)
    assert kept is not None
    assert kept.is_disabled is False


def test_a_protected_identity_email_counts(session, server):
    from app.models import Identity

    _list(session, "owner@example.com")
    identity = Identity(primary_email="owner@example.com")
    session.add(identity)
    session.flush()
    user = User(
        token="t",
        username="o",
        email="other-address@example.com",
        code="C",
        server_id=server.id,
        identity_id=identity.id,
    )
    session.add(user)
    session.commit()
    assert protected.is_protected_user(user)


def test_a_protected_address_redeeming_an_invite_never_expires(session, server):
    _list(session, "owner@example.com")
    invitation = Invitation(code="REDEEM01", used=False, unlimited=True)
    invitation.servers.append(server)
    session.add(invitation)
    soon = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=3)
    owner = User(
        token="o",
        username="o",
        email="Owner@example.com",
        code="REDEEM01",
        expires=soon,
        server_id=server.id,
    )
    other = User(
        token="s",
        username="s",
        email="sam@example.com",
        code="REDEEM01",
        expires=soon,
        server_id=server.id,
    )
    session.add_all([owner, other])
    session.commit()

    protected.clear_expiry_if_protected("owner@example.com", "REDEEM01", server.id)

    expires = {u.id: u.expires for u in User.query.all()}
    assert expires[owner.id] is None
    assert expires[other.id] is not None


def test_settings_general_saves_the_list(client, session):
    admin = AdminAccount(username="testadmin")
    admin.set_password("TestPass123")
    session.add(admin)
    session.commit()
    client.post("/login", data={"username": "testadmin", "password": "TestPass123"})

    response = client.post(
        "/settings/general",
        data={
            "server_name": "Home",
            "expiry_action": "disable",
            "cinema_posters_source": "",
            "protected_emails": "owner@example.com\nsecond@example.com",
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert protected.protected_emails() == {"owner@example.com", "second@example.com"}
