"""An Audiobookshelf account made for an invite gets the invite's libraries,
whichever path creates it: the ABS join page or the password step that follows
the Plex sign-in on a Plex + Audiobookshelf invite (which used to leave the
account with no libraries at all)."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from app.extensions import db
from app.models import AdminAccount, Invitation, Library, MediaServer, Settings
from app.services.media.audiobookshelf import AudiobookshelfClient


class _Recorder:
    def __init__(self, server_id):
        self.server_id = server_id
        self.granted = None

    def _set_specific_libraries(self, user_id, lib_ids, allow_downloads=True):
        self.granted = (user_id, lib_ids, allow_downloads)


def _grant(server_id, invitation, allow_downloads=True):
    fake = _Recorder(server_id)
    AudiobookshelfClient.grant_invite_libraries(
        cast(Any, fake), "u1", invitation, allow_downloads
    )
    return fake.granted


def _abs_server(session, n_libraries=3):
    server = MediaServer(name="ABS", server_type="audiobookshelf", url="http://abs")
    session.add(server)
    session.flush()
    libs = [
        Library(external_id=f"lib{i}", name=f"L{i}", server_id=server.id, enabled=True)
        for i in range(n_libraries)
    ]
    session.add_all(libs)
    session.commit()
    return server, libs


def test_picked_libraries_are_granted(session):
    server, libs = _abs_server(session)
    invite = SimpleNamespace(libraries=libs[:2])
    assert _grant(server.id, invite) == ("u1", ["lib0", "lib1"], True)


def test_every_library_picked_means_all(session):
    server, libs = _abs_server(session)
    assert _grant(server.id, SimpleNamespace(libraries=libs))[1] == []


def test_no_pick_on_this_server_means_all(session):
    server, _libs = _abs_server(session)
    other = Library(external_id="plexlib", name="Movies", server_id=server.id + 99)
    assert _grant(server.id, SimpleNamespace(libraries=[other]))[1] == []
    assert _grant(server.id, SimpleNamespace(libraries=[]))[1] == []


def test_downloads_flag_is_passed_through(session):
    server, libs = _abs_server(session)
    assert _grant(server.id, SimpleNamespace(libraries=libs[:1]), False)[2] is False


class _AbsClient:
    def __init__(self):
        self.created = None
        self.granted = None

    def create_user(self, username, password, email, allow_downloads=True):
        self.created = (username, email, allow_downloads)
        return "abs-user-1"

    def grant_invite_libraries(self, user_id, invitation, allow_downloads=True):
        self.granted = (user_id, invitation.code, allow_downloads)


def test_password_step_grants_the_invite_libraries(client, session):
    admin = AdminAccount(username="admin")
    admin.set_password("password")
    db.session.add_all([admin, Settings(key="admin_username", value="admin")])
    server, libs = _abs_server(session)
    invitation = Invitation(code="ABSLIBS1", used=False, allow_downloads=True)
    invitation.servers.append(server)
    invitation.libraries = libs[:1]
    db.session.add(invitation)
    db.session.commit()
    with client.session_transaction() as sess:
        sess["invite_code"] = "ABSLIBS1"

    abs_client = _AbsClient()
    with patch(
        "app.services.media.service.get_client_for_media_server",
        return_value=abs_client,
    ):
        response = client.post(
            "/j/ABSLIBS1/password",
            data={
                "username": "listener",
                "email": "listener@example.com",
                "password": "password123",
                "confirm": "password123",
            },
        )

    assert response.status_code == 302
    assert abs_client.created == ("listener", "listener@example.com", True)
    assert abs_client.granted == ("abs-user-1", "ABSLIBS1", True)
