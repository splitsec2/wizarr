"""Switch accounts off and on by hand.

Expiry already disables an account instead of deleting it, on a server whose
client declares ``disable``. This is the same switch for the admin: off now, and
on again later. Switching on follows the renewal rule (``reenable_if_renewed``):
only a disabled account is sent the enable, so an active one never gets a second
one, and an account whose expiry has passed stays off, because the next expiry
run would only disable it again.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import User
from app.services.expiry import expiry_has_passed, server_supports_disable
from app.services.media.service import disable_user, enable_user

DONE = "done"
ALREADY = "already"
FAILED = "failed"
EXPIRED = "expired"
UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class SwitchResult:
    user: User
    outcome: str


def switch_account(user: User, *, on: bool) -> SwitchResult:
    """Switch one account on or off and return what happened."""
    if user.server is None or not server_supports_disable(user.server.server_type):
        return SwitchResult(user, UNSUPPORTED)
    if on:
        if not user.is_disabled:
            return SwitchResult(user, ALREADY)
        if expiry_has_passed(user):
            return SwitchResult(user, EXPIRED)
        return SwitchResult(user, DONE if enable_user(user.id) else FAILED)
    if user.is_disabled:
        return SwitchResult(user, ALREADY)
    return SwitchResult(user, DONE if disable_user(user.id) else FAILED)


def switch_accounts(user_ids: list[int], *, on: bool) -> list[SwitchResult]:
    """Switch the given accounts; an unknown id is skipped."""
    from app.extensions import db

    results = []
    for uid in user_ids:
        user = db.session.get(User, uid)
        if user is not None:
            results.append(switch_account(user, on=on))
    return results
