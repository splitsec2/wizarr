"""
"Access ends on" sets a date instead of a number of days.

Access lasts through the chosen day in Wizarr's timezone and ends at the next
local midnight. The date applies to every server on the invite unless a server
has its own date, and it is stored as the per-server expiry the join already
uses.
"""

import datetime
from zoneinfo import ZoneInfo

import pytest

from app import jinja_filters
from app.models import Invitation, MediaServer, invitation_servers
from app.services import expiry
from app.services.expiry import access_end, calculate_user_expiry
from app.services.invites import create_invite

EDMONTON = ZoneInfo("America/Edmonton")
NEXT_YEAR = datetime.datetime.now(EDMONTON).year + 1


@pytest.fixture(autouse=True)
def edmonton(monkeypatch):
    monkeypatch.setattr(expiry, "local_timezone", lambda: EDMONTON)
    monkeypatch.setattr(jinja_filters, "_LOCAL_TIMEZONE", EDMONTON)


def _servers(session):
    plex = MediaServer(name="Movies", server_type="plex", url="http://p", api_key="k")
    books = MediaServer(
        name="Books", server_type="jellyfin", url="http://j", api_key="k"
    )
    session.add_all([plex, books])
    session.commit()
    return plex, books


def _stored(invite):
    rows = invitation_servers.select().where(
        invitation_servers.c.invite_id == invite.id
    )
    from app.extensions import db

    return {r.server_id: r.expires for r in db.session.execute(rows)}


def _utc(value):
    return value.replace(tzinfo=datetime.UTC) if value.tzinfo is None else value


def test_access_end_is_the_next_local_midnight():
    # 2026 dates, before Alberta's switch to year-round UTC-6 in the tz data.
    winter = access_end(datetime.date(2026, 2, 15))
    summer = access_end(datetime.date(2026, 7, 1))
    assert winter == datetime.datetime(2026, 2, 16, 7, tzinfo=datetime.UTC)
    assert summer == datetime.datetime(2026, 7, 2, 6, tzinfo=datetime.UTC)


def test_one_date_applies_to_every_server(session):
    plex, books = _servers(session)
    invite = create_invite(
        {
            "server_ids": [str(plex.id), str(books.id)],
            "access_ends_on": f"{NEXT_YEAR}-02-15",
        }
    )
    end = access_end(datetime.date(NEXT_YEAR, 2, 15))
    stored = _stored(invite)
    assert _utc(stored[plex.id]) == end
    assert _utc(stored[books.id]) == end
    assert _utc(calculate_user_expiry(invite, books.id)) == end
    assert invite.duration is None


def test_a_server_date_overrides_the_invite_date(session):
    plex, books = _servers(session)
    invite = create_invite(
        {
            "server_ids": [str(plex.id), str(books.id)],
            "access_ends_on": f"{NEXT_YEAR}-02-15",
            f"access_ends_on_{books.id}": f"{NEXT_YEAR}-03-31",
        }
    )
    stored = _stored(invite)
    assert _utc(stored[plex.id]) == access_end(datetime.date(NEXT_YEAR, 2, 15))
    assert _utc(stored[books.id]) == access_end(datetime.date(NEXT_YEAR, 3, 31))


def test_server_date_alone_leaves_other_servers_on_duration(session):
    plex, books = _servers(session)
    invite = create_invite(
        {
            "server_ids": [str(plex.id), str(books.id)],
            "duration": "30",
            f"access_ends_on_{books.id}": f"{NEXT_YEAR}-03-31",
        }
    )
    stored = _stored(invite)
    assert stored[plex.id] is None
    assert _utc(stored[books.id]) == access_end(datetime.date(NEXT_YEAR, 3, 31))
    assert invite.duration == "30"


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"access_ends_on": f"{NEXT_YEAR}-02-15", "duration": "30"}, "not both"),
        ({"access_ends_on": "2000-01-01"}, "in the past"),
        ({"access_ends_on": "15/02/2027"}, "not a valid date"),
    ],
)
def test_bad_dates_are_rejected(session, extra, message):
    plex, _books = _servers(session)
    with pytest.raises(ValueError, match=message):
        create_invite({"server_ids": [str(plex.id)], **extra})
    assert Invitation.query.count() == 0


def test_invite_card_shows_the_last_day(client, session):
    from tests.test_invite_library_selection import _login

    _login(client, session)
    plex, books = _servers(session)
    create_invite(
        {
            "server_ids": [str(plex.id), str(books.id)],
            "access_ends_on": f"{NEXT_YEAR}-02-15",
        }
    )
    body = client.post("/invite/table").get_data(as_text=True)
    assert f"Access ends: Feb 15, {NEXT_YEAR}" in body
