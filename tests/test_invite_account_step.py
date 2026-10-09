"""The account step: every server that takes a form shares one form and one
password, built from what the servers' clients declare.

Books access (a provisioning hook) declares email + password and the strong
password check; Audiobookshelf declares username, email and password. Together
they are one "Create your account" step with the strong check, both accounts
made in the same submit, then each server's own notes on connecting.
"""

import json
import logging

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
from app.services import invite_steps, password_policy
from app.services.media.provisioning_hook import ProvisioningHookClient

GOOD = "Robisagreatguyontuesdays"


@pytest.fixture
def setup_done(session):
    admin = AdminAccount(username="admin")
    admin.set_password("password")
    db.session.add_all([admin, Settings(key="admin_username", value="admin")])
    db.session.commit()


def _server(name, server_type, external_url=None):
    server = MediaServer(
        name=name,
        server_type=server_type,
        url="http://x",
        api_key="k",
        external_url=external_url,
    )
    db.session.add(server)
    db.session.flush()
    return server


def _invite(*servers, code="BOOKS123"):
    invitation = Invitation(code=code, used=False, invitee_name="Rob Patterson")
    for server in servers:
        invitation.servers.append(server)
    db.session.add(invitation)
    db.session.commit()
    return invitation


@pytest.fixture
def abs_books(setup_done):
    abs_server = _server("AudioBookShelf", "audiobookshelf", "https://abs.example")
    books = _server("Books", "provisioning_hook")
    books.invitee_notes = "KOReader catalog: https://books.example/opds"
    db.session.commit()
    return _invite(abs_server, books), abs_server, books


class _Client:
    def __init__(self, server, fail=False):
        self.server = server
        self.fail = fail
        self.joins = []

    def join(self, username, password, confirm, email, code):
        self.joins.append((username, password, email))
        if self.fail:
            return False, "refused"
        db.session.add(
            User(
                token=f"tok-{self.server.id}",
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
    failing = set()

    def client_for(server):
        if server.id not in clients:
            clients[server.id] = _Client(server, fail=server.id in failing)
        clients[server.id].fail = server.id in failing
        return clients[server.id]

    monkeypatch.setattr(
        "app.services.invitation_flow.workflows.get_client_for_media_server",
        client_for,
    )
    return clients, failing


def _verified(client, code, email):
    """As if this browser proved *email* with an emailed code for invite *code*."""
    with client.session_transaction() as sess:
        sess["invite_steps_verified"] = {code.lower(): email}


def _submit(
    client, password=GOOD, confirm=None, email="rob@example.com", username="robp"
):
    _verified(client, "BOOKS123", email)
    return client.post(
        "/j/BOOKS123/steps/account",
        data={
            "username": username,
            "email": email,
            "password": password,
            "confirm_password": password if confirm is None else confirm,
        },
    )


# ── the policy ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("password", "ok"),
    [
        ("robpatterson", False),
        ("R0bPatterson", False),
        (GOOD, True),
        ("short", False),
        ("x" * 65, False),
    ],
)
def test_policy_examples(password, ok, monkeypatch):
    monkeypatch.setenv("PASSWORD_EXTRA_WORDS", "fromthecouch, martin")
    words = password_policy.personal_words("rob.patterson@example.com", "Rob")
    assert (password_policy.problem_with(password, words) is None) is ok


def test_personal_words_include_email_name_and_operator_words(monkeypatch):
    monkeypatch.setenv("PASSWORD_EXTRA_WORDS", "fromthecouch,martin")
    words = password_policy.personal_words("pat.smith@example.com", "Pat Smith")
    assert {"pat.smith", "pat", "smith", "Pat Smith", "fromthecouch", "martin"} <= set(
        words
    )


# ── steps come from what the clients declare ─────────────────────────────


def test_form_servers_are_one_account_step(abs_books):
    invitation, abs_server, books = abs_books
    steps = invite_steps.steps_for(invitation)
    assert [s.key for s in steps] == ["account"]
    assert set(steps[0].servers) == {abs_server, books}
    assert steps[0].fields == ["username", "email", "password"]
    assert steps[0].strong_password
    assert invite_steps.uses_steps(invitation)


def test_plex_sign_in_is_its_own_step(setup_done):
    invitation = _invite(
        _server("P", "plex"),
        _server("A", "audiobookshelf"),
        _server("B", "provisioning_hook"),
    )
    assert [s.key for s in invite_steps.steps_for(invitation)] == ["plex", "account"]


def test_a_strong_password_server_alone_uses_the_checklist(setup_done):
    invitation = _invite(_server("B", "provisioning_hook"))
    steps = invite_steps.steps_for(invitation)
    assert [s.key for s in steps] == ["account"]
    assert steps[0].fields == ["email", "password"]
    assert invite_steps.uses_steps(invitation)


def test_a_single_basic_server_keeps_its_own_join_page(setup_done):
    invitation = _invite(_server("A", "audiobookshelf"))
    assert not invite_steps.uses_steps(invitation)


def test_shared_invites_with_a_strong_password_server_are_refused(setup_done):
    from app.services.invites import create_invite

    books = _server("Books", "provisioning_hook")
    db.session.commit()
    with pytest.raises(ValueError, match="until Settings > Email is set up"):
        create_invite({"server_ids": [str(books.id)], "unlimited": "1"})


# ── the step ─────────────────────────────────────────────────────────────


def test_form_asks_for_one_new_password(client, abs_books):
    _verified(client, "BOOKS123", "rob@example.com")
    html = client.get("/j/BOOKS123/steps/account").get_data(as_text=True)
    assert "This sets you up on AudioBookShelf and Books." in html
    assert "Use a new password, not one you use anywhere else." in html
    assert 'name="username"' in html
    assert "A short sentence works well." in html
    assert 'href="/j/BOOKS123/steps"' in html


@pytest.mark.parametrize(
    ("password", "confirm", "email", "message"),
    [
        ("robpatterson12", None, "rob@example.com", "too easy to guess"),
        ("short", None, "rob@example.com", "Use 12 to 64 characters."),
        (GOOD, "different-one-here", "rob@example.com", "match"),
    ],
)
def test_bad_input_stays_on_the_form(
    client, abs_books, fake_media, password, confirm, email, message
):
    resp = _submit(client, password, confirm, email)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert message in html.replace("&#39;", "'")
    assert fake_media[0] == {}


def test_one_submit_makes_both_accounts(client, abs_books, fake_media):
    invitation, abs_server, books = abs_books
    clients, _failing = fake_media

    html = _submit(client).get_data(as_text=True)

    assert clients[abs_server.id].joins == [("robp", GOOD, "rob@example.com")]
    assert clients[books.id].joins == [("robp", GOOD, "rob@example.com")]
    states = {
        r.server_id: r.state
        for r in InvitationProgress.query.filter_by(invitation_id=invitation.id)
    }
    assert states == {abs_server.id: "done", books.id: "done"}
    assert "Sign in with the details you just chose." in html
    assert "https://abs.example" in html
    assert "KOReader catalog:" in html
    assert 'href="https://books.example/opds"' in html
    assert GOOD not in html


def test_a_retry_only_makes_what_is_missing(client, abs_books, fake_media):
    invitation, abs_server, books = abs_books
    clients, failing = fake_media
    failing.add(books.id)
    first = _submit(client).get_data(as_text=True)
    assert "Books could not be set up." in first
    assert invite_steps.pending(invitation)

    failing.clear()
    _submit(client)
    assert len(clients[abs_server.id].joins) == 1
    assert len(clients[books.id].joins) == 2
    assert not invite_steps.pending(invitation)


def test_settings_card_is_there_again_from_the_checklist(client, abs_books, fake_media):
    assert client.get("/j/BOOKS123/steps/account/settings").status_code == 302
    _submit(client)
    checklist = client.get("/j/BOOKS123/steps").get_data(as_text=True)
    assert "/j/BOOKS123/steps/account/settings" in checklist
    card = client.get("/j/BOOKS123/steps/account/settings").get_data(as_text=True)
    assert 'href="https://books.example/opds"' in card


# ── the books grant carries the password, never logged ───────────────────


class _Reply:
    ok = True
    status_code = 200
    text = ""

    def json(self):
        return {"ok": True}


def test_grant_carries_the_password_only_when_chosen(monkeypatch, caplog):
    sent = []

    def post(url, data, headers, timeout):
        sent.append(json.loads(data))
        return _Reply()

    monkeypatch.setattr("app.services.media.provisioning_hook.requests.post", post)
    client = ProvisioningHookClient.__new__(ProvisioningHookClient)
    client.url = "http://hook"
    client.token = "secret"
    with caplog.at_level(logging.DEBUG):
        client._call("grant", "rob@example.com", GOOD)
        client._call("grant", "rob@example.com")
        client._call("disable", "rob@example.com")
    assert sent[0] == {"verb": "grant", "email": "rob@example.com", "password": GOOD}
    assert "password" not in sent[1]
    assert "password" not in sent[2]
    assert GOOD not in caplog.text


def test_step_name_lists_its_servers(abs_books):
    invitation, _abs, _books = abs_books
    assert invite_steps.steps_for(invitation)[0].name == "AudioBookShelf and Books"
