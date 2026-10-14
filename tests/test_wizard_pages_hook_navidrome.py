"""The fork ships setup pages for the provisioning_hook and navidrome types.

An invitee reads Wizard Steps, not the server note. The app imports a bundled
folder on startup when the database has no steps for that server type, so the
two folders seed Books and Music without touching the live database.
"""

from pathlib import Path

import frontmatter
import pytest

from app.blueprints.wizard.routes import _render
from app.services.wizard_seed import _collect_builtin_files, _parse_markdown

ADDRESS = "https://books.example"


def test_both_types_ship_pages():
    sources = _collect_builtin_files()
    assert len(sources["provisioning_hook"]) == 3
    assert len(sources["navidrome"]) == 2


def _pages():
    sources = _collect_builtin_files()
    for server_type in ("provisioning_hook", "navidrome"):
        for path in sorted(sources[server_type]):
            yield server_type, path


@pytest.mark.parametrize(
    ("server_type", "path"), list(_pages()), ids=lambda v: Path(v).name
)
def test_each_page_has_a_title_and_renders_with_the_server_address(
    app, server_type, path
):
    assert _parse_markdown(path)["title"]
    with app.test_request_context():
        html = _render(
            frontmatter.load(str(path)), {"external_url": ADDRESS}, server_type
        )
    assert "Error Loading Step" not in html
    assert "{{" not in html


def test_the_books_pages_carry_the_catalog_address_and_the_sync_warning(app):
    sources = _collect_builtin_files()
    with app.test_request_context():
        html = "".join(
            _render(
                frontmatter.load(str(p)), {"external_url": ADDRESS}, "provisioning_hook"
            )
            for p in sorted(sources["provisioning_hook"])
        )
    assert f"{ADDRESS}/opds" in html
    assert "Never pick the built-in sync option" in html
    assert "Send me a code" in html
