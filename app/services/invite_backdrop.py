"""The icons that move across the background of the invite pages.

The theme follows the kind of media the invite shares: video (movies and TV),
music, or books (ebooks and audiobooks). It comes from the types of the invite's
servers, so one invite looks the same on every page of its flow. A server type
with no entry here (a ROM library, a file drop) adds nothing, and an invite with
no themed server shows the plain background.

A service could later supply its own pictures (book covers, album art) instead of
these icons; that would replace icon_rows, which is all the template reads.
"""

from collections.abc import Iterable
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from app.models import MediaServer

VIDEO = "video"
MUSIC = "music"
BOOKS = "books"

# Shown in this order when an invite mixes kinds.
KINDS = (VIDEO, MUSIC, BOOKS)

KIND_BY_SERVER_TYPE = {
    "plex": VIDEO,
    "jellyfin": VIDEO,
    "emby": VIDEO,
    "navidrome": MUSIC,
    "audiobookshelf": BOOKS,
    "kavita": BOOKS,
    "komga": BOOKS,
    "provisioning_hook": BOOKS,  # Books: ebooks and audiobooks
}

ICONS_BY_KIND = {
    VIDEO: ("film", "tv", "play"),
    MUSIC: ("note", "disc", "headphones"),
    BOOKS: ("book", "headphones", "bookmark"),
}

ROWS = 4
PER_ROW = 10


def kinds_for_server_types(server_types: Iterable[str]) -> list[str]:
    """The kinds an invite shares, in display order, each once."""
    found = {KIND_BY_SERVER_TYPE[t] for t in server_types if t in KIND_BY_SERVER_TYPE}
    return [kind for kind in KINDS if kind in found]


def kinds_for_invitation(invitation=None, code: str | None = None) -> list[str]:
    """The kinds of an invitation, given it or its code."""
    if invitation is None and code:
        from app.services import invite_steps

        invitation = invite_steps.find_invitation(code)
    if invitation is None:
        return []
    servers = cast("list[MediaServer]", invitation.servers or [])
    return kinds_for_server_types(s.server_type for s in servers)


def icon_rows(kinds: list[str]) -> list[list[str]]:
    """Icon names for each moving row, mixing the kinds and shifted row by row.

    Every row has PER_ROW icons; the template draws each row twice so it loops
    without a seam."""
    if not kinds:
        return []
    cycle: list[str] = []
    longest = max(len(ICONS_BY_KIND[k]) for k in kinds)
    for position in range(longest):
        for kind in kinds:
            icons = ICONS_BY_KIND[kind]
            cycle.append(icons[position % len(icons)])
    rows = []
    for row in range(ROWS):
        shift = (row * 2) % len(cycle)
        rotated = cycle[shift:] + cycle[:shift]
        rows.append([rotated[i % len(rotated)] for i in range(PER_ROW)])
    return rows
