"""Rate limits key on the visitor's address. Behind a proxy that address comes
from REAL_IP_HEADER, but only on requests from TRUSTED_PROXIES; a forged header
from anywhere else is ignored. The limiter stays off unless RATELIMIT_ENABLED."""

import pytest

from app.extensions import client_ip


@pytest.fixture
def behind_tunnel(monkeypatch):
    monkeypatch.setenv("REAL_IP_HEADER", "CF-Connecting-IP")
    monkeypatch.setenv("TRUSTED_PROXIES", "10.10.10.30, 192.168.0.0/24")


def _ip(app, remote, headers=None):
    with app.test_request_context(
        "/", environ_base={"REMOTE_ADDR": remote}, headers=headers or {}
    ):
        return client_ip()


def test_trusted_proxy_passes_the_visitor_on(app, behind_tunnel):
    assert _ip(app, "10.10.10.30", {"CF-Connecting-IP": "203.0.113.7"}) == (
        "203.0.113.7"
    )


def test_trusted_network_range(app, behind_tunnel):
    assert _ip(app, "192.168.0.9", {"CF-Connecting-IP": "203.0.113.8"}) == (
        "203.0.113.8"
    )


def test_forged_header_from_elsewhere_is_ignored(app, behind_tunnel):
    assert _ip(app, "198.51.100.4", {"CF-Connecting-IP": "203.0.113.7"}) == (
        "198.51.100.4"
    )


def test_first_address_of_a_list_is_used(app, behind_tunnel):
    headers = {"CF-Connecting-IP": "203.0.113.7, 10.0.0.1"}
    assert _ip(app, "10.10.10.30", headers) == "203.0.113.7"


def test_missing_header_falls_back_to_the_connection(app, behind_tunnel):
    assert _ip(app, "10.10.10.30") == "10.10.10.30"


def test_without_settings_the_connection_is_used(app, monkeypatch):
    monkeypatch.delenv("REAL_IP_HEADER", raising=False)
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
    assert _ip(app, "10.10.10.30", {"CF-Connecting-IP": "203.0.113.7"}) == (
        "10.10.10.30"
    )


@pytest.mark.parametrize(
    ("value", "enabled"),
    [("", False), ("false", False), ("true", True), ("1", True), ("Yes", True)],
)
def test_limiter_switch(monkeypatch, value, enabled):
    from app.config import _env_flag

    monkeypatch.setenv("RATELIMIT_ENABLED", value)
    assert _env_flag("RATELIMIT_ENABLED") is enabled
