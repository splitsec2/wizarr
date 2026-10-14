"""The Create Invitation dialog is a four-step wizard.

Same form, same POST: the fields are grouped into Who, What they get, How long
and Review, and the page shows one step at a time. Nothing is ticked at the
start, the link expires after a week unless changed, and the shared-invite
field is called Supporter.
"""

import re

from app.models import AdminAccount, MediaServer

HX = {"HX-Request": "true"}


def _admin_with_servers(client, session):
    admin = AdminAccount(username="testadmin")
    admin.set_password("TestPass123")
    session.add(admin)
    servers = []
    for name in ("AllThePopcorn", "TimeVortex"):
        s = MediaServer(
            name=name, server_type="plex", url="http://plex.local", api_key="t"
        )
        session.add(s)
        servers.append(s)
    session.commit()
    client.post("/login", data={"username": "testadmin", "password": "TestPass123"})
    return servers


def _dialog(client, path="/invite"):
    resp = client.get(path, headers=HX)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_the_dialog_has_four_steps_in_order(client, session):
    _admin_with_servers(client, session)
    html = _dialog(client)

    steps = re.findall(r'data-wizard-step="(\d)"', html)
    assert steps == ["0", "1", "2", "3"]

    def at(needle):
        return html.index(needle)

    # who, then how long, then what they get, then review
    assert at('id="unlimited"') < at('id="invitee_name"') < at('id="expires"')
    assert at('id="expires"') < at('id="duration"') < at('id="access_ends_on"')
    assert at('id="access_ends_on"') < at('id="server-grid"') < at('id="invite-review"')
    assert at('id="invite-review"') < at('id="code"')
    assert "Advanced Options" not in html


def test_the_access_fields_say_what_they_count_from(client, session):
    _admin_with_servers(client, session)
    html = " ".join(_dialog(client).split())
    assert "Access lasts (days after they join)" in html
    assert "Access ends on (one date for everyone)" in html
    assert "End date for this server only" in html
    assert "Replaces the access end from the previous step" in html
    # the (?) popover that did nothing is gone
    assert "popover" not in html


def test_no_server_is_ticked_when_the_dialog_opens(client, session):
    _admin_with_servers(client, session)
    html = _dialog(client)
    assert 'name="server_ids"' in html
    for tag in re.findall(r"<input[^>]*name=\"server_ids\"[^>]*>", html):
        assert "checked" not in tag


def test_a_server_the_admin_started_from_is_still_ticked(client, session):
    servers = _admin_with_servers(client, session)
    html = _dialog(client, f"/invite?server_id={servers[1].id}")
    ticked = [
        t
        for t in re.findall(r"<input[^>]*name=\"server_ids\"[^>]*>", html)
        if "checked" in t
    ]
    assert len(ticked) == 1
    assert f'value="{servers[1].id}"' in ticked[0]


def test_link_expiry_is_named_and_defaults_to_a_week(client, session):
    _admin_with_servers(client, session)
    html = _dialog(client)
    assert "Invite link expires" in html
    assert re.search(r'<option value="week"\s+selected', html)


def test_the_shared_invite_field_says_supporter(client, session):
    _admin_with_servers(client, session)
    html = _dialog(client)
    assert "Paid by" not in html
    label = html[html.index('for="paid_by"') :][:300]
    assert "Supporter" in label


def test_hidden_steps_and_disabled_fields_are_not_a_dead_end(client, session):
    """The browser blocks a submit on a field it cannot show; the page walks to
    the step with the problem instead, and opens More options when needed."""
    _admin_with_servers(client, session)
    html = _dialog(client)
    assert "function stepValid" in html
    assert "more.open = true" in html
    assert 'id="more-options"' in html


def test_a_shared_invite_is_called_multi_use(client, session):
    _admin_with_servers(client, session)
    html = _dialog(client)
    assert "Multi-use invite" in html
    assert "Unlimited Usages" not in html
    assert "Leave How many people empty for no limit, which is not recommended." in html


def test_the_review_step_warns_when_nothing_ends(client, session):
    _admin_with_servers(client, session)
    html = _dialog(client)
    assert 'id="invite-no-end-warning"' in html
    assert "Nothing on this invite ends" in html
    assert html.index('id="invite-review"') < html.index('id="invite-no-end-warning"')
