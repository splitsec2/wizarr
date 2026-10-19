"""The icons behind the invite pages follow the kind of media the invite shares:
video (movies and TV), music, or books (ebooks and audiobooks)."""

import pytest

from app.services import invite_backdrop as backdrop
from tests.test_invite_steps import _invite, _server, setup_done  # noqa: F401

pytestmark = pytest.mark.usefixtures("setup_done")


def test_each_server_type_maps_to_one_kind():
    assert backdrop.kinds_for_server_types(["plex"]) == ["video"]
    assert backdrop.kinds_for_server_types(["jellyfin", "emby"]) == ["video"]
    assert backdrop.kinds_for_server_types(["navidrome"]) == ["music"]
    for book_type in ("audiobookshelf", "kavita", "komga", "provisioning_hook"):
        assert backdrop.kinds_for_server_types([book_type]) == ["books"]


def test_a_mixed_invite_lists_each_kind_once_in_display_order():
    kinds = backdrop.kinds_for_server_types(
        ["audiobookshelf", "provisioning_hook", "plex", "navidrome", "plex"]
    )

    assert kinds == ["video", "music", "books"]


def test_a_server_type_with_no_theme_adds_nothing():
    assert backdrop.kinds_for_server_types(["romm", "drop"]) == []
    assert backdrop.icon_rows([]) == []


def test_every_row_mixes_the_kinds_and_is_the_same_length():
    rows = backdrop.icon_rows(["video", "books"])

    assert len(rows) == backdrop.ROWS
    assert all(len(row) == backdrop.PER_ROW for row in rows)
    used = {icon for row in rows for icon in row}
    assert {"film", "book"} <= used
    assert "note" not in used


def test_rows_start_at_different_icons():
    rows = backdrop.icon_rows(["video"])

    assert len({row[0] for row in rows}) > 1


def _icons(html):
    return {
        name
        for name in (
            "film",
            "tv",
            "play",
            "note",
            "disc",
            "headphones",
            "book",
            "bookmark",
        )
        if f'href="#bd-{name}"' in html
    }


def _welcome(client, *servers):
    _invite(*servers, code="BACKDROP1")
    return client.get("/j/BACKDROP1/welcome").get_data(as_text=True)


def test_a_plex_invite_shows_video_icons(client):
    html = _welcome(client, _server("TimeVortex", "plex"))

    assert {"film", "tv", "play"} <= _icons(html)
    assert not {"book", "note", "bookmark"} & _icons(html)


def test_a_books_invite_shows_book_and_audiobook_icons(client):
    html = _welcome(
        client,
        _server("Books", "provisioning_hook"),
        _server("AudioBookShelf", "audiobookshelf"),
    )

    assert {"book", "headphones", "bookmark"} <= _icons(html)
    assert not {"film", "tv", "note"} & _icons(html)


def test_a_mixed_invite_shows_every_kind(client):
    html = _welcome(
        client,
        _server("TimeVortex", "plex"),
        _server("AudioBookShelf", "audiobookshelf"),
    )

    assert {"film", "book"} <= _icons(html)


def test_an_invite_with_no_themed_server_shows_the_plain_background(client):
    html = _welcome(client, _server("Games", "romm"))

    assert _icons(html) == set()
    assert "invite-icons-row" not in html


@pytest.mark.parametrize("path", ["/steps", "/steps/plex"])
def test_the_icons_follow_the_invite_onto_every_page(client, path):
    _invite(
        _server("TimeVortex", "plex"),
        _server("AudioBookShelf", "audiobookshelf"),
        code="BACKDROP1",
    )

    html = client.get("/j/BACKDROP1" + path).get_data(as_text=True)

    assert {"film", "book"} <= _icons(html)


def test_the_plex_page_no_longer_asks_the_server_for_posters(client):
    _invite(_server("TimeVortex", "plex"), code="BACKDROP1")

    html = client.get("/j/BACKDROP1/steps/plex").get_data(as_text=True)

    assert "cinema-posters" not in html
    assert "poster-row" not in html
    assert "invite-icons-row" in html


def test_the_icons_hold_still_for_visitors_who_ask_for_less_motion(client):
    html = _welcome(client, _server("TimeVortex", "plex"))

    assert "prefers-reduced-motion: reduce" in html
