"""The protected-address list refuses delete, switch off and remove too.

Needs the build that carries app.services.protected (the Wizarr fork build).
"""

import pytest

from app.extensions import db
from app.models import MediaServer, Settings, User
from app.services import account_guard
from app.services.account_guard import AccountProtected
from app.services.media import service

pytest.importorskip("app.services.protected")


def _server():
    row = MediaServer(
        name="ABS", server_type="audiobookshelf", url="http://abs.local", api_key="k"
    )
    db.session.add(row)
    db.session.commit()
    return row


def test_a_listed_address_is_refused_everywhere(session):
    server = _server()
    db.session.add(Settings(key="protected_emails", value="Owner@Example.com"))
    owner = User(
        token="t1",
        username="owner",
        email="owner@example.com",
        code="X",
        server_id=server.id,
    )
    other = User(
        token="t2",
        username="other",
        email="other@example.com",
        code="X",
        server_id=server.id,
    )
    db.session.add_all([owner, other])
    db.session.commit()

    assert account_guard.refusal(owner, "delete")
    assert account_guard.refusal(owner, "disable")
    assert account_guard.refusal(other, "delete") is None
    with pytest.raises(AccountProtected):
        service.delete_user(owner.id)
