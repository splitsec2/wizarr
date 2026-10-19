"""An invitee opens the link, sets up each service and returns to a list. The pages
the fork added (welcome, checklist, account, email, verify, done, complete) wear
the same dark look as the Plex sign-in page, so the person does not move between
two designs."""

import pytest

from tests.test_invite_steps import plex_abs, setup_done  # noqa: F401

BACKGROUND = "from-slate-900 via-orange-900 to-slate-900"
CARD = "bg-gray-800/40 backdrop-blur-xl"

SHELL_PAGES = (
    "invite-welcome.html",
    "invite-steps.html",
    "invite-step-account.html",
    "invite-step-account-done.html",
    "invite-step-email.html",
    "invite-step-verify.html",
    "invite-complete.html",
)


@pytest.mark.usefixtures("plex_abs")
@pytest.mark.parametrize("path", ["/j/STEPS123/welcome", "/j/STEPS123/steps"])
def test_the_welcome_and_checklist_look_like_the_plex_page(client, path):
    html = client.get(path).get_data(as_text=True)

    assert BACKGROUND in html
    assert CARD in html
    assert "bg-white" not in html


@pytest.mark.usefixtures("plex_abs")
def test_the_plex_page_has_the_same_background_and_card(client):
    html = client.get("/j/STEPS123/steps/plex").get_data(as_text=True)

    assert BACKGROUND in html
    assert CARD in html


@pytest.mark.parametrize("name", SHELL_PAGES)
def test_every_added_page_uses_the_shared_layout(client, name):
    source = client.application.jinja_loader.get_source(
        client.application.jinja_env, name
    )[0]

    assert '{% extends "invite-shell.html" %}' in source
    assert "bg-white" not in source
    assert "dark:bg-" not in source


@pytest.mark.usefixtures("plex_abs")
def test_the_welcome_leads_with_the_invited_heading(client):
    html = client.get("/j/STEPS123/welcome").get_data(as_text=True)

    assert "You&#39;ve been invited!" in html or "You've been invited!" in html
    assert html.index("To join") < html.index("invite-welcome-pieces")
