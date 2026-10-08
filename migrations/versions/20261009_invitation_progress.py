"""Remember where each person is in an invite's setup steps.

Revision ID: 20261009_invite_progress
Revises: 20261009_access_admins
Create Date: 2026-10-09 13:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "20261009_invite_progress"
down_revision = "20261009_access_admins"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "invitation_progress",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invitation_id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("person", sa.String(), nullable=False, server_default=""),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["invitation_id"], ["invitation.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["server_id"], ["media_server.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "invitation_id", "server_id", "person", name="uq_invitation_progress"
        ),
    )


def downgrade():
    op.drop_table("invitation_progress")
