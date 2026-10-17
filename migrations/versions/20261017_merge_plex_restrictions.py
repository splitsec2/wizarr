"""Join upstream's Plex invite restrictions with the fork's user protection.

Upstream added the Plex content restriction columns (20261013_plex_invite_restrictions)
and the fork added user.protected (20261014_user_protected); both branch from
20261011_ldap_user_sync_filter's line. This revision has both as parents, so a
database on either branch upgrades to one head; nothing changes here.

Revision ID: 20261017_merge_plex_restrictions
Revises: 20261014_user_protected, 20261013_plex_invite_restrictions
Create Date: 2026-10-17 14:00:00.000000
"""

revision = "20261017_merge_plex_restrictions"
down_revision = ("20261014_user_protected", "20261013_plex_invite_restrictions")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
