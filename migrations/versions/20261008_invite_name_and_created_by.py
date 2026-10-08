"""Add who an invite is for and who created it.

Revision ID: 20261008_invite_name
Revises: 20260901_expired_unique
Create Date: 2026-10-08 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "20261008_invite_name"
down_revision = "20260901_expired_unique"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("invitation", schema=None) as batch_op:
        batch_op.add_column(sa.Column("invitee_name", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("created_by", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("invitation", schema=None) as batch_op:
        batch_op.drop_column("created_by")
        batch_op.drop_column("invitee_name")
