"""A family on one shared invite: a people limit, who pays, and renewing the
whole group at once (R12 and R9b)."""

import datetime
import re

import pytest

from app.extensions import db
from app.models import (
    AdminAccount,
    Invitation,
    InvitationPerson,
    MediaServer,
    Settings,
    User,
)
from app.services import invite_steps
from app.services.expiry import access_end, get_server_specific_expiry, local_today
from app.services.invites import create_invite, extend_group


@pytest.fixture
def mail(session, monkeypatch):
    admin = AdminAccount(username="admin")
    admin.set_password("TestPass123")
    db.session.add_all(
        [
            admin,
            Settings(key="admin_username", value="admin"),
            Settings(key="email_host", value="smtp.example.com"),
            Settings(key="email_from", value="wizarr@example.com"),
        ]
    )
    db.session.commit()
    outbox = []
    monkeypatch.setattr(
        "app.services.mailer.send",
        lambda to, subject, body, config=None: outbox.append((to, body)) or (True, ""),
    )
    return outbox


def _server(name="ABS", server_type="audiobookshelf"):
    server = MediaServer(
        name=name, server_type=server_type, url="http://x", api_key="k"
    )
    db.session.add(server)
    db.session.commit()
    return server


def _family(server, max_people=2, paid_by="Pat"):
    invitation = Invitation(
        code="FAMILY01",
        used=False,
        unlimited=True,
        max_people=max_people,
        paid_by=paid_by,
    )
    invitation.servers.append(server)
    db.session.add(invitation)
    db.session.commit()
    return invitation


def _join(client, email, outbox):
    sent = client.post(
        "/j/FAMILY01/steps/email", data={"email": email, "next": "/j/FAMILY01/steps"}
    )
    if sent.status_code != 302:
        return sent
    found = re.search(r"\b(\d{6})\b", outbox[-1][1])
    assert found is not None
    return client.post(
        "/j/FAMILY01/steps/verify",
        data={"code": found.group(1), "next": "/j/FAMILY01/steps"},
    )


# ── the invite form ──────────────────────────────────────────────────────────


def test_a_shared_invite_keeps_its_limit_and_payer(mail):
    server = _server()
    invite = create_invite(
        {
            "server_ids": [str(server.id)],
            "unlimited": "1",
            "max_people": "5",
            "paid_by": "  Pat   Smith ",
        }
    )
    assert (invite.max_people, invite.paid_by) == (5, "Pat Smith")


def test_limit_and_payer_need_a_shared_invite(mail):
    server = _server()
    with pytest.raises(ValueError, match="Turn on Unlimited Usages"):
        create_invite({"server_ids": [str(server.id)], "max_people": "5"})


@pytest.mark.parametrize("value", ["0", "101", "five"])
def test_bad_people_counts_are_refused(mail, value):
    server = _server()
    with pytest.raises(ValueError, match="How many people"):
        create_invite(
            {"server_ids": [str(server.id)], "unlimited": "1", "max_people": value}
        )


# ── the limit ────────────────────────────────────────────────────────────────


def test_people_count_up_to_the_limit(app, mail):
    invitation = _family(_server(), max_people=2)
    for email in ("pat@example.com", "sam@example.com"):
        resp = _join(app.test_client(), email, mail)
        assert resp.headers["Location"].endswith("/j/FAMILY01/steps")
    assert [p.email for p in InvitationPerson.query.order_by(InvitationPerson.id)] == [
        "pat@example.com",
        "sam@example.com",
    ]
    assert list(invite_steps.people(invitation)) == [
        "pat@example.com",
        "sam@example.com",
    ]


def test_a_full_invite_sends_no_code_to_someone_new(app, mail):
    _family(_server(), max_people=1)
    _join(app.test_client(), "pat@example.com", mail)
    sent_before = len(mail)
    resp = _join(app.test_client(), "sam@example.com", mail)
    assert "This invite is full." in resp.get_data(as_text=True)
    assert len(mail) == sent_before


def test_someone_already_on_a_full_invite_can_come_back(app, mail):
    _family(_server(), max_people=1)
    _join(app.test_client(), "pat@example.com", mail)
    resp = _join(app.test_client(), "pat@example.com", mail)
    assert resp.headers["Location"].endswith("/j/FAMILY01/steps")


def test_filling_up_between_code_and_confirmation_is_caught(app, mail):
    invitation = _family(_server(), max_people=1)
    late = app.test_client()
    late.post(
        "/j/FAMILY01/steps/email",
        data={"email": "late@example.com", "next": "/j/FAMILY01/steps"},
    )
    late_code = re.search(r"\b(\d{6})\b", mail[-1][1])
    assert late_code is not None
    _join(app.test_client(), "pat@example.com", mail)
    resp = late.post(
        "/j/FAMILY01/steps/verify",
        data={"code": late_code.group(1), "next": "/j/FAMILY01/steps"},
    )
    assert "This invite is full." in resp.get_data(as_text=True)
    assert not invite_steps.is_person(invitation, "late@example.com")


def test_no_limit_means_anyone_with_the_link(app, mail):
    invitation = _family(_server(), max_people=None)
    for i in range(4):
        _join(app.test_client(), f"p{i}@example.com", mail)
    assert len(invite_steps.people(invitation)) == 4


# ── renewing the group ───────────────────────────────────────────────────────


def test_extending_moves_everyone_and_later_joiners(mail):
    server = _server()
    invitation = _family(server)
    for name in ("pat", "sam"):
        db.session.add(
            User(
                token=name,
                username=name,
                email=f"{name}@example.com",
                code="FAMILY01",
                server_id=server.id,
            )
        )
    db.session.commit()
    day = local_today() + datetime.timedelta(days=200)

    assert extend_group(invitation, day) == 2

    ends = access_end(day).replace(tzinfo=None)
    assert {u.expires.replace(tzinfo=None) for u in User.query.all()} == {ends}
    stored = get_server_specific_expiry(invitation.id, server.id)
    assert stored is not None
    assert stored.replace(tzinfo=None) == ends


def test_extending_into_the_past_is_refused(mail):
    invitation = _family(_server())
    with pytest.raises(ValueError, match="in the past"):
        extend_group(invitation, local_today() - datetime.timedelta(days=1))


def _admin(app):
    admin_client = app.test_client()
    admin_client.post("/login", data={"username": "admin", "password": "TestPass123"})
    return admin_client


def test_the_invite_card_shows_payer_people_and_extends(app, mail):
    invitation = _family(_server(), max_people=5)
    _join(app.test_client(), "pat@example.com", mail)
    admin_client = _admin(app)

    card = admin_client.post("/invite/table").get_data(as_text=True)
    assert "Paid by Pat" in card
    assert "People: 1 of 5" in card
    assert "Extend everyone" in card

    day = local_today() + datetime.timedelta(days=30)
    resp = admin_client.post(
        f"/invite/table?extend_id={invitation.id}",
        data={"ends_on": day.isoformat()},
    ).get_data(as_text=True)
    assert "Everyone on this invite now has access through" in resp


def test_users_show_who_pays_for_them(app, mail):
    server = _server()
    _family(server)
    db.session.add(
        User(
            token="t",
            username="sam",
            email="sam@example.com",
            code="FAMILY01",
            server_id=server.id,
        )
    )
    db.session.commit()
    assert User.query.one().supported_by() == "Pat"
    html = _admin(app).get("/users/table").get_data(as_text=True)
    assert "Part of Pat's support" in html
