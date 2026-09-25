"""SQLAlchemy ORM models — the 6 tables that replace preferences_<id>.json,
menu_intake_<date>.json, and the old single-account .env token. JSONB columns
keep known_dishes/extracted_items in the same shape the Pydantic models in
react_agent.py already use, so agent/repository.py is the only place that
translates between the two.
"""
import uuid
from datetime import date as date_, datetime, time as time_
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, LargeBinary, String, Time, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # Nullable: migrate_json_to_db.py seeds the real u001 student's row by
    # email before they've ever logged in, so google_sub isn't known yet —
    # it's filled in on their first real login (see Repository.upsert_user).
    google_sub: Mapped[Optional[str]] = mapped_column(String, unique=True, nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    provider: Mapped[str] = mapped_column(String, default="google", nullable=False)
    encrypted_refresh_token: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    scopes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Set when Google returns invalid_grant (revoked/expired token); the weekly
    # job skips these users and the dashboard shows a "Reconnect Google" banner.
    needs_reauth: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Preferences(Base):
    __tablename__ = "preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    dietary_restrictions: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    skip_meal_slots: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    known_dishes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tag_weights_note: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Drives the first-login redirect into onboarding — false until the user
    # has saved the baseline preferences at least once.
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # The raw baseline-question answers ({question_id: option value(s) or
    # text}), kept only so the form can show a returning student what they
    # picked last time. Scoring never reads this — preference_questions.
    # apply_answers turns answers into known_dishes/restrictions as before.
    intake_answers: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MenuIntakeRow(Base):
    """One row per (student, calendar date) — each student uploads and
    confirms their own weekly menu photo, so nobody else's upload changes
    what gets scheduled for them. No `processed` flag: a date "has a menu"
    for a student iff a row exists for it."""

    __tablename__ = "menu_intake"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    event_date: Mapped[date_] = mapped_column(Date, primary_key=True)
    source_image_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # sha256 hex — no image is ever stored
    extracted_items: Mapped[list] = mapped_column(JSONB, nullable=False)
    uploaded_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PendingMenuUpload(Base):
    """Holds a Gemini extraction between the upload step and the confirm
    step — nothing lands in menu_intake until the uploader confirms.
    Extraction runs in the background after the upload request returns
    (so onboarding can carry on while Gemini reads the photo): `status` is
    "processing" until it finishes, then "ready" or "error"."""

    __tablename__ = "pending_menu_uploads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    week_start: Mapped[date_] = mapped_column(Date, nullable=False)
    extracted_items: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="processing", nullable=False)
    error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserLLMKey(Base):
    """A student's own bring-your-own API key for one AI provider, used only
    for their own /feedback sentiment classification (see routes_feedback.py)
    — menu-photo extraction always stays on the shared GEMINI_API_KEY since
    it's a shared weekly action, not a personal one. Falls back to that
    shared key when a student hasn't added one of their own."""

    __tablename__ = "user_llm_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)  # "gemini" | "anthropic" | "openai" | "groq"
    encrypted_api_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ScheduledMealRow(Base):
    """Per-user claim row — the real fix for the old shared-file race. Only
    the run that successfully INSERTs this row for a given
    (user, date, slot) goes on to touch the Calendar API for it."""

    __tablename__ = "scheduled_meals"
    __table_args__ = (UniqueConstraint("user_id", "event_date", "meal_slot", name="uq_scheduled_meals_user_date_slot"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    event_date: Mapped[date_] = mapped_column(Date, nullable=False)
    meal_slot: Mapped[str] = mapped_column(String, nullable=False)
    calendar_event_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    selected_items: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    top_picks: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    scheduled_time: Mapped[Optional[time_]] = mapped_column(Time, nullable=True)
    conflict_resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Feedback is checked once, a week later, by jobs/collect_feedback.py —
    # there's no ongoing RSVP signal to re-poll, so one check is enough.
    feedback_applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
