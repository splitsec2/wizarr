"""The account guard: delete, switch off and remove refuse a protected account.

One setting per person (Protected in the user modal). The refusal is made in the
media service, so the admin pages, the API and the expiry job all get it.
"""

import datetime
from unittest.mock import Mock

import pytest
from flask import url_for

from app.extensions import db
from app.models import AdminAccount, Identity, MediaServer, User
from app.services import account_guard, expiry
from app.services.account_guard import AccountProtected
from app.services.media import service


@pytest.fixture
def server(session):
    row = MediaServer(
        name="ABS", server_type="audiobookshelf", url="http://abs.local", api_key="k"
    )
    session.add(row)
    session.commit()
    return row


@pytest.fixture
def remote(monkeypatch):
    client = Mock()
    client.enable_user.return_value = True
    client.disable_user.return_value = True
    monkeypatch.setattr(service, "get_client_for_media_server", lambda _s: client)
    return client


@pytest.fixture
def admin_client(client, session):
    admin = AdminAccount(username="testadmin")
    admin.set_password("TestPass123")
    session.add(admin)
    session.commit()
    client.post("/login", data={"username": "testadmin", "password": "TestPass123"})
    return client


def _load(user_id: int) -> User:
    user = db.session.get(User, user_id)
    assert user is not None
    return user


def _user(server, name="sam", *, protected=False, expires=None, identity=None):
    user = User(
        token=f"remote-{name}",
        username=name,
        email=f"{name}@example.com",
        code="CODE",
        server_id=server.id,
        protected=protected,
        expires=expires,
        identity=identity,
    )
    db.session.add(user)
    db.session.commit()
    return user.id


def test_refusal_names_a_reason_for_a_protected_account_only(session, server):
    guarded = db.session.get(User, _user(server, "a", protected=True))
    free = db.session.get(User, _user(server, "b"))

    assert guarded is not None
    assert free is not None
    assert account_guard.refusal(guarded, "delete")
    assert account_guard.refusal(guarded, "disable")
    assert account_guard.refusal(guarded, "remove")
    assert account_guard.refusal(guarded, "enable") is None
    assert account_guard.refusal(free, "delete") is None


def test_another_rule_can_be_added(session, server):
    rule = lambda user: "no" if user.username == "pat" else None  # noqa: E731
    account_guard.register(rule)
    try:
        pat = db.session.get(User, _user(server, "pat"))
        assert pat is not None
        assert account_guard.refusal(pat, "delete") == "no"
    finally:
        account_guard._rules.remove(rule)


def test_a_failing_rule_refuses_rather_than_opens_up(session, server):
    def broken(_user):
        raise RuntimeError("boom")

    account_guard.register(broken)
    try:
        user = db.session.get(User, _user(server))
        assert user is not None
        assert account_guard.refusal(user, "delete")
    finally:
        account_guard._rules.remove(broken)


def test_delete_refuses_and_keeps_the_row(session, server, remote):
    uid = _user(server, protected=True)

    with pytest.raises(AccountProtected):
        service.delete_user(uid)

    remote.delete_user.assert_not_called()
    assert db.session.get(User, uid) is not None


def test_disable_refuses_and_enable_still_works(session, server, remote):
    uid = _user(server, protected=True)

    assert service.disable_user(uid) is False
    remote.disable_user.assert_not_called()

    user = db.session.get(User, uid)
    assert user is not None
    user.is_disabled = True
    db.session.commit()
    assert service.enable_user(uid) is True


def test_remove_from_server_refuses(session, server, remote):
    uid = _user(server, protected=True)

    with pytest.raises(AccountProtected):
        service.remove_user_from_server(uid, server.id)

    assert db.session.get(User, uid) is not None


def test_an_unprotected_account_is_changed_as_before(session, server, remote):
    uid = _user(server)

    service.delete_user(uid)

    remote.delete_user.assert_called_once()
    assert db.session.get(User, uid) is None


def test_the_expiry_job_skips_a_protected_account_quietly(session, server, remote):
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=2)
    guarded = _user(server, "kept", protected=True, expires=past)

    expiry.disable_or_delete_user_if_expired()

    remote.disable_user.assert_not_called()
    remote.delete_user.assert_not_called()
    user = db.session.get(User, guarded)
    assert user is not None
    assert user.is_disabled is False


def test_bulk_delete_removes_the_rest_and_keeps_the_protected(
    app, admin_client, session, server, remote
):
    keep = _user(server, "keep", protected=True)
    drop = _user(server, "drop")
    with app.test_request_context():
        url = url_for("admin.bulk_delete_users")

    resp = admin_client.post(url, data={"uids": [str(keep), str(drop)]})

    assert resp.status_code == 200
    assert db.session.get(User, keep) is not None
    assert db.session.get(User, drop) is None


def test_the_modal_sets_protected_for_every_account_of_the_person(
    app, admin_client, session, server
):
    identity = Identity(nickname="Sam")
    db.session.add(identity)
    db.session.commit()
    first = _user(server, "one", identity=identity)
    second = _user(server, "two", identity=identity)
    with app.test_request_context():
        url = url_for("admin.user_detail", db_id=first)

    admin_client.post(url, data={"protected_form": "1", "protected": "1"})

    assert _load(first).protected is True
    assert _load(second).protected is True

    admin_client.post(url, data={"protected_form": "1"})

    assert _load(first).protected is False
    assert _load(second).protected is False


def test_saving_notes_leaves_protected_alone(app, admin_client, session, server):
    uid = _user(server, protected=True)
    with app.test_request_context():
        url = url_for("admin.user_detail", db_id=uid)

    admin_client.post(url, data={"notes": "hello"})

    assert _load(uid).protected is True


def test_the_card_says_protected_and_hides_switch_off(admin_client, session, server):
    _user(server, "guarded", protected=True)

    html = admin_client.get("/users/table", headers={"HX-Request": "true"}).get_data(
        as_text=True
    )

    assert "Protected" in html
    assert "Switch off" not in html


@pytest.fixture
def api_key(session):
    import hashlib

    from app.models import ApiKey, Settings

    admin = AdminAccount(username="apiadmin")
    admin.set_password("TestPass123")
    session.add_all([admin, Settings(key="admin_username", value="apiadmin")])
    session.commit()
    raw = "guard_test_key_12345"
    session.add(
        ApiKey(
            name="t",
            key_hash=hashlib.sha256(raw.encode()).hexdigest(),
            created_by_id=admin.id,
            is_active=True,
        )
    )
    session.commit()
    return raw


def test_the_api_answers_409_for_a_protected_account(
    client, session, server, remote, api_key
):
    uid = _user(server, protected=True)
    headers = {"X-API-Key": api_key}

    assert client.delete(f"/api/users/{uid}", headers=headers).status_code == 409
    assert client.post(f"/api/users/{uid}/disable", headers=headers).status_code == 409
    assert db.session.get(User, uid) is not None
    remote.delete_user.assert_not_called()
