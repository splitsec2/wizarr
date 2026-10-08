"""One invite link as a checklist of setup steps.

Each server's client declares how its invitee signs up (ClientCapabilities):
servers that sign in with Plex are one step (one Plex sign-in covers them all),
and every server that takes a form is one "Create your account" step, with one
form for the fields they declare and ONE password for all of them, checked
with the strong policy if any of them asks for it. Each step can be done or
skipped, and progress is stored against the invitation, so the same link picks
up where the person left off on any device.

A single-use invite opens the checklist when it has both kinds of step, or
when a server needs the strong password check (only the account step runs it).
Unlimited invites keep the existing flows until people on them can be told
apart (by a verified email), and so do invites that create an LDAP user.
"""

import datetime
from dataclasses import dataclass
from typing import cast

from sqlalchemy import func

from app.extensions import db
from app.models import Invitation, InvitationProgress, MediaServer, User
from app.services.media.client_base import capabilities_for, join_fields_for

DONE = "done"
SKIPPED = "skipped"
PLEX_STEP = "plex"
ACCOUNT_STEP = "account"
SINGLE_PERSON = ""


@dataclass(frozen=True)
class Step:
    key: str
    servers: tuple[MediaServer, ...]

    @property
    def is_plex(self) -> bool:
        return self.key == PLEX_STEP

    @property
    def is_account(self) -> bool:
        return self.key == ACCOUNT_STEP

    @property
    def name(self) -> str:
        names = [s.name for s in self.servers]
        if len(names) <= 2:
            return " and ".join(names)
        return ", ".join(names[:-1]) + " and " + names[-1]

    @property
    def fields(self) -> list[str]:
        """What the account form asks for: what these servers declare."""
        return join_fields_for(s.server_type for s in self.servers)

    @property
    def strong_password(self) -> bool:
        return any(
            capabilities_for(s.server_type).strong_password for s in self.servers
        )


def find_invitation(code: str) -> Invitation | None:
    return Invitation.query.filter(func.lower(Invitation.code) == code.lower()).first()


def steps_for(invitation: Invitation) -> list[Step]:
    servers = cast("list[MediaServer]", invitation.servers or [])
    plex = tuple(
        s for s in servers if capabilities_for(s.server_type).sign_in == "plex"
    )
    form = tuple(s for s in servers if s not in plex)
    steps = [Step(PLEX_STEP, plex)] if plex else []
    if form:
        steps.append(Step(ACCOUNT_STEP, form))
    return steps


def joined(invitation: Invitation, server: MediaServer) -> bool:
    """Whether this invite already made the account on *server*."""
    return (
        User.query.filter_by(code=invitation.code, server_id=server.id).first()
        is not None
    )


def uses_steps(invitation: Invitation) -> bool:
    """Whether this invite opens the checklist rather than a combined sign-up."""
    if invitation.unlimited:
        return False
    from app.services.ldap.invitation_ldap import InvitationLDAPHandler

    if InvitationLDAPHandler(invitation).should_create_ldap_user():
        return False
    steps = steps_for(invitation)
    return len(steps) >= 2 or any(step.strong_password for step in steps)


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
