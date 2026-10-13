"""A server type that signs in with a username and password is handled once.

RomM was the only type that took a username and password, and each place that
saves or checks a server carried its own copy of "if romm, pack them into the API
key". A client now declares ``capabilities.credentials`` and the create, edit and
scan routes pack them the same way for any type that does: RomM and Navidrome.
"""

from unittest.mock import patch

import pytest

from app.models import AdminAccount, MediaServer
from app.services.media.client_base import (
    credential_server_types,
    credentials_api_key,
    unpack_credentials,
)


@pytest.fixture
def admin_client(client, session):
    admin = AdminAccount(username="testadmin")
    admin.set_password("TestPass123")
    session.add(admin)
    session.commit()
    client.post("/login", data={"username": "testadmin", "password": "TestPass123"})
    return client


def test_clients_that_declare_credentials():
    types = credential_server_types()
    assert {"romm", "navidrome"} <= set(types)
    assert "plex" not in types


def test_credentials_are_packed_only_for_those_types_and_only_when_complete():
    form = {"server_username": " admin ", "server_password": "s3cret"}

    assert unpack_credentials(credentials_api_key("navidrome", form)) == (
        "admin",
        "s3cret",
    )
    assert unpack_credentials(credentials_api_key("romm", form)) == ("admin", "s3cret")
    assert credentials_api_key("plex", form) is None
    assert credentials_api_key("navidrome", {"server_username": "admin"}) is None
    assert credentials_api_key("navidrome", {}) is None


@pytest.mark.parametrize("server_type", ["navidrome", "romm"])
def test_create_saves_the_packed_credentials(admin_client, session, server_type):
    with patch(
        "app.blueprints.media_servers.routes.check_server_connection",
        return_value=(True, ""),
    ) as check:
        admin_client.post(
            "/settings/servers/create",
            data={
                "server_name": "Music",
                "server_type": server_type,
                "server_url": "http://nav.local:4533",
                "server_username": "wizarr",
                "server_password": "s3cret",
            },
        )

    server = MediaServer.query.filter_by(server_type=server_type).one()
    assert server.api_key is not None
    assert unpack_credentials(server.api_key) == ("wizarr", "s3cret")
    assert unpack_credentials(check.call_args.args[2]) == ("wizarr", "s3cret")


def test_a_type_with_an_api_key_keeps_it(admin_client, session):
    with patch(
        "app.blueprints.media_servers.routes.check_server_connection",
        return_value=(True, ""),
    ):
        admin_client.post(
            "/settings/servers/create",
            data={
                "server_name": "ABS",
                "server_type": "audiobookshelf",
                "server_url": "http://abs.local",
                "api_key": "plain-token",
                "server_username": "ignored",
                "server_password": "ignored",
            },
        )

    assert MediaServer.query.one().api_key == "plain-token"


def test_the_server_modal_shows_the_credential_fields_for_those_types(admin_client):
    html = admin_client.get(
        "/settings/servers/create", headers={"HX-Request": "true"}
    ).get_data(as_text=True)

    assert 'id="credential-options"' in html
    assert '["navidrome", "romm"]' in html or '["navidrome","romm"]' in html
