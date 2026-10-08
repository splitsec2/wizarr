"""Link invites to the admin account that created them; name admin accounts.

invitation.created_by (text: an email or a username) becomes created_by_id, a
link to admin_account. Rows whose text matches an account's external_id or
username keep their creator; others lose it. admin_account gains display_name.

Revision ID: 20261009_access_admins
Revises: 20261008_invite_name
Create Date: 2026-10-09 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "20261009_access_admins"
down_revision = "20261008_invite_name"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("admin_account", schema=None) as batch_op:
        batch_op.add_column(sa.Column("display_name", sa.String(), nullable=True))

    with op.batch_alter_table("invitation", schema=None) as batch_op:
        batch_op.add_column(sa.Column("created_by_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_invitation_created_by_id_admin_account",
            "admin_account",
            ["created_by_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.execute(
        """
        UPDATE invitation SET created_by_id = (
            SELECT a.id FROM admin_account a
            WHERE lower(a.external_id) = lower(invitation.created_by)
               OR a.username = invitation.created_by
            ORDER BY a.id LIMIT 1
        )
        WHERE created_by IS NOT NULL
        """
    )

    with op.batch_alter_table("invitation", schema=None) as batch_op:
        batch_op.drop_column("created_by")


def downgrade():
    with op.batch_alter_table("invitation", schema=None) as batch_op:
        batch_op.add_column(sa.Column("created_by", sa.String(), nullable=True))

    op.execute(
        """
        UPDATE invitation SET created_by = (
            SELECT coalesce(a.external_id, a.username) FROM admin_account a
            WHERE a.id = invitation.created_by_id
        )
        WHERE created_by_id IS NOT NULL
        """
    )

    with op.batch_alter_table("invitation", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_invitation_created_by_id_admin_account", type_="foreignkey"
        )
        batch_op.drop_column("created_by_id")

    with op.batch_alter_table("admin_account", schema=None) as batch_op:
        batch_op.drop_column("display_name")
