"""initial multi-tenant schema

Revision ID: 0001
Revises:
Create Date: 2026-09-19
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String, nullable=False, unique=True),
        sa.Column("google_sub", sa.String, nullable=True, unique=True),
        sa.Column("display_name", sa.String, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "oauth_tokens",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("provider", sa.String, nullable=False, server_default="google"),
        sa.Column("encrypted_refresh_token", sa.LargeBinary, nullable=False),
        sa.Column("scopes", sa.String, nullable=True),
        sa.Column("needs_reauth", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "preferences",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("dietary_restrictions", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("skip_meal_slots", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("known_dishes", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("comment", sa.String, nullable=True),
        sa.Column("tag_weights_note", sa.String, nullable=True),
        sa.Column("onboarded", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "menu_intake",
        sa.Column("event_date", sa.Date, primary_key=True),
        sa.Column("source_image_id", sa.String, nullable=True),
        sa.Column("extracted_items", postgresql.JSONB, nullable=False),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "pending_menu_uploads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("week_start", sa.Date, nullable=False),
        sa.Column("extracted_items", postgresql.JSONB, nullable=False),
        sa.Column("sha256", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "scheduled_meals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("event_date", sa.Date, nullable=False),
        sa.Column("meal_slot", sa.String, nullable=False),
        sa.Column("calendar_event_id", sa.String, nullable=True),
        sa.Column("selected_items", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("top_picks", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("scheduled_time", sa.Time, nullable=True),
        sa.Column("conflict_resolved", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("feedback_applied", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "event_date", "meal_slot", name="uq_scheduled_meals_user_date_slot"),
    )


def downgrade() -> None:
    op.drop_table("scheduled_meals")
    op.drop_table("pending_menu_uploads")
    op.drop_table("menu_intake")
    op.drop_table("preferences")
    op.drop_table("oauth_tokens")
    op.drop_table("users")
