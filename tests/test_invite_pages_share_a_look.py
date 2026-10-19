"""An invitee opens the link, sets up each service and returns to a list. The pages
the fork added (welcome, checklist, account, email, verify, done, complete) wear
the same dark look as the Plex sign-in page, so the person does not move between
two designs."""

import pytest

from tests.test_invite_steps import fake_plex, plex_abs, setup_done  # noqa: F401

BACKGROUND = "from-[#00281a] via-[#004225] to-[#00281a]"
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


# ── one service: no repeated "set up" screens ───────────────────────────────


@pytest.fixture
def plex_only(request):
    request.getfixturevalue("setup_done")
    from tests.test_invite_steps import _invite, _server

    return _invite(_server("AllThePopcorn", "plex"), code="ONEPLEX1")


def test_welcome_for_one_service_goes_straight_to_its_page(client, plex_only):
    resp = client.post("/j/ONEPLEX1/welcome")
    assert resp.status_code == 302
    assert "/steps/" in resp.headers["Location"]
    assert not resp.headers["Location"].endswith("/steps")


@pytest.mark.usefixtures("plex_abs")
def test_welcome_for_two_services_still_opens_the_list(client):
    resp = client.post("/j/STEPS123/welcome")
    assert resp.headers["Location"].endswith("/steps")


def test_the_plex_step_does_not_ask_for_the_code_again(client, plex_only):
    target = client.post("/j/ONEPLEX1/welcome").headers["Location"]
    html = client.get(target).get_data(as_text=True)
    assert "Sign in with Plex" in html
    assert "Enter Code to Continue" not in html
    assert "Join Server" not in html
    assert "You&#39;ve been invited" not in html
    assert "Back to your checklist" not in html
    assert 'type="hidden" name="code"' in html


@pytest.mark.usefixtures("plex_abs")
def test_the_plex_step_of_a_list_keeps_the_way_back(client):
    html = client.get("/j/STEPS123/steps").get_data(as_text=True)
    assert "back-to-checklist" not in html  # the list itself has no back link
    from app.models import Invitation
    from app.services import invite_steps

    invitation = Invitation.query.filter_by(code="STEPS123").first()
    plex_step = next(s for s in invite_steps.steps_for(invitation) if s.is_plex)
    page = client.get(f"/j/STEPS123/steps/{plex_step.key}").get_data(as_text=True)
    assert "Back to your checklist" in page


@pytest.mark.usefixtures("plex_only", "fake_plex")
def test_after_the_only_service_the_person_goes_on_to_the_instructions(client):
    step = client.post("/j/ONEPLEX1/welcome").headers["Location"]
    resp = client.post(step, data={"token": "plex-token"})
    assert resp.status_code in (200, 302)
    if resp.status_code == 200:
        # The address page: its one button goes on, not back to a list.
        html = resp.get_data(as_text=True)
        assert "Back to your checklist" not in html
        assert "/steps/finish" in html
        resp = client.post("/j/ONEPLEX1/steps/finish")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/wizard/")


@pytest.mark.usefixtures("plex_abs", "fake_plex")
def test_after_one_of_several_services_the_person_returns_to_the_list(client):
    from app.models import Invitation
    from app.services import invite_steps

    invitation = Invitation.query.filter_by(code="STEPS123").first()
    plex_step = next(s for s in invite_steps.steps_for(invitation) if s.is_plex)
    resp = client.post(f"/j/STEPS123/steps/{plex_step.key}", data={"token": "t"})
    if resp.status_code == 200:
        assert "Back to your checklist" in resp.get_data(as_text=True)
    else:
        assert resp.headers["Location"].endswith("/steps")
