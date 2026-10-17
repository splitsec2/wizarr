"""Helpers for the fork's tests that upstream's test cleanup removed from its own files."""

from app.models import AdminAccount


def login(client, session):
    """Create the test admin and sign in through the login form."""
    admin = AdminAccount(username="testadmin")
    admin.set_password("TestPass123")
    session.add(admin)
    session.commit()
    resp = client.post(
        "/login", data={"username": "testadmin", "password": "TestPass123"}
    )
    assert resp.status_code in {200, 302, 303}
    return admin
