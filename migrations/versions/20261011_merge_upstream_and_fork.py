"""Join upstream's migrations with the fork's.

Upstream (watchlist sync, Plex user provisioning, performance indexes,
Pushover) and the fork (invite names through shared invites) both branch from
20260901_expired_unique. This revision has both as parents, so a database on
either branch upgrades to one head; nothing changes here.

Revision ID: 20261011_merge_upstream
Revises: 20261009_invite_people, 20260903_pushover
Create Date: 2026-10-11 13:00:00.000000
"""

revision = "20261011_merge_upstream"
down_revision = ("20261009_invite_people", "20260903_pushover")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
