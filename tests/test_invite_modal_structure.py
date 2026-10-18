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


def _scripts(html):
    return re.findall(r"<script>(.*?)</script>", html, re.S)


def test_opening_the_modal_does_not_depend_on_the_rest_of_the_script(admin_client):
    # A script that fails to parse runs nothing, so a showModal() in the same
    # script as everything else leaves the dialog hidden after any error.
    scripts = _scripts(_modal(admin_client))

    assert (
        scripts[0].strip() == "document.getElementById('invite-dialog')?.showModal();"
    )


def test_the_script_can_run_again_each_time_the_modal_opens(admin_client):
    # htmx runs the modal's script on every open, in the one page. A top-level
    # const or let is "already declared" the second time and the script dies.
    top_level = re.compile(r"^ {4}(const|let|class)\s", re.M)

    for script in _scripts(_modal(admin_client)):
        assert top_level.search(script) is None


def test_the_swap_listener_is_added_once(admin_client):
    html = _modal(admin_client)

    assert html.count("wizarrInviteModalSwapListener") == 2
