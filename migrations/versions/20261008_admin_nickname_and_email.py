"""Add a nickname and an email to admin accounts.

Revision ID: 20261008_admin_nickname
Revises: 20261008_invite_name
Create Date: 2026-10-08 16:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "20261008_admin_nickname"
down_revision = "20261008_invite_name"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("admin_account", schema=None) as batch_op:
        batch_op.add_column(sa.Column("nickname", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("email", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("admin_account", schema=None) as batch_op:
        batch_op.drop_column("email")
        batch_op.drop_column("nickname")
