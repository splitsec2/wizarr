"""One invite link as a checklist of setup steps, one per service.

A single-use invite for more than one service opens a checklist instead of one
combined sign-up. Plex is one step (one Plex sign-in covers every Plex server on
the invite); every other server is its own step with its own form. Each step can
be done or skipped, and progress is stored against the invitation, so the same
link picks up where the person left off on any device.

Unlimited invites keep the existing flows until people on them can be told apart
(by a verified email), and so do invites that create an LDAP user, which the
per-step forms would create once per step.
"""

import datetime
from dataclasses import dataclass
from typing import cast

from sqlalchemy import func

from app.extensions import db
from app.models import Invitation, InvitationProgress, MediaServer, User

DONE = "done"
SKIPPED = "skipped"
PLEX_STEP = "plex"
SINGLE_PERSON = ""


@dataclass(frozen=True)
class Step:
    key: str
    servers: tuple[MediaServer, ...]

    @property
    def is_plex(self) -> bool:
        return self.key == PLEX_STEP

    @property
    def name(self) -> str:
        return " and ".join(s.name for s in self.servers)


def find_invitation(code: str) -> Invitation | None:
    return Invitation.query.filter(func.lower(Invitation.code) == code.lower()).first()


def steps_for(invitation: Invitation) -> list[Step]:
    servers = cast("list[MediaServer]", invitation.servers or [])
    plex = tuple(s for s in servers if s.server_type == "plex")
    steps = [Step(PLEX_STEP, plex)] if plex else []
    steps += [Step(str(s.id), (s,)) for s in servers if s.server_type != "plex"]
    return steps


def uses_steps(invitation: Invitation) -> bool:
    """Whether this invite opens the checklist rather than a combined sign-up."""
    if invitation.unlimited:
        return False
    from app.services.ldap.invitation_ldap import InvitationLDAPHandler

    if InvitationLDAPHandler(invitation).should_create_ldap_user():
        return False
    return len(steps_for(invitation)) >= 2


def is_expired(invitation: Invitation) -> bool:
    if not invitation.expires:
        return False
    expires = invitation.expires.replace(tzinfo=datetime.UTC)
    return expires <= datetime.datetime.now(datetime.UTC)


def server_states(
    invitation: Invitation, person: str = SINGLE_PERSON
) -> dict[int, str]:
    rows = InvitationProgress.query.filter_by(
        invitation_id=invitation.id, person=person
    ).all()
    return {row.server_id: row.state for row in rows}


def state_of(step: Step, states: dict[int, str]) -> str | None:
    return states.get(step.servers[0].id)


def find_step(invitation: Invitation, key: str) -> Step | None:
    return next((s for s in steps_for(invitation) if s.key == key), None)


def pending(invitation: Invitation, person: str = SINGLE_PERSON) -> list[Step]:
    states = server_states(invitation, person)
    return [s for s in steps_for(invitation) if state_of(s, states) is None]


def record(
    invitation: Invitation,
    step: Step,
    state: str,
    *,
    person: str = SINGLE_PERSON,
) -> None:
    """Mark *step* done or skipped. Done is final; a skipped step can be done later."""
    for server in step.servers:
        row = InvitationProgress.query.filter_by(
            invitation_id=invitation.id, server_id=server.id, person=person
        ).first()
        if row is None:
            row = InvitationProgress(
                invitation_id=invitation.id, server_id=server.id, person=person
            )
            db.session.add(row)
        elif row.state == DONE:
            continue
        row.state = state
        if state == DONE:
            user = (
                User.query.filter_by(code=invitation.code, server_id=server.id)
                .order_by(User.id.desc())
                .first()
            )
            row.user_id = user.id if user else None
    db.session.commit()


def earlier_account(invitation: Invitation) -> User | None:
    """An account this person already made from the invite, to prefill the next form."""
    return User.query.filter_by(code=invitation.code).order_by(User.id.asc()).first()
