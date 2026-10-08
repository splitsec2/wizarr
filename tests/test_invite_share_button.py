"""The created-invite modal offers Share beside Copy. The button starts hidden
and the page shows it only where the browser has a share sheet."""

from app.models import AdminAccount, MediaServer

HX = {"HX-Request": "true"}


def _created_invite_html(client, session):
    admin = AdminAccount(username="testadmin")
    admin.set_password("TestPass123")
    session.add(admin)
    server = MediaServer(
        name="Plex", server_type="plex", url="http://plex.local", api_key="token"
    )
    session.add(server)
    session.commit()
    client.post("/login", data={"username": "testadmin", "password": "TestPass123"})
    resp = client.post(
        "/invite",
        data={"server_ids": str(server.id), "expires": "never"},
        headers=HX,
    )
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_share_button_sits_beside_copy_and_starts_hidden(client, session):
    html = _created_invite_html(client, session)
    copy_at = html.index('onclick="copyLink()"')
    share_at = html.index('id="share_link"')
    assert copy_at < share_at
    button = html[html.rindex("<button", 0, share_at) : html.index(">", share_at)]
    assert "hidden" in button


def test_share_uses_the_share_sheet_with_the_link(client, session):
    html = _created_invite_html(client, session)
    assert "navigator.share" in html
    assert "url: link" in html
    assert "your invitation. Open the link to get set up." in html
    assert 'err.name !== "AbortError"' in html
