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
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "protected",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade():
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.drop_column("protected")
