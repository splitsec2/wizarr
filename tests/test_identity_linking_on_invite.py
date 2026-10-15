"""One person's accounts from one invite share an Identity, whatever address
each service holds (a Plex account's email often differs from the one given for
the other services)."""

import pytest

from app.extensions import db
from app.models import Invitation, MediaServer, User
from app.services.media.client_base import MediaClient


@pytest.fixture
def servers(session):
    made = [
        MediaServer(name=n, server_type=t, url="http://x")
        for n, t in (("Plex", "plex"), ("ABS", "audiobookshelf"))
    ]
    db.session.add_all(made)
    db.session.commit()
    return made


def _invite(code, unlimited):
    inv = Invitation(code=code, used=False, unlimited=unlimited)
    db.session.add(inv)
    db.session.commit()


def _make(server, email, code, username="u", person_email=None):
    user = MediaClient._create_user_with_identity_linking(
        None,
        {
            "username": username,
            "email": email,
            "token": "t",
            "code": code,
            "server_id": server.id,
        },
        person_email=person_email,
    )
    db.session.commit()
    return user


def test_single_use_accounts_with_different_emails_are_one_person(servers):
    plex, abs_server = servers
    _invite("ONE00001", unlimited=False)
    a = _make(plex, "a@gmail.example", "ONE00001")
    b = _make(abs_server, "b@work.example", "ONE00001")
    assert a.identity_id is not None
    assert a.identity_id == b.identity_id


def test_shared_invite_links_plex_to_the_proven_email(servers):
    plex, abs_server = servers
    _invite("SHARED01", unlimited=True)
    b = _make(abs_server, "b@work.example", "SHARED01")
    a = _make(plex, "a@gmail.example", "SHARED01", person_email="b@work.example")
    assert a.identity_id is not None
    assert a.identity_id == db.session.get(User, b.id).identity_id


def test_shared_invite_links_when_plex_comes_first(servers):
    plex, abs_server = servers
    _invite("SHARED02", unlimited=True)
    a = _make(plex, "a@gmail.example", "SHARED02", person_email="b@work.example")
    b = _make(abs_server, "b@work.example", "SHARED02")
    assert a.identity_id is not None
    assert a.identity_id == b.identity_id


def test_shared_invite_keeps_different_people_apart(servers):
    plex, abs_server = servers
    _invite("SHARED03", unlimited=True)
    pat_abs = _make(abs_server, "pat@example.com", "SHARED03")
    pat_plex = _make(
        plex, "pat.plex@example.com", "SHARED03", person_email="pat@example.com"
    )
    sam_abs = _make(abs_server, "sam@example.com", "SHARED03")
    sam_plex = _make(
        plex, "sam.plex@example.com", "SHARED03", person_email="sam@example.com"
    )
    assert pat_abs.identity_id == pat_plex.identity_id
    assert sam_abs.identity_id == sam_plex.identity_id
    assert pat_abs.identity_id != sam_abs.identity_id
