"""The Add Server form deals only with the server being added.

Scan Libraries used to list every library row of every saved server under the
new one, and a failed Test & Add drew the API key field for a server type that
signs in with a username and password. The saved server also kept the library
ids as names and a leading space in the URL.
"""

from unittest.mock import patch

import pytest

from app.extensions import db
from app.models import AdminAccount, Library, MediaServer


@pytest.fixture
def admin_client(client, session):
    admin = AdminAccount(username="testadmin")
    admin.set_password("TestPass123")
    session.add(admin)
    session.commit()
    client.post("/login", data={"username": "testadmin", "password": "TestPass123"})
    return client


def _other_server_with_libraries(session):
    other = MediaServer(
        name="Plex",
        server_type="plex",
        url="http://plex.local",
        api_key="k",
        verified=True,
    )
    session.add(other)
    session.commit()
    for i, name in enumerate(["Movies", "Series"]):
        session.add(Library(external_id=f"p{i}", name=name, server_id=other.id))
    session.commit()
    return other


def test_scan_lists_only_what_the_scanned_server_returned(admin_client, session):
    _other_server_with_libraries(session)

    with patch(
        "app.blueprints.settings.routes.scan_media",
        return_value={"1": "Music", "2": "Liked Songs"},
    ):
        html = admin_client.post(
            "/settings/scan-libraries",
            data={
                "server_type": "navidrome",
                "server_url": "http://nav.local:4533",
                "server_username": "wizarr",
                "server_password": "s3cret",
            },
        ).get_data(as_text=True)

    assert "Music" in html
    assert "Liked Songs" in html
    assert "Movies" not in html
    assert "Series" not in html
    # the other server's rows are untouched
    assert Library.query.filter(Library.server_id.isnot(None)).count() == 2


def test_failed_test_and_add_keeps_the_credential_fields(admin_client):
    with patch(
        "app.blueprints.media_servers.routes.check_server_connection",
        return_value=(False, "Navidrome refused that username and password."),
    ):
        html = admin_client.post(
            "/settings/servers/create",
            data={
                "server_name": "Music",
                "server_type": "navidrome",
                "server_url": "http://nav.local:4533",
                "server_username": "wizarr",
                "server_password": "wrong",
            },
        ).get_data(as_text=True)

    html = " ".join(html.split())
    assert "refused" in html
    assert 'id="credential-options" style' not in html
    assert 'id="api-key-div" style="display:none;"' in html
    assert 'value="wizarr"' in html


def test_a_type_with_an_api_key_draws_the_api_key_field(admin_client):
    with patch(
        "app.blueprints.media_servers.routes.check_server_connection",
        return_value=(False, "no"),
    ):
        html = admin_client.post(
            "/settings/servers/create",
            data={
                "server_name": "P",
                "server_type": "plex",
                "server_url": "http://plex.local",
                "api_key": "x",
            },
        ).get_data(as_text=True)

    html = " ".join(html.split())
    assert 'id="api-key-div" style' not in html
    assert 'id="credential-options" style="display:none;"' in html


def test_create_trims_the_url_and_names_the_chosen_libraries(admin_client, session):
    with (
        patch(
            "app.blueprints.media_servers.routes.check_server_connection",
            return_value=(True, ""),
        ),
        patch(
            "app.blueprints.media_servers.routes.scan_libraries_for_server",
            return_value=({"1": "Music", "2": "Liked Songs", "3": "Yoga"}, True),
        ),
        patch("app.blueprints.media_servers.routes.list_users_for_server"),
    ):
        admin_client.post(
            "/settings/servers/create",
            data={
                "server_name": "Music",
                "server_type": "navidrome",
                "server_url": " http://nav.local:4533",
                "server_username": "wizarr",
                "server_password": "s3cret",
                "libraries": ["1", "2"],
            },
        )

    server = MediaServer.query.filter_by(server_type="navidrome").one()
    assert server.url == "http://nav.local:4533"
    libs = {
        lib.name: lib.enabled
        for lib in db.session.query(Library).filter_by(server_id=server.id)
    }
    assert libs == {"Music": True, "Liked Songs": True, "Yoga": False}
