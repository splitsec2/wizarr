"""Send email through the SMTP server set in Settings > Email.

Wizarr sends little mail (sign-in codes, a test message), so this is plain
smtplib: STARTTLS, SSL or no encryption, an optional login, and a short
timeout. Failures come back as a plain reason and are logged without any
credential; nothing here raises.
"""

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from app.models import Settings

TIMEOUT_SECONDS = 15
SECURITY_CHOICES = ("starttls", "ssl", "none")

# Settings keys, all under one prefix so the email tab owns them.
KEYS = {
    "host": "email_host",
    "port": "email_port",
    "security": "email_security",
    "username": "email_username",
    "password": "email_password_encrypted",
    "sender": "email_from",
}


@dataclass(frozen=True)
class MailConfig:
    host: str
    port: int
    security: str
    username: str
    password: str
    sender: str


def _setting(key: str) -> str:
    row = Settings.query.filter_by(key=key).first()
    return (row.value or "").strip() if row else ""


def load_config() -> MailConfig | None:
    """The saved mail server, or None when it isn't set up."""
    host = _setting(KEYS["host"])
    sender = _setting(KEYS["sender"])
    if not host or not sender:
        return None
    security = _setting(KEYS["security"]) or "starttls"
    try:
        port = int(_setting(KEYS["port"]) or (465 if security == "ssl" else 587))
    except ValueError:
        return None
    password = ""
    encrypted = _setting(KEYS["password"])
    if encrypted:
        from app.services.ldap.encryption import decrypt_credential

        try:
            password = decrypt_credential(encrypted)
        except Exception:
            logging.error("Email: the saved password can't be decrypted")
            return None
    return MailConfig(
        host=host,
        port=port,
        security=security if security in SECURITY_CHOICES else "starttls",
        username=_setting(KEYS["username"]),
        password=password,
        sender=sender,
    )


def is_configured() -> bool:
    return load_config() is not None


def send(
    to: str, subject: str, body: str, config: MailConfig | None = None
) -> tuple[bool, str]:
    """Send one plain-text message. Returns (sent, reason if not)."""
    config = config or load_config()
    if config is None:
        return False, "Email isn't set up yet (Settings > Email)."

    message = EmailMessage()
    message["From"] = config.sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    context = ssl.create_default_context()
    try:
        if config.security == "ssl":
            server = smtplib.SMTP_SSL(
                config.host, config.port, timeout=TIMEOUT_SECONDS, context=context
            )
        else:
            server = smtplib.SMTP(config.host, config.port, timeout=TIMEOUT_SECONDS)
        with server:
            if config.security == "starttls":
                server.starttls(context=context)
            if config.username:
                server.login(config.username, config.password)
            server.send_message(message)
    except smtplib.SMTPAuthenticationError:
        logging.warning(
            "Email: %s refused the login for %s", config.host, config.username
        )
        return False, "The mail server refused the username or password."
    except (smtplib.SMTPException, OSError) as exc:
        logging.warning(
            "Email: sending via %s failed: %s", config.host, type(exc).__name__
        )
        return False, f"Couldn't send through {config.host}: {type(exc).__name__}."
    return True, ""
