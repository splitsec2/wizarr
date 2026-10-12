"""Join the fork's migrations with upstream's v2026.10.0 and v2026.10.1.

Upstream added hide_users_from_login and ldap_user_sync_filter on top of
the migrations the fork already joined in 20261011_merge_upstream. This
revision has the fork's head and upstream's new head as parents, so a
database on either one upgrades to a single head; nothing changes here.

Revision ID: 20261012_merge_heads
Revises: 20261011_merge_upstream, 20261011_ldap_user_sync_filter
Create Date: 2026-10-12 16:00:00.000000
"""

revision = "20261012_merge_heads"
down_revision = ("20261011_merge_upstream", "20261011_ldap_user_sync_filter")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
