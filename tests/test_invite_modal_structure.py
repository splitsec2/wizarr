"""The create-invite wizard sits inside the dialog's flex column: the title bar,
a scrolling body and a footer the wizard buttons live in. A title left unclosed
nests the form inside the heading, and then nothing inside the dialog scrolls."""

import re

HX = {"HX-Request": "true", "HX-Current-URL": "http://localhost/admin/invites"}


def _modal(admin_client):
    return admin_client.get("/invite", headers=HX).get_data(as_text=True)


def test_the_title_is_closed_before_the_form(admin_client):
    html = _modal(admin_client)

    heading = re.search(r"<h1\b[^>]*>(.*?)</h1>", html, re.S)

    assert heading is not None
    assert "Create Invitation" in heading.group(1)
    assert "<form" not in heading.group(1)
    assert html.index("</h1>") < html.index('<form id="invite-form"')


def test_the_close_button_and_the_wizard_buttons_are_in_the_modal(admin_client):
    html = _modal(admin_client)

    assert 'onclick="closeInviteModal()"' in html
    for button_id in ("wizard-back", "wizard-next", "wizard-create"):
        assert f'id="{button_id}"' in html


def test_the_form_scrolls_between_the_title_and_the_buttons(admin_client):
    html = _modal(admin_client)

    form = html[html.index('<form id="invite-form"') :]
    body_open = form.index('class="min-h-0 flex-1')
    footer = form.index('id="wizard-back"')

    assert "overflow-y-auto" in form[body_open : body_open + 200]
    assert body_open < footer
