"""
With CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD set, admin access needs a valid
Cloudflare Access token in Cf-Access-Jwt-Assertion on every request. A session
cookie alone, a token for another app, an expired or forged token, or a request
that never went through Access must not reach the admin pages.

The token's email signs in as the admin account linked to it; an email no
account is linked to is not an admin, and a session only counts while the
token still maps to the same account.
"""

import datetime
import hashlib
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.extensions import db
from app.models import AdminAccount, ApiKey
from app.services import cloudflare_access

TEAM = "example.cloudflareaccess.com"
AUD = "test-audience-tag"
ADMIN_PAGE = "/settings/general"


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


SIGNING_KEY = _key()
OTHER_KEY = _key()


def _token(key=SIGNING_KEY, **overrides):
    now = datetime.datetime.now(datetime.UTC)
    claims = {
        "aud": [AUD],
        "iss": f"https://{TEAM}",
        "email": "admin@example.com",
        "iat": now,
        "exp": now + datetime.timedelta(minutes=10),
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


def _headers(token):
    return {cloudflare_access.JWT_HEADER: token, "HX-Request": "true"}


def _access_admin(email="admin@example.com", username="rob", display_name=None):
    admin = AdminAccount(
        username=username,
        auth_source=cloudflare_access.AUTH_SOURCE,
        external_id=email,
        display_name=display_name,
    )
    db.session.add(admin)
    db.session.commit()
    return admin


@pytest.fixture
def linked_admin(session):
    return _access_admin()


@pytest.fixture
def access_mode(monkeypatch):
    """Turn Access mode on, with the team's key set served from memory."""
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", f"https://{TEAM}/")
    monkeypatch.setenv("CF_ACCESS_AUD", AUD)
    stub = SimpleNamespace(
        get_signing_key_from_jwt=lambda _token: SimpleNamespace(
            key=SIGNING_KEY.public_key()
        )
    )
    monkeypatch.setattr(cloudflare_access, "_jwks_client", lambda _team: stub)


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CF_ACCESS_TEAM_DOMAIN", raising=False)
    monkeypatch.delenv("CF_ACCESS_AUD", raising=False)
    assert cloudflare_access.enabled() is False


def test_one_setting_alone_does_not_enable_it(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", TEAM)
    monkeypatch.delenv("CF_ACCESS_AUD", raising=False)
    assert cloudflare_access.enabled() is False


def test_valid_token_logs_in_and_reaches_admin(
    client, session, access_mode, linked_admin
):
    token = _token()
    login = client.get("/login", headers=_headers(token))
    assert login.status_code == 302

    page = client.get(ADMIN_PAGE, headers=_headers(token))
    assert page.status_code == 200


def test_session_without_token_is_not_admin(client, session, access_mode, linked_admin):
    """A request that skipped Access can't reuse an admin's session cookie."""
    assert client.get("/login", headers=_headers(_token())).status_code == 302

    page = client.get(ADMIN_PAGE, headers={"HX-Request": "true"})
    assert page.status_code in {302, 401, 403}
    assert page.status_code != 200


def test_login_without_token_is_refused(client, session, access_mode):
    assert client.get("/login").status_code == 403
    assert client.get(ADMIN_PAGE).status_code == 302


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(_token(aud=["another-app"]), id="wrong audience"),
        pytest.param(
            _token(iss="https://other.cloudflareaccess.com"), id="wrong issuer"
        ),
        pytest.param(
            _token(
                exp=datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1)
            ),
            id="expired",
        ),
        pytest.param(_token(key=OTHER_KEY), id="forged signature"),
        pytest.param(_token(exp=None), id="no expiry"),
        pytest.param("not-a-jwt", id="garbage"),
    ],
)
def test_bad_tokens_are_refused(client, session, access_mode, token):
    assert client.get("/login", headers=_headers(token)).status_code == 403
    assert client.get(ADMIN_PAGE, headers=_headers(token)).status_code != 200


def test_access_mode_overrides_disable_builtin_auth(
    client, session, access_mode, monkeypatch
):
    monkeypatch.setenv("DISABLE_BUILTIN_AUTH", "true")
    assert client.get("/login").status_code == 403


def test_key_fetch_failure_refuses_admin(client, session, access_mode, monkeypatch):
    def unreachable(_token):
        raise jwt.PyJWKClientConnectionError("certs endpoint unreachable")

    stub = SimpleNamespace(get_signing_key_from_jwt=unreachable)
    monkeypatch.setattr(cloudflare_access, "_jwks_client", lambda _team: stub)
    assert client.get("/login", headers=_headers(_token())).status_code == 403


def test_public_paths_need_no_token(client, session, access_mode):
    assert client.get("/health").status_code == 200


def test_api_keys_work_without_a_token(client, session, access_mode):
    admin = AdminAccount(username="keyowner")
    admin.set_password("TestPass123")
    db.session.add(admin)
    db.session.commit()
    raw_key = "cf-access-test-key"
    db.session.add(
        ApiKey(
            name="Test",
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            created_by_id=admin.id,
            is_active=True,
        )
    )
    db.session.commit()

    assert client.get("/api/status", headers={"X-API-Key": raw_key}).status_code == 200
    assert client.get("/api/status").status_code == 401


def test_without_access_mode_disable_builtin_auth_still_works(
    client, session, monkeypatch
):
    monkeypatch.delenv("CF_ACCESS_TEAM_DOMAIN", raising=False)
    monkeypatch.delenv("CF_ACCESS_AUD", raising=False)
    monkeypatch.setenv("DISABLE_BUILTIN_AUTH", "true")
    assert client.get("/login").status_code == 302
    assert client.get(ADMIN_PAGE, headers={"HX-Request": "true"}).status_code == 200


# ── Access as an auth source: the token's email is an admin account ─────────


def test_login_is_the_linked_account(client, session, access_mode, linked_admin):
    client.get("/login", headers=_headers(_token()))
    with client.session_transaction() as sess:
        assert sess["_user_id"] == str(linked_admin.id)


def test_email_match_ignores_case(client, session, access_mode, linked_admin):
    token = _token(email="Admin@Example.COM")
    assert client.get("/login", headers=_headers(token)).status_code == 302
    assert client.get(ADMIN_PAGE, headers=_headers(token)).status_code == 200


def test_unknown_email_is_not_an_admin(client, session, access_mode, linked_admin):
    token = _token(email="stranger@example.com")
    login = client.get("/login", headers=_headers(token))
    assert login.status_code == 403
    assert "stranger@example.com isn't a Wizarr admin yet" in login.get_data(
        as_text=True
    )
    assert login.mimetype == "text/plain"
    assert client.get(ADMIN_PAGE, headers=_headers(token)).status_code != 200


def test_service_token_without_email_is_refused(client, session, access_mode):
    _access_admin(email="client-id.access")
    token = _token(email=None, common_name="client-id.access")
    assert client.get("/login", headers=_headers(token)).status_code == 403


def test_local_account_with_same_username_is_not_matched(client, session, access_mode):
    admin = AdminAccount(username="admin@example.com")
    admin.set_password("TestPass123")
    db.session.add(admin)
    db.session.commit()
    assert client.get("/login", headers=_headers(_token())).status_code == 403


def test_session_for_one_admin_with_anothers_token(client, session, access_mode):
    _access_admin(email="rob@example.com", username="rob")
    _access_admin(email="alex@example.com", username="alex")
    client.get("/login", headers=_headers(_token(email="rob@example.com")))

    page = client.get(ADMIN_PAGE, headers=_headers(_token(email="alex@example.com")))
    assert page.status_code != 200


def test_legacy_shared_admin_session_counts_for_nothing(
    client, session, access_mode, linked_admin
):
    with client.session_transaction() as sess:
        sess["_user_id"] = "admin"
        sess["_fresh"] = True
    assert client.get(ADMIN_PAGE, headers=_headers(_token())).status_code != 200


def test_unlinking_ends_the_session(client, session, access_mode, linked_admin):
    token = _token()
    client.get("/login", headers=_headers(token))
    linked_admin.auth_source = "local"
    linked_admin.external_id = None
    db.session.commit()
    assert client.get(ADMIN_PAGE, headers=_headers(token)).status_code != 200


# Paths Cloudflare Access bypasses for invitees behave the same with and without
# Access mode: no token, no session, and nothing here asks for one.
BYPASSED = [
    ("GET", "/health"),
    ("GET", "/join"),
    ("GET", "/j/NOSUCHCODE"),
    ("GET", "/wizard/"),
    ("GET", "/setup/"),
    ("GET", "/static/css/main.css"),
    ("GET", "/image-proxy?url=x"),
    ("GET", "/cinema-posters"),
    ("POST", "/invitation/process"),
]


@pytest.mark.parametrize(("method", "path"), BYPASSED)
def test_bypassed_paths_unchanged(client, session, monkeypatch, method, path):
    monkeypatch.delenv("CF_ACCESS_TEAM_DOMAIN", raising=False)
    monkeypatch.delenv("CF_ACCESS_AUD", raising=False)
    plain = client.open(path, method=method)

    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", TEAM)
    monkeypatch.setenv("CF_ACCESS_AUD", AUD)
    with_access = client.open(path, method=method)

    assert with_access.status_code == plain.status_code
    assert with_access.headers.get("Location") == plain.headers.get("Location")


# ── Adding and linking admins ───────────────────────────────────────────────


def _signed_in(client, admin_email="admin@example.com"):
    token = _token(email=admin_email)
    client.get("/login", headers=_headers(token))
    return _headers(token)


def test_access_mode_adds_an_admin_by_email(client, session, access_mode, linked_admin):
    headers = _signed_in(client)
    resp = client.post(
        "/settings/admins/create",
        headers=headers,
        data={
            "access_email": " Alex.Smith+wizarr@Example.com ",
            "display_name": "Alex",
        },
    )
    assert resp.status_code == 302
    alex = AdminAccount.query.filter_by(display_name="Alex").one()
    assert alex.auth_source == cloudflare_access.AUTH_SOURCE
    assert alex.external_id == "alex.smith+wizarr@example.com"
    assert alex.username == "alex.smith-wiza"
    assert alex.password_hash is None


def test_access_mode_refuses_an_email_already_linked(
    client, session, access_mode, linked_admin
):
    headers = _signed_in(client)
    client.post(
        "/settings/admins/create",
        headers=headers,
        data={"access_email": "ADMIN@example.com"},
    )
    assert AdminAccount.query.count() == 1


def test_create_form_in_access_mode_has_no_password(
    client, session, access_mode, linked_admin
):
    page = client.get("/settings/admins/create", headers=_signed_in(client))
    html = page.get_data(as_text=True)
    assert 'name="access_email"' in html
    assert 'name="password"' not in html


def test_edit_links_and_unlinks_a_local_account(client, session):
    from app.blueprints.admin_accounts.routes import _link_access_email

    admin = AdminAccount(username="local")
    db.session.add(admin)
    field = SimpleNamespace(errors=[])
    assert _link_access_email(admin, "Rob@Example.com", field)
    assert admin.access_email == "rob@example.com"
    assert _link_access_email(admin, "", field)
    assert admin.access_email is None
    assert admin.auth_source == "local"


def test_edit_refuses_to_relink_an_ldap_account(client, session):
    from app.blueprints.admin_accounts.routes import _link_access_email

    admin = AdminAccount(username="ldapuser", external_id="uid=x,dc=example")
    field = SimpleNamespace(errors=[])
    assert not _link_access_email(admin, "x@example.com", field)
    assert field.errors
    assert admin.external_id == "uid=x,dc=example"


def test_usernames_from_email_are_valid_and_free(session):
    from app.blueprints.admin_accounts.routes import _username_from_email

    db.session.add(AdminAccount(username="rob"))
    db.session.commit()
    assert _username_from_email("rob@example.com") == "rob-2"
    assert _username_from_email("al@example.com") == "al-admin"
    assert len(_username_from_email("a.very.long.name.indeed@example.com")) == 15


# ── Who created an invite ───────────────────────────────────────────────────


def test_acting_admin_is_the_signed_in_account(app, session, linked_admin):
    from flask_login import login_user

    from app.services.acting_admin import acting_admin

    with app.test_request_context():
        login_user(linked_admin)
        assert acting_admin() == linked_admin


def test_acting_admin_for_an_api_key_is_its_creator(app, session, linked_admin):
    from app.services.acting_admin import acting_admin, remember_api_key

    key = ApiKey(name="k", key_hash="h", created_by_id=linked_admin.id)
    with app.test_request_context():
        remember_api_key(key)
        assert acting_admin() == linked_admin


def test_legacy_shared_admin_is_nobody(app, session):
    from flask_login import login_user

    from app.models import AdminUser
    from app.services.acting_admin import acting_admin

    with app.test_request_context():
        login_user(AdminUser())
        assert acting_admin() is None


def test_api_invite_is_created_by_the_keys_creator(client, session):
    import json

    from app.models import Invitation, Library, MediaServer

    owner = _access_admin(display_name="Rob")
    raw_key = "creator-test-key"
    db.session.add(
        ApiKey(
            name="k",
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            created_by_id=owner.id,
            is_active=True,
        )
    )
    server = MediaServer(name="S", server_type="plex", url="http://x", verified=True)
    db.session.add(server)
    db.session.flush()
    db.session.add(Library(external_id="1", name="Movies", server_id=server.id))
    db.session.commit()

    resp = client.post(
        "/api/invitations",
        headers={"X-API-Key": raw_key, "Content-Type": "application/json"},
        data=json.dumps({"server_ids": [server.id], "expires_in_days": 7}),
    )
    assert resp.status_code == 201
    invite = Invitation.query.filter_by(
        code=resp.get_json()["invitation"]["code"]
    ).one()
    assert invite.created_by == owner
    assert invite.created_by.shown_name == "Rob"


def test_deleting_an_admin_keeps_their_invites(session):
    from app.models import Invitation

    admin = _access_admin(display_name="Rob")
    invite = Invitation(code="KEEPME", created_by=admin)
    db.session.add(invite)
    db.session.commit()
    db.session.delete(admin)
    db.session.commit()
    db.session.expire_all()
    kept = Invitation.query.filter_by(code="KEEPME").one()
    assert kept.created_by is None


def test_shown_name_falls_back_to_username():
    assert AdminAccount(username="rob").shown_name == "rob"
    assert AdminAccount(username="rob", display_name="Rob").shown_name == "Rob"


# ── Settings shows whether Access sign-in is on (read-only) ────────────────


def test_status_card_when_on(client, session, access_mode, linked_admin):
    local = AdminAccount(username="oldlocal")
    db.session.add(local)
    db.session.commit()
    html = client.get("/settings/admins", headers=_signed_in(client)).get_data(
        as_text=True
    )
    assert "Cloudflare Access sign-in" in html
    assert ">On<" in html
    assert f"Team: {TEAM}" in html
    assert "You signed in as admin@example.com" in html
    assert "Admins who can sign in: 1" in html
    assert "who can't sign in: 1" in html


def test_status_card_when_off(client, session, monkeypatch):
    monkeypatch.delenv("CF_ACCESS_TEAM_DOMAIN", raising=False)
    monkeypatch.delenv("CF_ACCESS_AUD", raising=False)
    monkeypatch.setenv("DISABLE_BUILTIN_AUTH", "true")
    client.post("/login")
    html = client.get("/settings/admins", headers={"HX-Request": "true"}).get_data(
        as_text=True
    )
    assert ">Off<" in html
    assert "the admin pages trust anyone who can reach Wizarr" in html


def test_status_card_has_no_control(client, session, access_mode, linked_admin):
    html = client.get("/settings/admins", headers=_signed_in(client)).get_data(
        as_text=True
    )
    card = html.split('id="access-status"', 1)[1].split("</div>\n", 1)[0]
    assert "<form" not in card
    assert "<input" not in card
    assert "hx-post" not in card
