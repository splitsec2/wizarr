"""Which server and libraries the wizard may show to the current visitor.

Wizard pages and widgets run for invitees, before and after they join. Any
server or media they show has to come from the visitor's own invite: the
invite's server of the requested type, limited to the libraries that invite
grants. Without an invite there is nothing to show, except to a signed-in
admin previewing the wizard. Falling back to "the first server" is how the
family server's media leaked onto the public wizard, so nothing here does.
"""

from collections.abc import Iterable
from typing import cast

from flask import session
from flask_login import current_user

from app.models import Invitation, Library, MediaServer
from app.services.invite_code_manager import InviteCodeManager


def current_invitation() -> Invitation | None:
    """The invite this browser is using: joined (wizard_access) or in progress."""
    code = session.get("wizard_access") or InviteCodeManager.get_invite_code()
    if not code:
        return None
    return Invitation.query.filter_by(code=code).first()


def invited_server(server_type: str) -> MediaServer | None:
    """The visitor's server of ``server_type``.

    An invitee gets their invite's server of that type, or nothing. A
    signed-in admin previewing the wizard gets the first server of the type.
    """
    invitation = current_invitation()
    if invitation is not None:
        servers = list(cast("Iterable[MediaServer]", invitation.servers)) or (
            [invitation.server] if invitation.server else []
        )
        return next((s for s in servers if s.server_type == server_type), None)
    if current_user.is_authenticated:
        return MediaServer.query.filter_by(server_type=server_type).first()
    return None


def invited_library_names(server: MediaServer) -> list[str]:
    """Libraries the visitor gets on ``server``: the invite's picks, or the
    server's enabled libraries when the invite picked none (what a join grants).
    """
    invitation = current_invitation()
    if invitation is not None:
        picked = [
            lib.name
            for lib in cast("Iterable[Library]", invitation.libraries)
            if lib.server_id == server.id
        ]
        if picked:
            return picked
    return [
        lib.name for lib in Library.query.filter_by(server_id=server.id, enabled=True)
    ]
