"""The invite checklist: one step per service, resumable and skippable.

Every route here loads the invite by its code, which is the credential, and
refuses an invite that is expired or doesn't use the checklist. A done step
can't be run again.
"""

from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_babel import _

from app.extensions import limiter
from app.services import invite_steps
from app.services.invitation_flow.workflows import (
    FormBasedWorkflow,
    PlexOAuthWorkflow,
    _create_join_form_template_data,
    enter_wizard,
)
from app.services.invite_code_manager import InviteCodeManager

invite_steps_bp = Blueprint("invite_steps", __name__)

# Set in the browser that finished a step, so only that browser sees the earlier
# account's username and email prefilled (the link alone shouldn't show them).
_DID_A_STEP = "invite_steps_code"


def _invalid():
    return render_template("invalid-invite.html", error=_("Invalid invite"))


def _load(code: str):
    invitation = invite_steps.find_invitation(code)
    if (
        invitation is None
        or invite_steps.is_expired(invitation)
        or not invite_steps.uses_steps(invitation)
    ):
        return None
    return invitation


def _open_step(code: str, key: str):
    """The invite and step, or None when the step is unknown or already done."""
    invitation = _load(code)
    if invitation is None:
        return None, None
    step = invite_steps.find_step(invitation, key)
    if step is None:
        return invitation, None
    states = invite_steps.server_states(invitation)
    if invite_steps.state_of(step, states) == invite_steps.DONE:
        return invitation, None
    return invitation, step


def _render(template_data: dict, form_action: str):
    """Render a sign-up page from the join flow, posting back to this step."""
    context = {k: v for k, v in template_data.items() if k != "template_name"}
    context["form_action"] = form_action
    return render_template(template_data["template_name"], **context)


def _step_page(invitation, step, *, form=None, error=None, code_error=None):
    action = url_for("invite_steps.run_step", code=invitation.code, key=step.key)
    if step.is_plex:
        data = PlexOAuthWorkflow().show_initial_form(invitation, list(step.servers))
        template_data = dict(data.template_data or {})
        if code_error:
            template_data["code_error"] = code_error
        return _render(template_data, action)
    template_data = _create_join_form_template_data(
        invitation, list(step.servers), form=form, error=error
    )
    if form is None and session.get(_DID_A_STEP) == invitation.code:
        earlier = invite_steps.earlier_account(invitation)
        join_form = template_data["form"]
        if earlier is not None:
            if "username" in join_form and not join_form.username.data:
                join_form.username.data = earlier.username
            if "email" in join_form and not join_form.email.data:
                join_form.email.data = earlier.email
    return _render(template_data, action)


@invite_steps_bp.route("/j/<code>/steps")
@limiter.limit("50 per minute")
def checklist(code):
    invitation = _load(code)
    if invitation is None:
        return _invalid()
    InviteCodeManager.store_invite_code(invitation.code)
    states = invite_steps.server_states(invitation)
    steps = [
        (step, invite_steps.state_of(step, states))
        for step in invite_steps.steps_for(invitation)
    ]
    next_step = next((step for step, state in steps if state is None), None)
    return render_template(
        "invite-steps.html",
        invitation=invitation,
        steps=steps,
        next_step=next_step,
        all_finished=next_step is None,
        any_done=any(state == invite_steps.DONE for _step, state in steps),
    )


@invite_steps_bp.route("/j/<code>/steps/<key>", methods=["GET", "POST"])
@limiter.limit("20 per minute", methods=["POST"])
def run_step(code, key):
    invitation, step = _open_step(code, key)
    if invitation is None:
        return _invalid()
    if step is None:
        return redirect(url_for("invite_steps.checklist", code=invitation.code))
    if request.method == "GET":
        return _step_page(invitation, step)

    if step.is_plex:
        token = request.form.get("token") or ""
        if not token:
            return _step_page(invitation, step)
        from app.services.media.plex import handle_oauth_token

        try:
            handle_oauth_token(current_app, token, invitation.code)
        except Exception as exc:
            current_app.logger.error(
                "Plex step failed for invite %s: %s", invitation.code, exc
            )
            return _step_page(
                invitation,
                step,
                code_error=_(
                    "There was an issue setting up your access. Please contact your server admin."
                ),
            )
        invite_steps.record(invitation, step, invite_steps.DONE)
        session[_DID_A_STEP] = invitation.code
        return redirect(url_for("invite_steps.checklist", code=invitation.code))

    result = FormBasedWorkflow().process_submission(
        invitation, list(step.servers), request.form.to_dict()
    )
    if result.has_successful_servers():
        invite_steps.record(invitation, step, invite_steps.DONE)
        session[_DID_A_STEP] = invitation.code
        return redirect(url_for("invite_steps.checklist", code=invitation.code))
    template_data = dict(result.template_data or {})
    if not template_data.get("template_name"):
        template_data = _create_join_form_template_data(
            invitation,
            list(step.servers),
            error=result.message or _("Something went wrong. Please try again."),
        )
    return _render(
        template_data,
        url_for("invite_steps.run_step", code=invitation.code, key=step.key),
    )


@invite_steps_bp.route("/j/<code>/steps/<key>/skip", methods=["POST"])
@limiter.limit("20 per minute")
def skip_step(code, key):
    invitation, step = _open_step(code, key)
    if invitation is None:
        return _invalid()
    if step is not None:
        invite_steps.record(invitation, step, invite_steps.SKIPPED)
    return redirect(url_for("invite_steps.checklist", code=invitation.code))


@invite_steps_bp.route("/j/<code>/steps/finish", methods=["POST"])
@limiter.limit("20 per minute")
def finish(code):
    invitation = _load(code)
    if invitation is None:
        return _invalid()
    if invite_steps.pending(invitation):
        return redirect(url_for("invite_steps.checklist", code=invitation.code))
    return redirect(enter_wizard(invitation))
