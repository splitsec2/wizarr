"""Accounts the expiry job never touches: the operator's own.

Settings > General holds a list of addresses (one per line or comma separated,
any case, ``#`` starts a comment line). An admin's Cloudflare Access address
counts too. A protected account is skipped by both expiry sweeps, and a
protected address that redeems an invite gets an account with no expiry.
"""

from __future__ import annotations

import logging

from app.extensions import db
from app.models import AdminAccount, Settings, User

KEY = "protected_emails"


def _clean(value: str | None) -> str:
    return (value or "").strip().lower()


def _parse(raw: str | None) -> set[str]:
    found: set[str] = set()
    for line in (raw or "").splitlines():
        if line.strip().startswith("#"):
            continue
        for part in line.split(","):
            if address := _clean(part):
                found.add(address)
    return found


def protected_emails() -> set[str]:
    """Every protected address, lower case."""
    row = Settings.query.filter_by(key=KEY).first()
    addresses = _parse(row.value if row else "")
    for admin in AdminAccount.query.all():
        if address := _clean(admin.access_email):
            addresses.add(address)
    return addresses


def is_protected_email(email: str | None, addresses: set[str] | None = None) -> bool:
    address = _clean(email)
    if not address:
        return False
    return address in (protected_emails() if addresses is None else addresses)


def is_protected_user(user: User, addresses: set[str] | None = None) -> bool:
    """A user whose own address, or whose identity's, is protected."""
    if addresses is None:
        addresses = protected_emails()
    identity = user.identity
    return is_protected_email(user.email, addresses) or (
        identity is not None and is_protected_email(identity.primary_email, addresses)
    )


def clear_expiry_if_protected(email: str, code: str, server_id) -> None:
    """Give an account a protected address just redeemed an invite with no expiry."""
    if not is_protected_email(email):
        return
    rows = User.query.filter(
        db.func.lower(User.email) == _clean(email),
        User.code == code,
        User.server_id == server_id,
        User.expires.is_not(None),
    ).all()
    for user in rows:
        user.expires = None
    if rows:
        db.session.commit()
        logging.info(
            "Protected address %s redeemed %s: its account does not expire",
            _clean(email),
            code,
        )
