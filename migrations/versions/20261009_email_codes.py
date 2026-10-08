"""Sign-in codes emailed to people joining with an invite.

Revision ID: 20261009_email_codes
Revises: 20261009_invitee_notes
Create Date: 2026-10-09 18:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "20261009_email_codes"
down_revision = "20261009_invitee_notes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "email_code",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invitation_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("code_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["invitation_id"], ["invitation.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_code_invitation_id", "email_code", ["invitation_id"])


def downgrade():
    op.drop_index("ix_email_code_invitation_id", table_name="email_code")
    op.drop_table("email_code")
