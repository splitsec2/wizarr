"""One invite link as a checklist of setup steps.

Each server's client declares how its invitee signs up (ClientCapabilities):
servers that sign in with Plex are one step (one Plex sign-in covers them all),
and every server that takes a form is one "Create your account" step, with one
form for the fields they declare and ONE password for all of them, checked
with the strong policy if any of them asks for it. Each step can be done or
skipped, and progress is stored against the invitation, so the same link picks
up where the person left off on any device.

A single-use invite opens the checklist when it has both kinds of step, or
when a server needs the strong password check (only the account step runs it);
its progress belongs to the invite. A shared (unlimited) invite opens it once
Settings > Email is set up: each person proves their email with a code and
their progress is kept under that address. Any email the account step uses is
proven the same way. Invites that create an LDAP user keep the old flow.
"""

import datetime
from dataclasses import dataclass
from typing import cast

from sqlalchemy import func

from app.extensions import db
from app.models import (
    Invitation,
    InvitationPerson,
    InvitationProgress,
    MediaServer,
    User,
)
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


def multi_use(invitation: Invitation) -> bool:
    """Whether several people share this invite, so each proves their email."""
    return bool(invitation.unlimited)


def uses_steps(invitation: Invitation) -> bool:
    """Whether this invite opens the checklist rather than a combined sign-up."""
    from app.services import mailer
    from app.services.ldap.invitation_ldap import InvitationLDAPHandler

    # People on a shared invite are told apart by an emailed code.
    if multi_use(invitation) and not mailer.is_configured():
        return False
    if InvitationLDAPHandler(invitation).should_create_ldap_user():
        return False
    steps = steps_for(invitation)
    if multi_use(invitation):
        return bool(steps)
    return len(steps) >= 2 or any(step.strong_password for step in steps)


def is_expired(invitation: Invitation) -> bool:
    if not invitation.expires:
        return False
    expires = invitation.expires.replace(tzinfo=datetime.UTC)
    return expires <= datetime.datetime.now(datetime.UTC)


def people(invitation: Invitation) -> dict[str, dict[int, str]]:
    """Everyone who proved their email on a shared invite, in the order they
    joined, with their progress: {email: {server id: state}}."""
    found: dict[str, dict[int, str]] = {
        row.email: {}
        for row in InvitationPerson.query.filter_by(invitation_id=invitation.id)
        .order_by(InvitationPerson.id)
        .all()
    }
    for row in InvitationProgress.query.filter(
        InvitationProgress.invitation_id == invitation.id,
        InvitationProgress.person != SINGLE_PERSON,
    ):
        found.setdefault(row.person, {})[row.server_id] = row.state
    return found


def is_person(invitation: Invitation, email: str) -> bool:
    return (
        InvitationPerson.query.filter_by(
            invitation_id=invitation.id, email=email
        ).first()
        is not None
    )


def is_full(invitation: Invitation, email: str) -> bool:
    """Whether *email* would be one person too many. Someone already on the
    invite can always come back."""
    if not invitation.max_people or is_person(invitation, email):
        return False
    count = InvitationPerson.query.filter_by(invitation_id=invitation.id).count()
    return count >= invitation.max_people


def add_person(invitation: Invitation, email: str) -> bool:
    """Record *email* as one of the invite's people; False when it is full."""
    if is_person(invitation, email):
        return True
    if is_full(invitation, email):
        return False
    db.session.add(InvitationPerson(invitation_id=invitation.id, email=email))
    db.session.commit()
    return True


def server_states(
    invitation: Invitation, person: str = SINGLE_PERSON
) -> dict[int, str]:
    rows = InvitationProgress.query.filter_by(
        invitation_id=invitation.id, person=person
    ).all()
    return {row.server_id: row.state for row in rows}


def state_of(step: Step, states: dict[int, str]) -> str | None:
    """Done when every server on the step is done; skipped when the rest of
    it was skipped; otherwise still to do."""
    found = [states.get(s.id) for s in step.servers]
    if all(state == DONE for state in found):
        return DONE
    if None not in found:
        return SKIPPED
    return None


def joined(
    invitation: Invitation, server: MediaServer, person: str = SINGLE_PERSON
) -> bool:
    """Whether this person already has their account on *server* from the invite."""
    return server_states(invitation, person).get(server.id) == DONE


def find_step(invitation: Invitation, key: str) -> Step | None:
    return next((s for s in steps_for(invitation) if s.key == key), None)


def pending(invitation: Invitation, person: str = SINGLE_PERSON) -> list[Step]:
    states = server_states(invitation, person)
    return [s for s in steps_for(invitation) if state_of(s, states) is None]


def _account_on(invitation: Invitation, server: MediaServer, person: str):
    query = User.query.filter_by(code=invitation.code, server_id=server.id)
    if person:
        query = query.filter(func.lower(User.email) == person)
    return query.order_by(User.id.desc()).first()


def record_server(
    invitation: Invitation,
    server: MediaServer,
    state: str,
    *,
    person: str = SINGLE_PERSON,
) -> None:
    """Mark one server done or skipped for this person. Done is final."""
    row = InvitationProgress.query.filter_by(
        invitation_id=invitation.id, server_id=server.id, person=person
    ).first()
    if row is None:
        row = InvitationProgress(
            invitation_id=invitation.id, server_id=server.id, person=person
        )
        db.session.add(row)
    elif row.state == DONE:
        return
    row.state = state
    if state == DONE:
        user = _account_on(invitation, server, person)
        row.user_id = user.id if user else None
    db.session.commit()


def record(
    invitation: Invitation,
    step: Step,
    state: str,
    *,
    person: str = SINGLE_PERSON,
) -> None:
    """Mark every server on *step*; a skipped step can be done later."""
    for server in step.servers:
        record_server(invitation, server, state, person=person)


def earlier_account(invitation: Invitation, person: str = SINGLE_PERSON) -> User | None:
    """An account this person already made from the invite, to prefill the next form."""
    query = User.query.filter_by(code=invitation.code)
    if person:
        query = query.filter(func.lower(User.email) == person)
    return query.order_by(User.id.asc()).first()
