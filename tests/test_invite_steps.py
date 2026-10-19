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

    def handle_oauth_token(app, token, code, person_email=None):
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


def _verified(client, code, email):
    """As if this browser proved *email* with an emailed code for invite *code*."""
    with client.session_transaction() as sess:
        sess["invite_steps_verified"] = {code.lower(): email}


def _states(invitation):
    return {
        row.server_id: row.state
        for row in InvitationProgress.query.filter_by(invitation_id=invitation.id)
    }


# ── which invites are a checklist ──────────────────────────────────────────


def test_single_use_invite_for_two_services_uses_steps(plex_abs):
    invitation, plex, abs_server = plex_abs
    steps = invite_steps.steps_for(invitation)
    assert [s.key for s in steps] == ["plex", "account"]
    assert invite_steps.uses_steps(invitation)


def test_all_plex_servers_are_one_step(setup_done):
    invitation = _invite(_server("A", "plex"), _server("B", "plex"))
    assert [s.key for s in invite_steps.steps_for(invitation)] == ["plex"]
    assert invite_steps.uses_steps(invitation)


def test_a_shared_invite_needs_mail_but_a_single_service_invite_does_not(setup_done):
    assert not invite_steps.uses_steps(
        _invite(_server("P", "plex"), _server("A", "audiobookshelf"), unlimited=True)
    )
    assert invite_steps.uses_steps(_invite(_server("J", "jellyfin"), code="ONLYONE1"))


def test_link_opens_the_welcome_first(client, plex_abs):
    resp = client.get("/j/STEPS123")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/j/STEPS123/welcome")


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
    _verified(client, "STEPS123", "sam@example.com")
    page = client.get("/j/STEPS123/steps/account").get_data(as_text=True)
    assert 'action="/j/STEPS123/steps/account"' in page
    assert 'value="plexsam"' in page
    assert 'value="sam@example.com"' in page


def test_form_step_creates_the_account_on_that_server_only(
    client, plex_abs, fake_media
):
    invitation, plex, abs_server = plex_abs
    _verified(client, "STEPS123", "listener@example.com")
    resp = client.post(
        "/j/STEPS123/steps/account",
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
    _verified(client, "STEPS123", "listener@example.com")
    resp = client.post(
        "/j/STEPS123/steps/account",
        data={"code": "STEPS123", "username": "x"},
    )
    assert resp.status_code == 200
    assert 'action="/j/STEPS123/steps/account"' in resp.get_data(as_text=True)
    assert fake_media == {}


def test_skip_then_set_up_later(client, plex_abs, fake_media):
    invitation, _plex, abs_server = plex_abs
    _verified(client, "STEPS123", "listener@example.com")
    client.post("/j/STEPS123/steps/account/skip")
    assert _states(invitation) == {abs_server.id: "skipped"}
    assert client.get("/j/STEPS123/steps/account").status_code == 200
    client.post(
        "/j/STEPS123/steps/account",
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

    client.post("/j/STEPS123/steps/account/skip")
    html = client.get("/j/STEPS123/steps").get_data(as_text=True)
    assert "You're all set." in html
    done = client.post("/j/STEPS123/steps/finish")
    assert done.headers["Location"].endswith("/wizard/")
    with client.session_transaction() as sess:
        assert sess["wizard_access"] == "STEPS123"


def test_finished_invite_link_shows_the_ticks(client, plex_abs, fake_plex):
    invitation, _plex, abs_server = plex_abs
    client.post("/j/STEPS123/steps/plex", data={"token": "t"})
    abs_step = invite_steps.find_step(invitation, "account")
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
    page = stranger.get("/j/STEPS123/steps/account").get_data(as_text=True)
    assert "sam@example.com" not in page
    assert "plexsam" not in page


def test_step_pages_link_back_to_the_checklist(client, plex_abs):
    _invitation, _plex, abs_server = plex_abs
    _verified(client, "STEPS123", "sam@example.com")
    for key in ("plex", "account"):
        page = client.get(f"/j/STEPS123/steps/{key}").get_data(as_text=True)
        assert 'href="/j/STEPS123/steps"' in page
        assert "Back to your checklist" in page


def test_ordinary_join_pages_have_no_back_link(client, setup_done):
    _invite(_server("J", "jellyfin"), code="PLAIN123")
    page = client.get("/j/PLAIN123").get_data(as_text=True)
    assert "Back to your checklist" not in page


def test_users_show_how_far_their_invite_got(client, plex_abs, fake_plex):
    _invitation, plex, _abs = plex_abs
    client.post("/j/STEPS123/steps/plex", data={"token": "t"})
    user = User.query.filter_by(server_id=plex.id).one()
    assert user.invite_setup() == [
        ("AllThePopcorn", "done"),
        ("AudioBookShelf", "todo"),
    ]
    admin_client = client.application.test_client()
    admin_client.post("/login", data={"username": "admin", "password": "password"})
    html = admin_client.get("/users/table", headers={"HX-Request": "true"}).get_data(
        as_text=True
    )
    assert "AllThePopcorn ✓" in html
    assert "AudioBookShelf (to do)" in html


def test_the_setup_line_lists_every_invite_the_person_has(client, plex_abs, fake_plex):
    _invitation, plex, _abs = plex_abs
    client.post("/j/STEPS123/steps/plex", data={"token": "t"})
    first = User.query.filter_by(server_id=plex.id).one()
    # A second invite for another service, joined by the same person.
    other = _server("Navidrome", "navidrome")
    _invite(other, code="SECOND12")
    second = User(
        token="tok-second",
        username="plexsam",
        email=first.email,
        code="SECOND12",
        server_id=other.id,
    )
    db.session.add(second)
    db.session.commit()
    first.accounts = [first, second]
    assert first.invite_setup() == [
        ("AllThePopcorn", "done"),
        ("AudioBookShelf", "todo"),
        ("Navidrome", "todo"),
    ]


def _name_block(html: str) -> str:
    """The text inside the first card's name block (the div around the name, the
    email and the invite lines)."""
    from html.parser import HTMLParser

    class Grab(HTMLParser):
        def __init__(self):
            super().__init__()
            self.depth = 0
            self.parts: list[str] = []
            self.done = False

        def handle_starttag(self, tag, attrs):
            if tag != "div" or self.done:
                return
            if self.depth:
                self.depth += 1
            elif "min-w-0" in dict(attrs).get("class", "").split():
                self.depth = 1

        def handle_endtag(self, tag):
            if tag == "div" and self.depth:
                self.depth -= 1
                self.done = self.depth == 0

        def handle_data(self, data):
            if self.depth and not self.done:
                self.parts.append(data)

    grab = Grab()
    grab.feed(html)
    return " ".join(" ".join(grab.parts).split())


def test_the_card_keeps_the_email_and_invite_lines_under_the_name(
    client, plex_abs, fake_plex
):
    """The invite lines used to sit beside the avatar, which squeezed the name
    and the email away. They belong in the same block, under the email."""
    _invitation, plex, _abs = plex_abs
    client.post("/j/STEPS123/steps/plex", data={"token": "t"})
    admin_client = client.application.test_client()
    admin_client.post("/login", data={"username": "admin", "password": "password"})
    html = admin_client.get("/users/table", headers={"HX-Request": "true"}).get_data(
        as_text=True
    )

    block = _name_block(html)
    assert "sam@example.com" in block
    assert "AllThePopcorn" in block
    assert "Set up:" in block


def test_the_avatar_is_one_initial(client, plex_abs, fake_plex):
    client.post("/j/STEPS123/steps/plex", data={"token": "t"})
    admin_client = client.application.test_client()
    admin_client.post("/login", data={"username": "admin", "password": "password"})
    html = admin_client.get("/users/table", headers={"HX-Request": "true"}).get_data(
        as_text=True
    )
    initials = re.findall(r"rounded-full bg-primary/10[^>]*>\s*(\S+)\s*<", html)
    assert initials
    assert all(len(letters) == 1 for letters in initials)


def test_accounts_from_ordinary_invites_show_no_setup(session):
    assert User(username="x", code=None).invite_setup() == []


# ── one-service invites ────────────────────────────────────────────────────
# A single-use invite for one service that takes an email used to skip the
# checklist, so nobody proved the address. It opens the checklist when Settings >
# Email can send the code; with no mail set up (or nothing to prove) it keeps the
# plain sign-up.


@pytest.fixture
def mail_ready(monkeypatch):
    monkeypatch.setattr("app.services.mailer.is_configured", lambda: True)


@pytest.mark.parametrize(
    "server_type", ["audiobookshelf", "jellyfin", "provisioning_hook"]
)
def test_one_service_invite_opens_the_checklist_when_mail_can_send(
    setup_done, mail_ready, server_type
):
    invitation = _invite(_server("Only", server_type), code="ONLYONE2")
    assert [s.key for s in invite_steps.steps_for(invitation)] == ["account"]
    assert invite_steps.uses_steps(invitation)


def test_one_service_invite_uses_the_checklist_without_mail(setup_done):
    invitation = _invite(_server("Only", "audiobookshelf"), code="ONLYONE3")
    assert invite_steps.uses_steps(invitation)


def test_a_plex_only_invite_uses_the_checklist(setup_done, mail_ready):
    invitation = _invite(_server("Plex", "plex"), code="PLEXONE1")
    assert invite_steps.uses_steps(invitation)


def test_a_one_row_checklist_opens_its_row_once(client, setup_done):
    _invite(_server("Only", "audiobookshelf"), code="ONEROW01")

    first = client.get("/j/ONEROW01/steps")
    assert first.status_code == 302
    assert first.headers["Location"].endswith("/j/ONEROW01/steps/account")

    again = client.get("/j/ONEROW01/steps")
    assert again.status_code == 200
    assert "/j/ONEROW01/steps/account" in again.get_data(as_text=True)


# ── a join that fails is shown as failed ───────────────────────────────────


class _FailingClient:
    def __init__(self, server):
        self.server = server

    def join(self, *_args, **_kwargs):
        return False, "refused"


@pytest.fixture
def failing_media(monkeypatch):
    monkeypatch.setattr(
        "app.services.invitation_flow.workflows.get_client_for_media_server",
        lambda server: _FailingClient(server),
    )


def _post_account(client):
    return client.post(
        "/j/STEPS123/steps/account",
        data={
            "code": "STEPS123",
            "username": "listener",
            "email": "listener@example.com",
            "password": "Password123",
            "confirm_password": "Password123",
        },
    )


def test_a_failed_join_is_recorded_as_failed(client, plex_abs, failing_media):
    invitation, _plex, abs_server = plex_abs
    _verified(client, "STEPS123", "listener@example.com")

    resp = _post_account(client)

    assert resp.status_code == 200
    assert "could not be set up" in resp.get_data(as_text=True)
    assert _states(invitation) == {abs_server.id: "failed"}


def test_a_failed_step_is_still_to_do_for_the_invitee(client, plex_abs, failing_media):
    invitation, _plex, abs_server = plex_abs
    _verified(client, "STEPS123", "listener@example.com")
    _post_account(client)

    steps = invite_steps.steps_for(invitation)
    states = invite_steps.server_states(invitation)
    account = next(s for s in steps if s.is_account)
    assert invite_steps.state_of(account, states) is None
    page = client.get("/j/STEPS123/steps").get_data(as_text=True)
    assert "Nothing is set up yet" not in page
    assert "You're all set" not in page


def test_a_retry_that_works_replaces_the_failure(
    client, plex_abs, failing_media, monkeypatch
):
    invitation, _plex, abs_server = plex_abs
    _verified(client, "STEPS123", "listener@example.com")
    _post_account(client)
    monkeypatch.setattr(
        "app.services.invitation_flow.workflows.get_client_for_media_server",
        lambda server: _Client(server),
    )

    _post_account(client)

    assert _states(invitation) == {abs_server.id: "done"}


def test_the_invite_card_and_the_users_card_say_failed(client, plex_abs, failing_media):
    _invitation, plex, abs_server = plex_abs
    db.session.add(
        User(
            token="plex-tok",
            username="plexsam",
            email="sam@example.com",
            code="STEPS123",
            server_id=plex.id,
        )
    )
    db.session.commit()
    _verified(client, "STEPS123", "listener@example.com")
    _post_account(client)
    admin_client = client.application.test_client()
    admin_client.post("/login", data={"username": "admin", "password": "password"})
    hx = {"HX-Request": "true"}

    users = admin_client.get("/users/table", headers=hx).get_data(as_text=True)
    invites = admin_client.post("/invite/table", headers=hx).get_data(as_text=True)

    assert "AudioBookShelf (failed)" in users
    assert "failed to set up" in invites


# ── a service the person already holds ─────────────────────────────────────


def _holds(server, email):
    db.session.add(
        User(
            token="held",
            username="held",
            email=email,
            code="OLDINVITE",
            server_id=server.id,
        )
    )
    db.session.commit()


@pytest.fixture
def abs_books(setup_done):
    abs_server = _server("AudioBookShelf", "audiobookshelf")
    books = _server("Books", "provisioning_hook")
    return _invite(abs_server, books), abs_server, books


def _account_form(email):
    return {
        "code": "STEPS123",
        "username": "listener",
        "email": email,
        "password": "Correct-Horse-9x",
        "confirm_password": "Correct-Horse-9x",
    }


def test_a_held_service_counts_as_done_and_the_step_completes(
    client, abs_books, fake_media
):
    invitation, abs_server, books = abs_books
    _holds(abs_server, "Listener@Example.com")
    _verified(client, "STEPS123", "listener@example.com")
    resp = client.post(
        "/j/STEPS123/steps/account", data=_account_form("listener@example.com")
    )
    assert resp.headers["Location"].endswith(
        "/wizard/"
    )  # one row: on to the instructions
    assert list(fake_media) == [books.id]
    assert _states(invitation) == {abs_server.id: "done", books.id: "done"}


def test_a_step_with_everything_held_completes_without_an_account(
    client, abs_books, fake_media
):
    invitation, abs_server, books = abs_books
    _holds(abs_server, "listener@example.com")
    _holds(books, "LISTENER@example.com")
    _verified(client, "STEPS123", "listener@example.com")
    resp = client.get("/j/STEPS123/steps/account")
    assert resp.headers["Location"].endswith("/wizard/")
    assert fake_media == {}
    assert _states(invitation) == {abs_server.id: "done", books.id: "done"}


def test_someone_elses_account_is_not_held(client, abs_books, fake_media):
    invitation, abs_server, _books = abs_books
    _holds(abs_server, "other@example.com")
    _verified(client, "STEPS123", "listener@example.com")
    client.post("/j/STEPS123/steps/account", data=_account_form("listener@example.com"))
    assert set(fake_media) == {abs_server.id, _books.id}


def _lapse(server, email, *, disabled, days_ago=3):
    user = User.query.filter(User.server_id == server.id, User.email == email).one()
    user.is_disabled = disabled
    user.expires = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
        days=days_ago
    )
    db.session.commit()
    return user.id


@pytest.fixture
def enable_ok(monkeypatch):
    from unittest.mock import Mock

    remote = Mock()
    remote.enable_user.return_value = True
    monkeypatch.setattr(
        "app.services.media.service.get_client_for_media_server", lambda _s: remote
    )
    return remote


def test_a_switched_off_held_account_is_renewed_and_switched_on(
    client, abs_books, fake_media, enable_ok
):
    invitation, abs_server, books = abs_books
    invitation.duration = "30"
    db.session.commit()
    _holds(abs_server, "listener@example.com")
    uid = _lapse(abs_server, "listener@example.com", disabled=True)
    _verified(client, "STEPS123", "listener@example.com")

    client.post("/j/STEPS123/steps/account", data=_account_form("listener@example.com"))

    user = db.session.get(User, uid)
    assert user is not None
    assert user.is_disabled is False
    ends = user.expires.replace(tzinfo=datetime.UTC)
    assert ends > datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=29)
    enable_ok.enable_user.assert_called_once()
    assert _states(invitation) == {abs_server.id: "done", books.id: "done"}


def test_an_expired_but_not_yet_disabled_held_account_gets_the_new_end(
    client, abs_books, fake_media, enable_ok
):
    invitation, abs_server, _books = abs_books
    invitation.duration = "30"
    db.session.commit()
    _holds(abs_server, "listener@example.com")
    uid = _lapse(abs_server, "listener@example.com", disabled=False)
    _verified(client, "STEPS123", "listener@example.com")

    client.post("/j/STEPS123/steps/account", data=_account_form("listener@example.com"))

    user = db.session.get(User, uid)
    assert user is not None
    assert user.expires is not None
    assert user.expires.replace(tzinfo=datetime.UTC) > datetime.datetime.now(
        datetime.UTC
    )
    enable_ok.enable_user.assert_not_called()
    assert _states(invitation)[abs_server.id] == "done"


def test_a_held_account_that_cannot_be_switched_on_stays_on_the_checklist(
    abs_books, enable_ok
):
    invitation, abs_server, books = abs_books
    invitation.duration = "30"
    db.session.commit()
    enable_ok.enable_user.return_value = False
    _holds(abs_server, "listener@example.com")
    uid = _lapse(abs_server, "listener@example.com", disabled=True)
    step = invite_steps.find_step(invitation, "account")
    assert step is not None

    invite_steps.record_held(invitation, step, "", "listener@example.com")

    assert _states(invitation) == {}
    held = db.session.get(User, uid)
    assert held is not None
    assert held.is_disabled is True


# ── how the Users card flags the access end ────────────────────────────────


def _cards(client):
    admin_client = client.application.test_client()
    admin_client.post("/login", data={"username": "admin", "password": "password"})
    html = admin_client.get("/users/table", headers={"HX-Request": "true"}).get_data(
        as_text=True
    )
    return re.findall(r'data-expiry="(\w+)"', html), html


def _expires(offset_days):
    user = User.query.filter_by(username="plexsam").first()
    assert user is not None
    user.expires = (
        None
        if offset_days is None
        else datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=offset_days)
    )
    db.session.commit()


@pytest.mark.parametrize(
    ("offset", "state", "border"),
    [(None, "none", "border-dashed"), (10, "soon", "border-red-500"), (90, "ok", None)],
)
def test_the_card_border_shows_when_access_ends(
    client, plex_abs, fake_plex, offset, state, border
):
    client.post("/j/STEPS123/steps/plex", data={"token": "t"})
    _expires(offset)

    states, html = _cards(client)

    assert states and set(states) == {state}
    if border:
        assert border in html
    else:
        assert "border-red-500" not in html
        assert "border-dashed" not in html


def test_an_account_already_past_its_end_is_red_too(client, plex_abs, fake_plex):
    client.post("/j/STEPS123/steps/plex", data={"token": "t"})
    _expires(-3)

    states, _html = _cards(client)

    assert set(states) == {"soon"}


# ── the welcome ─────────────────────────────────────────────────────────────


def test_welcome_names_the_household_and_the_pieces(client, plex_abs):
    html = client.get("/j/STEPS123/welcome").get_data(as_text=True)
    assert "invited to join the" in html
    assert "AllThePopcorn" in html and "AudioBookShelf" in html
    assert "Get started" in html
    assert "invite-welcome-inviter" not in html


def test_welcome_uses_the_household_display_name(client, plex_abs):
    db.session.add(Settings(key="server_name", value="Martin House"))
    db.session.commit()
    html = client.get("/j/STEPS123/welcome").get_data(as_text=True)
    assert "join the Martin House server" in html
    steps = client.get("/j/STEPS123/steps").get_data(as_text=True)
    assert "Set up your access to Martin House" in steps


def test_welcome_says_who_it_is_from(client, plex_abs):
    invitation, _plex, _abs = plex_abs
    invitation.paid_by = "Alex"
    db.session.commit()
    html = client.get("/j/STEPS123/welcome").get_data(as_text=True)
    assert "Part of Alex" in html


def test_welcome_names_the_admin_who_made_it_when_they_have_a_nickname(
    client, plex_abs
):
    invitation, _plex, _abs = plex_abs
    admin = AdminAccount.query.first()
    admin.display_name = "Rob"
    invitation.created_by = admin
    db.session.commit()
    html = client.get("/j/STEPS123/welcome").get_data(as_text=True)
    assert "Invited by Rob" in html


def test_get_started_goes_to_the_checklist_and_the_link_then_skips_the_welcome(
    client, plex_abs
):
    resp = client.post("/j/STEPS123/welcome")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/j/STEPS123/steps")
    again = client.get("/j/STEPS123")
    assert again.headers["Location"].endswith("/j/STEPS123/steps")


def test_a_return_visit_with_progress_skips_the_welcome(client, plex_abs, fake_plex):
    client.post("/j/STEPS123/steps/plex", data={"token": "t"})
    fresh = client.application.test_client()
    resp = fresh.get("/j/STEPS123")
    assert resp.headers["Location"].endswith("/j/STEPS123/steps")


def test_a_shared_invite_shows_the_welcome_before_the_emailed_code(
    client, setup_done, mail_ready
):
    _invite(_server("P", "plex"), _server("A", "audiobookshelf"), unlimited=True)
    resp = client.get("/j/STEPS123")
    assert resp.headers["Location"].endswith("/j/STEPS123/welcome")
    started = client.post("/j/STEPS123/welcome")
    assert started.headers["Location"].endswith("/j/STEPS123/steps")
    gate = client.get("/j/STEPS123/steps")
    assert "/email" in gate.headers["Location"]


@pytest.mark.parametrize("path", ["/j/NOPE0000/welcome", "/j/EXPIRED1/welcome"])
def test_welcome_refuses_unknown_and_expired_invites(client, setup_done, path):
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)
    _invite(
        _server("P", "plex"),
        _server("A", "audiobookshelf"),
        code="EXPIRED1",
        expires=past,
    )
    assert "Invalid invite" in client.get(path).get_data(as_text=True)
