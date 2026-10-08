"""What to tell an invitee once a server is set up.

Revision ID: 20261009_invitee_notes
Revises: 20261009_invite_progress
Create Date: 2026-10-09 15:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "20261009_invitee_notes"
down_revision = "20261009_invite_progress"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("media_server", schema=None) as batch_op:
        batch_op.add_column(sa.Column("invitee_notes", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("media_server", schema=None) as batch_op:
        batch_op.drop_column("invitee_notes")
