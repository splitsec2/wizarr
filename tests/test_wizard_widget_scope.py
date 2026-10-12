"""
The wizard only shows media from the visitor's own invite.

The "recently added" widget used to read every library on the first server of
a type, and the wizard let anyone in who sent a Referer containing /j/. Now
the widget uses the invite's server and the libraries it grants, nothing
without an invite (except an admin preview), and a Referer opens nothing.
"""

from unittest.mock import Mock

import pytest

from app.models import Invitation, Library, MediaServer
from app.services import wizard_widgets
from app.services.wizard_widgets import RecentlyAddedMediaWidget


@pytest.fixture
def servers(session):
    family = MediaServer(name="Family", server_type="plex", url="http://f", api_key="k")
    public = MediaServer(name="Public", server_type="plex", url="http://p", api_key="k")
    session.add_all([family, public])
    session.flush()
    session.add_all(
        [
            Library(
                name="Home Videos", external_id="f1", server_id=family.id, enabled=True
            ),
            Library(name="Movies", external_id="p1", server_id=public.id, enabled=True),
            Library(name="Music", external_id="p2", server_id=public.id, enabled=True),
            Library(name="Old", external_id="p3", server_id=public.id, enabled=False),
        ]
    )
    session.commit()
    return family, public


@pytest.fixture
def fake_clients(monkeypatch):
    """Record which server and libraries the widget asked for."""
    calls = []

    def client_for(_type, server):
        client = Mock()
        client.get_recent_items.side_effect = lambda limit, library_names: (
            calls.append((server.name, sorted(library_names))) or [{"title": "x"}]
        )
        return client

    monkeypatch.setattr(wizard_widgets, "get_media_client", client_for)
    return calls


def _widget_data(app, code=None):
    with app.test_request_context("/wizard/plex/0"):
        from flask import session

        if code:
            session["wizard_access"] = code
        return RecentlyAddedMediaWidget().get_data("plex")


def _invite(session, server, libraries=()):
    invitation = Invitation(code="SCOPE00001", used=False)
    invitation.servers.append(server)
    invitation.libraries.extend(libraries)
    session.add(invitation)
    session.commit()
    return invitation


def test_no_invite_shows_nothing(app, servers, fake_clients):
    assert _widget_data(app)["items"] == []
    assert fake_clients == []


def test_invite_uses_its_own_server_and_enabled_libraries(
    app, session, servers, fake_clients
):
    _family, public = servers
    _invite(session, public)
    assert _widget_data(app, "SCOPE00001")["items"]
    assert fake_clients == [("Public", ["Movies", "Music"])]


def test_invite_libraries_limit_what_is_shown(app, session, servers, fake_clients):
    _family, public = servers
    music = Library.query.filter_by(name="Music").one()
    _invite(session, public, [music])
    _widget_data(app, "SCOPE00001")
    assert fake_clients == [("Public", ["Music"])]


def test_invite_without_a_server_of_the_type_shows_nothing(app, session, fake_clients):
    books = MediaServer(
        name="Books", server_type="jellyfin", url="http://b", api_key="k"
    )
    session.add(books)
    session.flush()
    _invite(session, books)
    assert _widget_data(app, "SCOPE00001")["items"] == []
    assert fake_clients == []


def test_forged_referer_does_not_open_the_wizard(client, session):
    resp = client.get(
        "/wizard/plex/0", headers={"Referer": "https://example.com/j/ANYTHING"}
    )
    assert resp.status_code == 302
