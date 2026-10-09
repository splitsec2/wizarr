"""Shared invites: a people limit, who pays, and the people who joined.

Revision ID: 20261009_invite_people
Revises: 20261009_email_codes
Create Date: 2026-10-09 20:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "20261009_invite_people"
down_revision = "20261009_email_codes"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("invitation", schema=None) as batch_op:
        batch_op.add_column(sa.Column("max_people", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("paid_by", sa.String(), nullable=True))
    op.create_table(
        "invitation_person",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invitation_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["invitation_id"], ["invitation.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invitation_id", "email", name="uq_invitation_person"),
    )


def downgrade():
    op.drop_table("invitation_person")
    with op.batch_alter_table("invitation", schema=None) as batch_op:
        batch_op.drop_column("paid_by")
        batch_op.drop_column("max_people")
