"""Add user.protected: an account the delete, switch-off and remove paths refuse.

Revision ID: 20261014_user_protected
Revises: 20261012_merge_heads
Create Date: 2026-10-14 09:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "20261014_user_protected"
down_revision = "20261012_merge_heads"
branch_labels = None
depends_on = None


def upgrade():
    # Plain ALTER TABLE, not batch_alter_table: on SQLite a batch rebuild copies the
    # table through reflection, which cannot see the expression index
    # ix_user_server_lower_username, and the rebuilt table would lose it.
    op.add_column(
        "user",
        sa.Column("protected", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("user", "protected")
