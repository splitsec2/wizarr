"""Rate limits stay off unless RATELIMIT_ENABLED is set, and count per
visitor once TRUST_PROXY_HEADERS gives the limiter the client's address."""

import pytest
from flask import Flask
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import env_bool


@pytest.mark.parametrize(
    ("value", "enabled"),
    [(None, False), ("false", False), ("true", True), ("1", True), ("on", True)],
)
def test_switch(monkeypatch, value, enabled):
    if value is None:
        monkeypatch.delenv("RATELIMIT_ENABLED", raising=False)
    else:
        monkeypatch.setenv("RATELIMIT_ENABLED", value)
    assert env_bool("RATELIMIT_ENABLED", False) is enabled


def test_behind_a_proxy_the_key_is_the_visitor():
    """ProxyFix (TRUST_PROXY_HEADERS) takes the last X-Forwarded-For entry,
    which Cloudflare appends, so a forged earlier entry is ignored."""
    app = Flask(__name__)
    setattr(app, "wsgi_app", ProxyFix(app.wsgi_app, x_for=1))  # noqa: B010
    seen = []

    @app.route("/")
    def index():
        seen.append(get_remote_address())
        return ""

    app.test_client().get(
        "/",
        environ_base={"REMOTE_ADDR": "10.10.10.30"},
        headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.7"},
    )
    assert seen == ["203.0.113.7"]
