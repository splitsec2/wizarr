"""Settings > Email and the mail sender.

The SMTP password is stored encrypted, never shown back, and kept when the
field is left empty. Sending reports a plain reason on failure and never
raises or logs the password.
"""

import logging
import smtplib
from typing import ClassVar

import pytest

from app.extensions import db
from app.models import AdminAccount, Settings
from app.services import mailer

SECRET = "app-password-1234"


@pytest.fixture
def admin_client(client, session):
    admin = AdminAccount(username="admin")
    admin.set_password("TestPass123")
    db.session.add(admin)
    db.session.commit()
    client.post("/login", data={"username": "admin", "password": "TestPass123"})
    return client


def _save(client, **overrides):
    data = {
        "host": "smtp.example.com",
        "port": "587",
        "security": "starttls",
        "username": "sender@example.com",
        "password": SECRET,
        "sender": "sender@example.com",
    }
    data.update(overrides)
    return client.post("/settings/email", data=data, headers={"HX-Request": "true"})


def _stored(key):
    row = Settings.query.filter_by(key=key).first()
    return row.value if row else None


class _FakeSMTP:
    instances: ClassVar[list["_FakeSMTP"]] = []
    fail_login = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        if _FakeSMTP.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"bad")
        self.calls.append(("login", user, password))

    def send_message(self, message):
        self.calls.append(("send", message["To"], message["Subject"]))


@pytest.fixture
def fake_smtp(monkeypatch):
    _FakeSMTP.instances = []
    _FakeSMTP.fail_login = False
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", _FakeSMTP)
    return _FakeSMTP


def test_not_set_up_until_server_and_sender_are_saved(session):
    assert mailer.load_config() is None
    ok, reason = mailer.send("a@example.com", "s", "b")
    assert not ok
    assert "isn't set up" in reason


def test_saving_stores_the_password_encrypted(admin_client):
    resp = _save(admin_client)
    assert resp.status_code == 200
    stored = _stored(mailer.KEYS["password"])
    assert stored
    assert SECRET not in stored
    config = mailer.load_config()
    assert config is not None
    assert config.password == SECRET
    assert (config.host, config.port, config.security) == (
        "smtp.example.com",
        587,
        "starttls",
    )


def test_page_never_shows_the_password(admin_client):
    _save(admin_client)
    html = admin_client.get("/settings/email", headers={"HX-Request": "true"}).get_data(
        as_text=True
    )
    assert SECRET not in html
    assert "Saved. Leave empty to keep it." in html


def test_an_empty_password_keeps_the_saved_one(admin_client):
    _save(admin_client)
    _save(admin_client, password="", host="smtp2.example.com")
    config = mailer.load_config()
    assert config is not None
    assert config.host == "smtp2.example.com"
    assert config.password == SECRET


def test_send_uses_starttls_and_logs_in(admin_client, fake_smtp):
    _save(admin_client)
    ok, reason = mailer.send("to@example.com", "Hello", "Body")
    assert ok, reason
    smtp = fake_smtp.instances[-1]
    assert (smtp.host, smtp.port) == ("smtp.example.com", 587)
    assert smtp.calls == [
        "starttls",
        ("login", "sender@example.com", SECRET),
        ("send", "to@example.com", "Hello"),
    ]


def test_ssl_skips_starttls(admin_client, fake_smtp):
    _save(admin_client, security="ssl", port="465")
    assert mailer.send("to@example.com", "Hi", "Body")[0]
    assert "starttls" not in fake_smtp.instances[-1].calls


def test_a_refused_login_is_a_plain_reason_never_the_password(
    admin_client, fake_smtp, caplog
):
    _save(admin_client)
    fake_smtp.fail_login = True
    with caplog.at_level(logging.DEBUG):
        ok, reason = mailer.send("to@example.com", "Hi", "Body")
    assert not ok
    assert reason == "The mail server refused the username or password."
    assert SECRET not in caplog.text


def test_test_button_sends_and_reports(admin_client, fake_smtp):
    _save(admin_client)
    html = admin_client.post(
        "/settings/email/test",
        data={"test_to": "rob@example.com"},
        headers={"HX-Request": "true"},
    ).get_data(as_text=True)
    assert "Sent. Check that inbox." in html
    assert fake_smtp.instances[-1].calls[-1] == (
        "send",
        "rob@example.com",
        "Wizarr test email",
    )


def test_test_button_needs_an_address(admin_client, fake_smtp):
    _save(admin_client)
    html = admin_client.post(
        "/settings/email/test", data={}, headers={"HX-Request": "true"}
    ).get_data(as_text=True)
    assert "Enter an address to send the test to." in html
    assert fake_smtp.instances == []
