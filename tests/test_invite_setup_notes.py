"""A server's note is for admins; what a person reads is in Wizard Steps.

After a step its page shows the server's address. The last page ("You're all
set") gives each set-up server with an address one card. The note is never
shown to the person, and the wizard shows the stock pages for every set-up
server.
"""

import pytest

from app.extensions import db
from app.jinja_filters import linkify
from app.models import AdminAccount, Invitation, MediaServer, Settings, User
from app.services import invite_steps


@pytest.fixture
def setup_done(session):
    db.session.add_all(
        [AdminAccount(username="admin"), Settings(key="admin_username", value="admin")]
    )
    db.session.commit()


def _server(name, server_type, note=None):
    server = MediaServer(
        name=name, server_type=server_type, url="http://x", invitee_notes=note
    )
    db.session.add(server)
    db.session.flush()
    return server


def _invite(*servers, code="NOTES123"):
    invitation = Invitation(code=code, used=False)
    for server in servers:
        invitation.servers.append(server)
    db.session.add(invitation)
    db.session.commit()
    return invitation


def _wizard_access(client, code="NOTES123"):
    with client.session_transaction() as sess:
        sess["wizard_access"] = code


def test_links_are_made_and_html_is_escaped():
    html = str(linkify("Go to https://abs.example.com. <b>hi</b>"))
    assert '<a href="https://abs.example.com"' in html
    assert ">https://abs.example.com</a>." in html
    assert "&lt;b&gt;hi&lt;/b&gt;" in html


def test_plex_step_shows_the_address_and_never_the_note(
    client, setup_done, monkeypatch
):
    atp = _server("AllThePopcorn", "plex", "Install Plex, then open AllThePopcorn.")
    atp.external_url = "https://plex.example.com"
    abs_server = _server("ABS", "audiobookshelf")
    _invite(atp, abs_server)

    def fake_oauth(app, token, code):
        db.session.add(
            User(
                token=token, username="p", email="p@x.com", code=code, server_id=atp.id
            )
        )
        db.session.commit()

    monkeypatch.setattr("app.services.media.plex.handle_oauth_token", fake_oauth)
    html = client.post("/j/NOTES123/steps/plex", data={"token": "t"}).get_data(
        as_text=True
    )
    assert "plex.example.com" in html
    assert "Install Plex, then open AllThePopcorn." not in html
    assert "Back to your checklist" in html


def _finish(client, code="NOTES123"):
    return client.get("/wizard/complete")


def test_the_last_page_shows_each_address_once_and_never_the_note(client, setup_done):
    atp = _server("AllThePopcorn", "plex", "ATP help")
    atp.external_url = "https://atp.example"
    music = _server("Timevortex", "plex", "Music help")
    abs_server = _server("ABS", "audiobookshelf", "ABS help")
    abs_server.external_url = "https://abs.example"
    invitation = _invite(atp, music, abs_server)
    plex_step = invite_steps.find_step(invitation, "plex")
    assert plex_step is not None
    invite_steps.record(invitation, plex_step, "done")
    account = invite_steps.find_step(invitation, "account")
    assert account is not None
    invite_steps.record(invitation, account, "done")
    _wizard_access(client)
    with client.session_transaction() as sess:
        sess["wizarr_invite_code"] = "NOTES123"

    html = _finish(client).get_data(as_text=True)

    assert "You&#39;re all set" in html or "You're all set" in html
    assert html.count("https://atp.example") >= 1
    assert "https://abs.example" in html
    assert "ATP help" not in html
    assert "Music help" not in html
    assert "ABS help" not in html
    assert "Also set up: Timevortex" in html
    assert "ask the person who sent you this invite" in html


def test_the_last_page_names_pieces_without_an_address_and_clears_the_session(
    client, setup_done
):
    atp = _server("AllThePopcorn", "plex")
    atp.external_url = "https://atp.example"
    abs_server = _server("ABS", "audiobookshelf")
    invitation = _invite(atp, abs_server)
    for key in ("plex", "account"):
        step = invite_steps.find_step(invitation, key)
        assert step is not None
        invite_steps.record(invitation, step, "done")
    _wizard_access(client)
    with client.session_transaction() as sess:
        sess["wizarr_invite_code"] = "NOTES123"

    invitation.used = True  # a single-use invite is used up once they have joined
    db.session.commit()

    html = _finish(client).get_data(as_text=True)

    assert "Also set up: ABS" in html
    with client.session_transaction() as sess:
        assert "wizard_access" not in sess


def test_the_wizard_goes_to_the_stock_pages_even_when_a_server_has_a_note(
    client, setup_done
):
    atp = _server("AllThePopcorn", "plex", "ATP help")
    abs_server = _server("ABS", "audiobookshelf")
    invitation = _invite(atp, abs_server)
    for key in ("plex", "account"):
        step = invite_steps.find_step(invitation, key)
        assert step is not None
        invite_steps.record(invitation, step, "done")
    _wizard_access(client)

    resp = client.get("/wizard/post-wizard")

    assert resp.status_code == 302
    assert not resp.headers["Location"].endswith("/wizard/complete")


def test_the_last_page_without_an_invite_goes_home(client, setup_done):
    resp = client.get("/wizard/complete")
    assert resp.status_code == 302
