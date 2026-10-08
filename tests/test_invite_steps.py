"""A single-use invite for several services is a checklist of steps.

Plex is one step; every other server is its own step. Each step can be done or
skipped, progress is stored on the server against the invitation (so the link
resumes on any device), a done step can't be run again, and the checklist
replaces the combined sign-up only for single-use invites.
"""

import datetime
import re

import pytest

from app.extensions import db
from app.models import (
    AdminAccount,
    Invitation,
    InvitationProgress,
    MediaServer,
    Settings,
    User,
)
from app.services import invite_steps


@pytest.fixture
def setup_done(session):
    admin = AdminAccount(username="admin")
    admin.set_password("password")
    db.session.add_all([admin, Settings(key="admin_username", value="admin")])
    db.session.commit()


def _server(name, server_type):
    server = MediaServer(
        name=name, server_type=server_type, url="http://x", api_key="k"
    )
    db.session.add(server)
    db.session.flush()
    return server


def _invite(*servers, code="STEPS123", unlimited=False, expires=None):
    invitation = Invitation(code=code, used=False, unlimited=unlimited, expires=expires)
    for server in servers:
        invitation.servers.append(server)
    db.session.add(invitation)
    db.session.commit()
    return invitation


@pytest.fixture
def plex_abs(setup_done):
    plex = _server("AllThePopcorn", "plex")
    abs_server = _server("AudioBookShelf", "audiobookshelf")
    return _invite(plex, abs_server), plex, abs_server


class _Client:
    """Stands in for a media server: join makes the account locally."""

    def __init__(self, server):
        self.server = server
        self.joins = 0

    def join(self, username, password, confirm, email, code):
        self.joins += 1
        db.session.add(
            User(
                token=f"tok-{username}",
                username=username,
                email=email,
                code=code,
                server_id=self.server.id,
            )
        )
        db.session.commit()
        return True, "ok"


@pytest.fixture
def fake_media(monkeypatch):
    clients = {}

    def client_for(server):
        return clients.setdefault(server.id, _Client(server))

    monkeypatch.setattr(
        "app.services.invitation_flow.workflows.get_client_for_media_server",
        client_for,
    )
    return clients


@pytest.fixture
def fake_plex(monkeypatch):
    calls = []

    def handle_oauth_token(app, token, code):
        calls.append(token)
        invitation = invite_steps.find_invitation(code)
        assert invitation is not None
        for step in invite_steps.steps_for(invitation):
            for server in step.servers if step.is_plex else ():
                db.session.add(
                    User(
                        token=token,
                        username="plexsam",
                        email="sam@example.com",
                        code=code,
                        server_id=server.id,
                    )
                )
        db.session.commit()

    monkeypatch.setattr(
        "app.services.media.plex.handle_oauth_token", handle_oauth_token
    )
    return calls


def _states(invitation):
    return {
        row.server_id: row.state
        for row in InvitationProgress.query.filter_by(invitation_id=invitation.id)
    }


# ── which invites are a checklist ──────────────────────────────────────────


def test_single_use_invite_for_two_services_uses_steps(plex_abs):
    invitation, plex, abs_server = plex_abs
    steps = invite_steps.steps_for(invitation)
    assert [s.key for s in steps] == ["plex", str(abs_server.id)]
    assert invite_steps.uses_steps(invitation)


def test_all_plex_servers_are_one_step(setup_done):
    invitation = _invite(_server("A", "plex"), _server("B", "plex"))
    assert [s.key for s in invite_steps.steps_for(invitation)] == ["plex"]
    assert not invite_steps.uses_steps(invitation)


def test_unlimited_and_single_service_invites_keep_their_flow(setup_done):
    assert not invite_steps.uses_steps(
        _invite(_server("P", "plex"), _server("A", "audiobookshelf"), unlimited=True)
    )
    assert not invite_steps.uses_steps(
        _invite(_server("J", "jellyfin"), code="ONLYONE1")
    )


def test_link_opens_the_checklist(client, plex_abs):
    resp = client.get("/j/STEPS123")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/j/STEPS123/steps")


def test_unlimited_link_does_not(client, setup_done):
    _invite(_server("P", "plex"), _server("A", "audiobookshelf"), unlimited=True)
    resp = client.get("/j/STEPS123")
    assert "/steps" not in resp.headers.get("Location", "")


# ── the checklist ───────────────────────────────────────────────────────────


def test_checklist_lists_each_service_to_do(client, plex_abs):
    html = client.get("/j/STEPS123/steps").get_data(as_text=True)
    assert "Set up your access" in html
    assert html.index("AllThePopcorn") < html.index("AudioBookShelf")
    assert html.count('data-state="todo"') == 2


def test_plex_step_signs_in_and_ticks(client, plex_abs, fake_plex):
    invitation, plex, _abs = plex_abs
    page = client.get("/j/STEPS123/steps/plex").get_data(as_text=True)
    assert 'action="/j/STEPS123/steps/plex"' in page

    resp = client.post("/j/STEPS123/steps/plex", data={"token": "plex-token"})
    assert resp.headers["Location"].endswith("/j/STEPS123/steps")
    assert fake_plex == ["plex-token"]
    assert _states(invitation) == {plex.id: "done"}
    row = InvitationProgress.query.filter_by(server_id=plex.id).one()
    assert row.user_id == User.query.filter_by(server_id=plex.id).one().id


def test_a_done_step_cannot_run_again(client, plex_abs, fake_plex):
    client.post("/j/STEPS123/steps/plex", data={"token": "first"})
    resp = client.post("/j/STEPS123/steps/plex", data={"token": "second"})
    assert resp.headers["Location"].endswith("/j/STEPS123/steps")
    assert fake_plex == ["first"]
    again = client.get("/j/STEPS123/steps/plex")
    assert again.status_code == 302


def test_next_step_is_prefilled_from_the_earlier_account(client, plex_abs, fake_plex):
    _invitation, _plex, abs_server = plex_abs
    client.post("/j/STEPS123/steps/plex", data={"token": "t"})
    page = client.get(f"/j/STEPS123/steps/{abs_server.id}").get_data(as_text=True)
    assert f'action="/j/STEPS123/steps/{abs_server.id}"' in page
    assert 'value="plexsam"' in page
    assert 'value="sam@example.com"' in page


def test_form_step_creates_the_account_on_that_server_only(
    client, plex_abs, fake_media
):
    invitation, plex, abs_server = plex_abs
    resp = client.post(
        f"/j/STEPS123/steps/{abs_server.id}",
        data={
            "code": "STEPS123",
            "username": "listener",
            "email": "listener@example.com",
            "password": "Password123",
            "confirm_password": "Password123",
        },
    )
    assert resp.headers["Location"].endswith("/j/STEPS123/steps")
    assert list(fake_media) == [abs_server.id]
    assert _states(invitation) == {abs_server.id: "done"}


def test_form_step_errors_stay_on_the_step(client, plex_abs, fake_media):
    _invitation, _plex, abs_server = plex_abs
    resp = client.post(
        f"/j/STEPS123/steps/{abs_server.id}",
        data={"code": "STEPS123", "username": "x"},
    )
    assert resp.status_code == 200
    assert f'action="/j/STEPS123/steps/{abs_server.id}"' in resp.get_data(as_text=True)
    assert fake_media == {}


def test_skip_then_set_up_later(client, plex_abs, fake_media):
    invitation, _plex, abs_server = plex_abs
    client.post(f"/j/STEPS123/steps/{abs_server.id}/skip")
    assert _states(invitation) == {abs_server.id: "skipped"}
    assert client.get(f"/j/STEPS123/steps/{abs_server.id}").status_code == 200
    client.post(
        f"/j/STEPS123/steps/{abs_server.id}",
        data={
            "code": "STEPS123",
            "username": "listener",
            "email": "listener@example.com",
            "password": "Password123",
            "confirm_password": "Password123",
        },
    )
    assert _states(invitation) == {abs_server.id: "done"}


def test_skipping_a_done_step_changes_nothing(client, plex_abs, fake_plex):
    invitation, plex, _abs = plex_abs
    client.post("/j/STEPS123/steps/plex", data={"token": "t"})
    client.post("/j/STEPS123/steps/plex/skip")
    assert _states(invitation) == {plex.id: "done"}


def test_progress_follows_the_link_to_another_device(app, client, plex_abs, fake_plex):
    client.post("/j/STEPS123/steps/plex", data={"token": "t"})
    other_device = app.test_client()
    html = other_device.get("/j/STEPS123/steps").get_data(as_text=True)
    assert re.search(r'data-step="plex"\s+data-state="done"', html)
    assert html.count('data-state="todo"') == 1


def test_finish_goes_to_the_wizard_only_when_nothing_is_left(
    client, plex_abs, fake_plex
):
    _invitation, _plex, abs_server = plex_abs
    client.post("/j/STEPS123/steps/plex", data={"token": "t"})
    early = client.post("/j/STEPS123/steps/finish")
    assert early.headers["Location"].endswith("/j/STEPS123/steps")

    client.post(f"/j/STEPS123/steps/{abs_server.id}/skip")
    html = client.get("/j/STEPS123/steps").get_data(as_text=True)
    assert "You're all set." in html
    done = client.post("/j/STEPS123/steps/finish")
    assert done.headers["Location"].endswith("/wizard/")
    with client.session_transaction() as sess:
        assert sess["wizard_access"] == "STEPS123"


def test_finished_invite_link_shows_the_ticks(client, plex_abs, fake_plex):
    invitation, _plex, abs_server = plex_abs
    client.post("/j/STEPS123/steps/plex", data={"token": "t"})
    abs_step = invite_steps.find_step(invitation, str(abs_server.id))
    assert abs_step is not None
    invite_steps.record(invitation, abs_step, "done")
    invitation.used = True
    db.session.commit()
    resp = client.get("/j/STEPS123")
    assert resp.headers["Location"].endswith("/j/STEPS123/steps")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/j/STEPS123/steps"),
        ("GET", "/j/STEPS123/steps/plex"),
        ("POST", "/j/STEPS123/steps/plex"),
        ("POST", "/j/STEPS123/steps/plex/skip"),
        ("POST", "/j/STEPS123/steps/finish"),
    ],
)
def test_expired_or_unknown_invites_are_refused(client, setup_done, method, path):
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)
    _invite(_server("P", "plex"), _server("A", "audiobookshelf"), expires=past)
    resp = client.open(path, method=method, data={"token": "t"})
    assert resp.status_code == 200
    assert "Invalid invite" in resp.get_data(as_text=True)
    assert (
        client.open(path.replace("STEPS123", "NOPE0000"), method=method).status_code
        == 200
    )


def test_unknown_step_goes_back_to_the_checklist(client, plex_abs):
    resp = client.get("/j/STEPS123/steps/999")
    assert resp.headers["Location"].endswith("/j/STEPS123/steps")


# ── admin ───────────────────────────────────────────────────────────────────


def test_invite_card_shows_the_ticks(client, plex_abs, fake_plex):
    client.post("/j/STEPS123/steps/plex", data={"token": "t"})
    admin_client = client.application.test_client()
    admin_client.post("/login", data={"username": "admin", "password": "password"})
    html = admin_client.post("/invite/table", headers={"HX-Request": "true"}).get_data(
        as_text=True
    )
    assert "✓ set up" in html


def test_prefill_is_only_for_the_browser_that_did_the_step(
    app, client, plex_abs, fake_plex
):
    _invitation, _plex, abs_server = plex_abs
    client.post("/j/STEPS123/steps/plex", data={"token": "t"})
    stranger = app.test_client()
    page = stranger.get(f"/j/STEPS123/steps/{abs_server.id}").get_data(as_text=True)
    assert "sam@example.com" not in page
    assert "plexsam" not in page
