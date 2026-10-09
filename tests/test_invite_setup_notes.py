"""Each server's "After sign-up, tell them" note is the setup help.

It shows after the step that set the server up (Plex included), on a
"Getting started" page that the post-join wizard opens first, for the
servers this person actually set up, and Wizarr's stock setup pages only
cover set-up servers that have no note.
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


def test_plex_step_shows_its_servers_notes(client, setup_done, monkeypatch):
    atp = _server("AllThePopcorn", "plex", "Install Plex, then open AllThePopcorn.")
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
    assert "Install Plex, then open AllThePopcorn." in html
    assert "Back to your checklist" in html


def test_getting_started_needs_this_invites_wizard_access(client, setup_done):
    _invite(_server("A", "audiobookshelf", "note"))
    assert "Invalid invite" in client.get("/j/NOTES123/setup").get_data(as_text=True)


def test_getting_started_lists_only_what_was_set_up(client, setup_done):
    atp = _server("AllThePopcorn", "plex", "ATP help")
    music = _server("Timevortex", "plex", "Music help")
    abs_server = _server("ABS", "audiobookshelf", "ABS help")
    invitation = _invite(atp, music, abs_server)
    plex_step = invite_steps.find_step(invitation, "plex")
    assert plex_step is not None
    invite_steps.record(invitation, plex_step, "done")
    account = invite_steps.find_step(invitation, "account")
    assert account is not None
    invite_steps.record(invitation, account, "skipped")
    _wizard_access(client)

    html = client.get("/j/NOTES123/setup").get_data(as_text=True)

    assert "ATP help" in html
    assert "Music help" in html
    assert "ABS help" not in html
    assert ">Done<" in html.replace("\n", "").replace(" ", "")


def test_the_wizard_opens_the_notes_first_then_only_stock_pages_without(
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

    first = client.get("/wizard/post-wizard")
    assert first.headers["Location"].endswith("/j/NOTES123/setup")
    page = client.get("/j/NOTES123/setup").get_data(as_text=True)
    assert "More setup help" in page

    second = client.get("/wizard/post-wizard")
    assert "/j/NOTES123/setup" not in second.headers.get("Location", "")
    with client.session_transaction() as sess:
        assert sess.get("wizard_notes_seen") == "NOTES123"


def test_when_every_server_has_a_note_the_wizard_ends_after_them(client, setup_done):
    atp = _server("AllThePopcorn", "plex", "ATP help")
    invitation = _invite(atp, code="ONLYATP1")
    _wizard_access(client, "ONLYATP1")
    assert not invite_steps.uses_steps(invitation)

    first = client.get("/wizard/post-wizard")
    assert first.headers["Location"].endswith("/j/ONLYATP1/setup")
    client.get("/j/ONLYATP1/setup")
    second = client.get("/wizard/post-wizard")
    assert second.headers["Location"].endswith("/wizard/complete")
