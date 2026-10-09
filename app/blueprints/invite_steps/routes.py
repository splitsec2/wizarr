"""The invite checklist: a Plex sign-in step and an account step, resumable
and skippable.

Every route here loads the invite by its code, which is the credential, and
refuses an invite that is expired or doesn't use the checklist. A done step
can't be run again. Nothing here knows a server type: what each step asks for
comes from what the servers' clients declare (invite_steps.Step).
"""

import re

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
from app.forms.validators import (
    USERNAME_MAX_LENGTH,
    USERNAME_MIN_LENGTH,
    USERNAME_PATTERN,
)
from app.services import email_codes, invite_steps, password_policy
from app.services.invitation_flow.workflows import (
    PlexOAuthWorkflow,
    enter_wizard,
    join_servers,
)
from app.services.invite_code_manager import InviteCodeManager
from app.services.media.client_base import EMAIL_RE

invite_steps_bp = Blueprint("invite_steps", __name__)

# Set in the browser that finished a step, so only that browser sees the earlier
# account's username and email prefilled (the link alone shouldn't show them).
_DID_A_STEP = "invite_steps_code"
# {invite code: email} this browser proved with an emailed code, and the address
# a code was last sent to.
_VERIFIED = invite_steps.VERIFIED_SESSION_KEY
_PENDING = "invite_steps_pending_email"


def _full_message() -> str:
    return _("This invite is full. Ask the person who sent it to you.")


def _invalid():
    return render_template("invalid-invite.html", error=_("Invalid invite"))


def _checklist_url(invitation) -> str:
    return url_for("invite_steps.checklist", code=invitation.code)


def _load(code: str):
    invitation = invite_steps.find_invitation(code)
    if (
        invitation is None
        or invite_steps.is_expired(invitation)
        or not invite_steps.uses_steps(invitation)
    ):
        return None
    return invitation


def _verified_email(invitation) -> str | None:
    return (session.get(_VERIFIED) or {}).get(invitation.code.lower())


def _person(invitation) -> str:
    """Whose progress this is: the verified email on a shared invite."""
    return invite_steps.person_in_session(invitation)


def _gate(invitation, next_url: str):
    """Send the browser to confirm an email first, then on to *next_url*."""
    return redirect(
        url_for("invite_steps.email_gate", code=invitation.code, next=next_url)
    )


def _safe_next(invitation, value: str | None) -> str:
    """Only ever continue to a page of this invite's checklist."""
    base = _checklist_url(invitation)
    if value and (value == base or value.startswith(base + "/")):
        return value
    return base


def _open_step(code: str, key: str):
    """The invite and step, or None when the step is unknown or already done."""
    invitation = _load(code)
    if invitation is None:
        return None, None
    step = invite_steps.find_step(invitation, key)
    if step is None:
        return invitation, None
    states = invite_steps.server_states(invitation, _person(invitation))
    if invite_steps.state_of(step, states) == invite_steps.DONE:
        return invitation, None
    return invitation, step


def _mark_done(invitation, step) -> None:
    invite_steps.record(invitation, step, invite_steps.DONE, person=_person(invitation))
    session[_DID_A_STEP] = invitation.code


def _has_notes(step) -> bool:
    """Whether the step's servers have anything to tell the person afterwards."""
    return any(invite_steps.has_note(s) for s in step.servers)


@invite_steps_bp.route("/j/<code>/steps")
@limiter.limit("50 per minute")
def checklist(code):
    invitation = _load(code)
    if invitation is None:
        return _invalid()
    InviteCodeManager.store_invite_code(invitation.code)
    if invite_steps.multi_use(invitation) and not _verified_email(invitation):
        return _gate(invitation, _checklist_url(invitation))
    states = invite_steps.server_states(invitation, _person(invitation))
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
        has_notes=_has_notes,
    )


@invite_steps_bp.route("/j/<code>/steps/<key>", methods=["GET", "POST"])
@limiter.limit("20 per minute", methods=["POST"])
def run_step(code, key):
    invitation, step = _open_step(code, key)
    if invitation is None:
        return _invalid()
    if step is None:
        return redirect(_checklist_url(invitation))
    here = url_for("invite_steps.run_step", code=invitation.code, key=step.key)
    if invite_steps.multi_use(invitation) and not _verified_email(invitation):
        return _gate(invitation, here)
    if step.is_plex:
        return _plex_step(invitation, step)
    # Any email Wizarr acts on is proven first (Plex proves its own sign-in).
    if "email" in step.fields and not _verified_email(invitation):
        return _gate(invitation, here)
    return _account_step(invitation, step)


# ── Plex: its own sign-in ───────────────────────────────────────────────────


def _plex_page(invitation, step, code_error=None):
    data = PlexOAuthWorkflow().show_initial_form(invitation, list(step.servers))
    context = {
        k: v for k, v in (data.template_data or {}).items() if k != "template_name"
    }
    context["form_action"] = url_for(
        "invite_steps.run_step", code=invitation.code, key=step.key
    )
    context["back_url"] = _checklist_url(invitation)
    if code_error:
        context["code_error"] = code_error
    return render_template("user-plex-login.html", **context)


def _plex_step(invitation, step):
    token = (request.form.get("token") or "") if request.method == "POST" else ""
    if not token:
        return _plex_page(invitation, step)
    from app.services.media.plex import handle_oauth_token

    try:
        handle_oauth_token(current_app, token, invitation.code)
    except Exception as exc:
        current_app.logger.error(
            "Plex step failed for invite %s: %s", invitation.code, exc
        )
        return _plex_page(
            invitation,
            step,
            code_error=_(
                "There was an issue setting up your access. Please contact your server admin."
            ),
        )
    _mark_done(invitation, step)
    if _has_notes(step):
        return _notes_page(invitation, step)
    return redirect(_checklist_url(invitation))


# ── Account: one form, one password, for every other server ─────────────────


def _account_problem(step, values: dict, invitation) -> str | None:
    """What's wrong with the account form, in plain words, or None."""
    fields = step.fields
    if "username" in fields:
        username = values["username"]
        if not (
            USERNAME_MIN_LENGTH <= len(username) <= USERNAME_MAX_LENGTH
            and re.fullmatch(USERNAME_PATTERN, username)
        ):
            return _(
                "Choose a username of 3 to 15 letters, numbers, dots, dashes or underscores."
            )
    if "email" in fields and not EMAIL_RE.fullmatch(values["email"]):
        return _("Enter a valid email address.")
    if "password" in fields:
        if values["password"] != values["confirm_password"]:
            return _("The passwords don't match.")
        return password_policy.problem_with(
            values["password"],
            password_policy.personal_words(
                values["email"], values["username"], invitation.invitee_name
            ),
            strong=step.strong_password,
        )
    return None


def _account_step(invitation, step):
    def form(error=None, values=None):
        return render_template(
            "invite-step-account.html",
            invitation=invitation,
            step=step,
            values=values or {},
            error=error,
            back_url=_checklist_url(invitation),
        )

    person = _person(invitation)
    verified = _verified_email(invitation) or ""
    if request.method == "GET":
        earlier = (
            invite_steps.earlier_account(invitation, person)
            if session.get(_DID_A_STEP) == invitation.code
            else None
        )
        prefill = {"username": earlier.username or ""} if earlier else {}
        prefill["email"] = verified
        return form(values=prefill)

    values = {"username": (request.form.get("username") or "").strip()}
    # The address is the one proven with a code, whatever the form says.
    values["email"] = verified
    values["password"] = request.form.get("password") or ""
    values["confirm_password"] = request.form.get("confirm_password") or ""
    problem = _account_problem(step, values, invitation)
    if problem:
        return form(problem, values)

    # A retry after a partial failure only makes what is still missing.
    pending = [
        s for s in step.servers if not invite_steps.joined(invitation, s, person)
    ]
    succeeded, failed = join_servers(
        pending,
        {
            "username": values["username"] or values["email"],
            "email": values["email"],
            "password": values["password"],
            "confirm_password": values["password"],
            "code": invitation.code,
        },
        invitation.code,
    )
    for result in succeeded:
        invite_steps.record_server(
            invitation, result.server, invite_steps.DONE, person=person
        )
    if failed:
        names = " and ".join(result.server.name for result in failed)
        return form(
            _(
                "%(names)s could not be set up. Try again, or contact your server admin.",
                names=names,
            ),
            values,
        )
    _mark_done(invitation, step)
    if _has_notes(step):
        return _notes_page(invitation, step)
    return redirect(_checklist_url(invitation))


def _notes_page(invitation, step):
    return render_template(
        "invite-step-account-done.html",
        invitation=invitation,
        step=step,
        back_url=_checklist_url(invitation),
    )


@invite_steps_bp.route("/j/<code>/steps/<key>/settings")
@limiter.limit("50 per minute")
def step_settings(code, key):
    """What the servers on a done step say about connecting, again."""
    invitation = _load(code)
    if invitation is None:
        return _invalid()
    step = invite_steps.find_step(invitation, key)
    states = invite_steps.server_states(invitation, _person(invitation))
    if (
        step is None
        or invite_steps.state_of(step, states) != invite_steps.DONE
        or not _has_notes(step)
    ):
        return redirect(_checklist_url(invitation))
    return _notes_page(invitation, step)


@invite_steps_bp.route("/j/<code>/steps/<key>/skip", methods=["POST"])
@limiter.limit("20 per minute")
def skip_step(code, key):
    invitation, step = _open_step(code, key)
    if invitation is None:
        return _invalid()
    if step is not None:
        invite_steps.record(
            invitation, step, invite_steps.SKIPPED, person=_person(invitation)
        )
    return redirect(_checklist_url(invitation))


@invite_steps_bp.route("/j/<code>/steps/finish", methods=["POST"])
@limiter.limit("20 per minute")
def finish(code):
    invitation = _load(code)
    if invitation is None:
        return _invalid()
    if invite_steps.pending(invitation, _person(invitation)):
        return redirect(_checklist_url(invitation))
    return redirect(enter_wizard(invitation))


# ── Proving an email with a code ────────────────────────────────────────────


@invite_steps_bp.route("/j/<code>/steps/email", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def email_gate(code):
    invitation = _load(code)
    if invitation is None:
        return _invalid()
    next_url = _safe_next(invitation, request.values.get("next"))
    if request.method == "GET":
        return render_template(
            "invite-step-email.html",
            invitation=invitation,
            next_url=next_url,
            email=session.get(_PENDING, ""),
            error=None,
        )
    email = email_codes.normalise(request.form.get("email") or "")
    if not EMAIL_RE.fullmatch(email):
        return render_template(
            "invite-step-email.html",
            invitation=invitation,
            next_url=next_url,
            email=email,
            error=_("Enter a valid email address."),
        )
    if invite_steps.multi_use(invitation) and invite_steps.is_full(invitation, email):
        return render_template(
            "invite-step-email.html",
            invitation=invitation,
            next_url=next_url,
            email=email,
            error=_full_message(),
        )
    sent, reason = email_codes.send_code(invitation, email)
    if not sent:
        return render_template(
            "invite-step-email.html",
            invitation=invitation,
            next_url=next_url,
            email=email,
            error=reason,
        )
    session[_PENDING] = email
    return redirect(
        url_for("invite_steps.verify_code", code=invitation.code, next=next_url)
    )


@invite_steps_bp.route("/j/<code>/steps/verify", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def verify_code(code):
    invitation = _load(code)
    if invitation is None:
        return _invalid()
    next_url = _safe_next(invitation, request.values.get("next"))
    email = session.get(_PENDING)
    if not email:
        return _gate(invitation, next_url)
    error = None
    if request.method == "POST":
        if email_codes.check_code(invitation, email, request.form.get("code") or ""):
            if invite_steps.multi_use(invitation) and not invite_steps.add_person(
                invitation, email
            ):
                session.pop(_PENDING, None)
                return render_template(
                    "invite-step-email.html",
                    invitation=invitation,
                    next_url=next_url,
                    email=email,
                    error=_full_message(),
                )
            verified = dict(session.get(_VERIFIED) or {})
            verified[invitation.code.lower()] = email
            session[_VERIFIED] = verified
            session.pop(_PENDING, None)
            return redirect(next_url)
        error = _("That code isn't right or has expired.")
    return render_template(
        "invite-step-verify.html",
        invitation=invitation,
        next_url=next_url,
        email=email,
        error=error,
    )


# ── After joining: what each service the person set up says about it ────────


@invite_steps_bp.route("/j/<code>/setup")
@limiter.limit("50 per minute")
def setup_page(code):
    """The notes of every server this person set up, before Wizarr's stock
    setup pages, which only cover the servers without a note."""
    invitation = invite_steps.find_invitation(code)
    if invitation is None or (session.get("wizard_access") or "").lower() != (
        invitation.code.lower()
    ):
        return _invalid()
    servers = invite_steps.set_up_servers(invitation, _person(invitation))
    session["wizard_notes_seen"] = invitation.code
    without_note = [s for s in servers if not invite_steps.has_note(s)]
    return render_template(
        "invite-setup.html",
        invitation=invitation,
        servers=[s for s in servers if invite_steps.has_note(s)],
        more_help=bool(without_note),
        next_url=url_for("wizard.post_wizard")
        if without_note
        else url_for("wizard.complete"),
    )
