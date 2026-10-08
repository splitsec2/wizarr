"""Prove an email address belongs to the person joining, with a code.

A 6-digit code is emailed to the address; only a keyed hash of it is stored.
It expires after 10 minutes, allows 5 attempts, and is deleted once used. An
address gets at most 3 codes per invite in half an hour, and an invite at most
20 codes an hour (the per-IP limit is the route's rate limit). Every answer is
the same whether or not the address is known to Wizarr.
"""

import datetime
import hashlib
import hmac
import secrets

from flask import current_app
from flask_babel import gettext as _

from app.extensions import db
from app.models import EmailCode, Invitation
from app.services import mailer

CODE_DIGITS = 6
LIFETIME = datetime.timedelta(minutes=10)
MAX_ATTEMPTS = 5
PER_ADDRESS = (3, datetime.timedelta(minutes=30))
PER_INVITE = (20, datetime.timedelta(hours=1))


def normalise(email: str) -> str:
    return (email or "").strip().lower()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _hash(invitation: Invitation, email: str, code: str) -> str:
    key = str(current_app.config["SECRET_KEY"]).encode()
    message = f"{invitation.id}:{email}:{code}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _recent(invitation: Invitation, since: datetime.datetime, email=None) -> int:
    query = EmailCode.query.filter(
        EmailCode.invitation_id == invitation.id, EmailCode.created_at >= since
    )
    if email is not None:
        query = query.filter(EmailCode.email == email)
    return query.count()


def send_code(invitation: Invitation, email: str) -> tuple[bool, str]:
    """Email a fresh code. Returns (sent, reason if not)."""
    email = normalise(email)
    now = _now()
    limit, window = PER_ADDRESS
    if _recent(invitation, now - window, email) >= limit:
        return False, _("Too many codes for this address. Try again in a while.")
    limit, window = PER_INVITE
    if _recent(invitation, now - window) >= limit:
        return False, _("Too many codes for this invite. Try again in a while.")

    code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
    ok, reason = mailer.send(
        email,
        _("Your sign-in code"),
        _(
            "Your code is %(code)s. It expires in 10 minutes.\n\n"
            "If you didn't ask for it, you can ignore this email.",
            code=code,
        ),
    )
    if not ok:
        current_app.logger.warning("Sign-in code not sent: %s", reason)
        return False, _("The code couldn't be sent. Please try again later.")
    db.session.add(
        EmailCode(
            invitation_id=invitation.id,
            email=email,
            code_hash=_hash(invitation, email, code),
            expires_at=now + LIFETIME,
            created_at=now,
        )
    )
    db.session.commit()
    return True, ""


def check_code(invitation: Invitation, email: str, code: str) -> bool:
    """Whether *code* is the live code for this address; used codes are deleted."""
    email = normalise(email)
    code = (code or "").strip()
    row = (
        EmailCode.query.filter_by(invitation_id=invitation.id, email=email)
        .order_by(EmailCode.id.desc())
        .first()
    )
    if row is None or row.expires_at <= _now() or row.attempts >= MAX_ATTEMPTS:
        return False
    if not hmac.compare_digest(row.code_hash, _hash(invitation, email, code)):
        row.attempts += 1
        db.session.commit()
        return False
    EmailCode.query.filter_by(invitation_id=invitation.id, email=email).delete()
    db.session.commit()
    return True
