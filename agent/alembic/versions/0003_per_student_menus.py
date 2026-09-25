"""per-student menus, background menu extraction, saved intake answers

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-25

menu_intake was shared (one row per date for the whole college). Each
student now uploads their own, so the table is keyed by (user_id,
event_date). Every existing shared row is copied to every existing user
first, so nobody loses the menu that's already scheduled for this week.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "menu_intake",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )
    op.drop_constraint("menu_intake_pkey", "menu_intake", type_="primary")
    op.execute(
        """
        INSERT INTO menu_intake (user_id, event_date, source_image_id, extracted_items, uploaded_by, created_at, updated_at)
        SELECT u.id, m.event_date, m.source_image_id, m.extracted_items, m.uploaded_by, m.created_at, m.updated_at
        FROM menu_intake m CROSS JOIN users u
        WHERE m.user_id IS NULL
        """
    )
    op.execute("DELETE FROM menu_intake WHERE user_id IS NULL")
    op.alter_column("menu_intake", "user_id", nullable=False)
    op.create_primary_key("menu_intake_pkey", "menu_intake", ["user_id", "event_date"])

    op.add_column(
        "pending_menu_uploads",
        sa.Column("status", sa.String, nullable=False, server_default="ready"),
    )
    op.add_column("pending_menu_uploads", sa.Column("error", sa.String, nullable=True))
    op.alter_column("pending_menu_uploads", "extracted_items", server_default=sa.text("'[]'::jsonb"))

    op.add_column(
        "preferences",
        sa.Column("intake_answers", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("preferences", "intake_answers")

    op.alter_column("pending_menu_uploads", "extracted_items", server_default=None)
    op.drop_column("pending_menu_uploads", "error")
    op.drop_column("pending_menu_uploads", "status")

    # Lossy by nature: collapse back to one row per date, keeping whichever
    # student's copy was updated most recently.
    op.drop_constraint("menu_intake_pkey", "menu_intake", type_="primary")
    op.execute(
        """
        DELETE FROM menu_intake m
        USING menu_intake newer
        WHERE m.event_date = newer.event_date
          AND (m.updated_at, m.user_id) < (newer.updated_at, newer.user_id)
        """
    )
    op.drop_column("menu_intake", "user_id")
    op.create_primary_key("menu_intake_pkey", "menu_intake", ["event_date"])
