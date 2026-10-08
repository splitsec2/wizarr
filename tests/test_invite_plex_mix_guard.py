"""
A Plex invite sets up its other servers in the password step after Plex
sign-in, and that step skips any server type it doesn't handle. Such an
invite is refused instead of silently giving no access.
"""

import pytest

from app.models import Invitation, MediaServer
from app.services.invites import create_invite


def _server(session, name, server_type):
    server = MediaServer(
        name=name, server_type=server_type, url="http://x", api_key="k"
    )
    session.add(server)
    session.commit()
    return server


def test_plex_with_an_unhandled_type_is_refused(session):
    plex = _server(session, "Movies", "plex")
    books = _server(session, "Books", "kavita")
    with pytest.raises(ValueError, match="Make a separate invite for Books"):
        create_invite({"server_ids": [str(plex.id), str(books.id)]})
    assert Invitation.query.count() == 0


@pytest.mark.parametrize("server_type", ["audiobookshelf", "jellyfin"])
def test_plex_with_a_handled_type_is_allowed(session, server_type):
    plex = _server(session, "Movies", "plex")
    other = _server(session, "Other", server_type)
    assert create_invite({"server_ids": [str(plex.id), str(other.id)]})


def test_unhandled_type_without_plex_is_allowed(session):
    books = _server(session, "Books", "kavita")
    assert create_invite({"server_ids": [str(books.id)]})


def test_plex_options_show_advice(client, session):
    from tests.test_invite_library_selection import HX, _login

    _login(client, session)
    _server(session, "Movies", "plex")
    body = client.get("/invite", headers=HX).get_data(as_text=True)
    assert "(recommended)" in body
    assert body.count("(not recommended)") == 3
