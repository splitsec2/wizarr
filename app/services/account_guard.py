"""One place that decides whether an account may be deleted, switched off or removed.

Every path that does one of those (the admin pages, the API, the expiry job)
ends in ``app.services.media.service``, which asks ``refusal`` first. A rule is
a function that gets the user and returns a reason to leave the account alone,
or ``None``. The built-in rule is the account's own Protected setting; others
can be added with ``register``.

Switching an account on is never refused.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.models import User

Rule = Callable[[User], str | None]

ACTIONS = ("delete", "disable", "remove")


class AccountProtected(Exception):
    """The account may not be changed this way."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _marked_protected(user: User) -> str | None:
    if getattr(user, "protected", False):
        return "This account is protected."
    return None


_rules: list[Rule] = [_marked_protected]


def register(rule: Rule) -> None:
    """Add a rule. Adding the same function twice does nothing."""
    if rule not in _rules:
        _rules.append(rule)


def refusal(user: User, action: str) -> str | None:
    """Why *action* (delete, disable or remove) must not touch *user*, or None."""
    if action not in ACTIONS:
        return None
    for rule in _rules:
        try:
            reason = rule(user)
        except Exception:
            # A broken rule must not switch protection off for everyone else's
            # rules, and must not open the account up: refuse and say why.
            logging.exception("Account guard rule %s failed", rule)
            return "The protection check failed, so nothing was changed."
        if reason:
            return reason
    return None


def require(user: User, action: str) -> None:
    """Raise ``AccountProtected`` when *action* is refused, after an audit line."""
    reason = refusal(user, action)
    if reason is None:
        return
    from app.services.audit import audit

    server = getattr(user, "server", None)
    audit(
        "refused",
        action=action,
        id=user.id,
        server=getattr(server, "name", None),
        username=user.username,
        reason=reason,
    )
    raise AccountProtected(reason)
