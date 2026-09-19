"""The only module that talks to Postgres. react_agent.py's ReActMealAgent,
the web routes, and the weekly jobs all go through a Repository instance —
nothing else builds a SQLAlchemy query or touches an encrypted token
directly. Implements agent.react_agent.AgentRepository structurally (no
import needed on that side — see the Protocol's own docstring for why).
"""
import os
import uuid
from datetime import date
from typing import Optional

from google.oauth2.credentials import Credentials
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from agent.crypto import decrypt_token, encrypt_token
from agent.models_db import MenuIntakeRow, OAuthToken, PendingMenuUpload, Preferences, ScheduledMealRow, User, UserLLMKey
from agent.react_agent import ExtractedDish, GoogleAuthUnavailable, KnownDish, MenuIntake, UserPreferences
from agent.timezone import now_ist

# Narrower than the old full "calendar" scope — this app only ever
# lists/creates/gets/deletes events, never touches calendar settings. Falls
# back to the full scope via env var if that ever turns out to be needed.
CALENDAR_SCOPE = os.getenv("CALENDAR_SCOPE", "https://www.googleapis.com/auth/calendar.events")


class Repository:
    def __init__(self, db: Session):
        self.db = db

    # --- users ---------------------------------------------------------

    def get_user_by_google_sub(self, google_sub: str) -> Optional[User]:
        return self.db.scalar(select(User).where(User.google_sub == google_sub))

    def get_user_by_email(self, email: str) -> Optional[User]:
        return self.db.scalar(select(User).where(User.email == email))

    def upsert_user(self, email: str, google_sub: str, display_name: Optional[str]) -> User:
        """Resolves by google_sub first (stable identity); falls back to a
        verified email match so a user seeded by migrate_json_to_db.py (which
        has no google_sub yet) attaches to their real history on first login
        instead of getting a second, empty row."""
        user = self.get_user_by_google_sub(google_sub) or self.get_user_by_email(email)
        if user is None:
            user = User(email=email, google_sub=google_sub, display_name=display_name)
            self.db.add(user)
        else:
            user.email = email
            user.google_sub = google_sub
            user.display_name = display_name or user.display_name
        user.last_login_at = now_ist()
        self.db.flush()
        if self.db.get(Preferences, user.id) is None:
            self.db.add(Preferences(user_id=user.id))
        self.db.commit()
        return user

    def seed_user_by_email(self, email: str, display_name: Optional[str] = None) -> User:
        """For migrate_json_to_db.py only: pre-creates a user row by email,
        google_sub left NULL, before they've ever logged in. upsert_user (the
        real login path, above) resolves by email and fills google_sub in on
        their first real sign-in — so this seeded history attaches to them
        automatically instead of starting a second, empty row."""
        user = self.get_user_by_email(email)
        if user is None:
            user = User(email=email, google_sub=None, display_name=display_name)
            self.db.add(user)
            self.db.flush()
        if self.db.get(Preferences, user.id) is None:
            self.db.add(Preferences(user_id=user.id))
        self.db.commit()
        return user

    def list_active_users(self) -> list[User]:
        return list(self.db.scalars(select(User).where(User.is_active.is_(True))))

    # --- oauth tokens ----------------------------------------------------

    def save_oauth_token(self, user_id, refresh_token: str, scopes: str) -> None:
        token = self.db.get(OAuthToken, user_id)
        encrypted = encrypt_token(refresh_token)
        if token is None:
            token = OAuthToken(user_id=user_id, encrypted_refresh_token=encrypted, scopes=scopes, needs_reauth=False)
            self.db.add(token)
        else:
            token.encrypted_refresh_token = encrypted
            token.scopes = scopes
            token.needs_reauth = False
        self.db.commit()

    def set_needs_reauth(self, user_id, value: bool = True) -> None:
        token = self.db.get(OAuthToken, user_id)
        if token is not None:
            token.needs_reauth = value
            self.db.commit()

    def has_valid_token(self, user_id) -> bool:
        token = self.db.get(OAuthToken, user_id)
        return token is not None and not token.needs_reauth

    def build_user_credentials(self, user_id) -> Credentials:
        token = self.db.get(OAuthToken, user_id)
        if token is None or token.needs_reauth:
            raise GoogleAuthUnavailable(f"user {user_id} has no valid stored Calendar token")
        return Credentials(
            token=None,
            refresh_token=decrypt_token(token.encrypted_refresh_token),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.environ["GOOGLE_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
            scopes=[CALENDAR_SCOPE],
        )

    # --- preferences -------------------------------------------------------

    def load_preferences(self, user_id) -> UserPreferences:
        row = self.db.get(Preferences, user_id)
        if row is None:
            row = Preferences(user_id=user_id)
            self.db.add(row)
            self.db.commit()
        return UserPreferences(
            user_id=str(user_id),
            dietary_restrictions=row.dietary_restrictions or [],
            skip_meal_slots=row.skip_meal_slots or [],
            known_dishes=[KnownDish(**d) for d in (row.known_dishes or [])],
            comment=row.comment,
            tag_weights_note=row.tag_weights_note,
        )

    def save_preferences(self, user_id, prefs: UserPreferences) -> None:
        """Locks the row for the read-modify-write so two concurrent writers
        for the same user can't clobber each other — low real contention at
        weekly cadence, but cheap to close off correctly."""
        row = self.db.execute(
            select(Preferences).where(Preferences.user_id == user_id).with_for_update()
        ).scalar_one_or_none()
        if row is None:
            row = Preferences(user_id=user_id)
            self.db.add(row)
        row.dietary_restrictions = prefs.dietary_restrictions
        row.skip_meal_slots = prefs.skip_meal_slots
        row.known_dishes = [d.model_dump(mode="json") for d in prefs.known_dishes]
        row.comment = prefs.comment
        row.tag_weights_note = prefs.tag_weights_note
        self.db.commit()

    def is_onboarded(self, user_id) -> bool:
        row = self.db.get(Preferences, user_id)
        return bool(row and row.onboarded)

    def mark_onboarded(self, user_id) -> None:
        row = self.db.get(Preferences, user_id)
        if row is not None:
            row.onboarded = True
            self.db.commit()

    # --- bring-your-own LLM key (feedback sentiment only — see llm_providers.py) ---

    def save_llm_key(self, user_id, provider: str, api_key: str) -> None:
        row = self.db.get(UserLLMKey, user_id)
        encrypted = encrypt_token(api_key)
        if row is None:
            row = UserLLMKey(user_id=user_id, provider=provider, encrypted_api_key=encrypted)
            self.db.add(row)
        else:
            row.provider = provider
            row.encrypted_api_key = encrypted
        self.db.commit()

    def get_llm_key(self, user_id) -> Optional[tuple[str, str]]:
        """Returns (provider, decrypted_api_key), or None if this student
        hasn't added their own — the caller falls back to the shared
        GEMINI_API_KEY in that case."""
        row = self.db.get(UserLLMKey, user_id)
        if row is None:
            return None
        return row.provider, decrypt_token(row.encrypted_api_key)

    def has_llm_key(self, user_id) -> Optional[str]:
        """Returns the provider name if set (for showing "using your own
        <provider> key" in Settings without ever decrypting/displaying the
        key itself), else None."""
        row = self.db.get(UserLLMKey, user_id)
        return row.provider if row is not None else None

    def delete_llm_key(self, user_id) -> None:
        row = self.db.get(UserLLMKey, user_id)
        if row is not None:
            self.db.delete(row)
            self.db.commit()

    # --- menu intake -----------------------------------------------------

    def get_menu_intake(self, event_date: date) -> Optional[MenuIntake]:
        row = self.db.get(MenuIntakeRow, event_date)
        if row is None:
            return None
        return MenuIntake(
            date=row.event_date,
            source_image_id=row.source_image_id or "",
            extracted_items=[ExtractedDish(**item) for item in row.extracted_items],
        )

    def weeks_menu_dates_present(self, dates: list[date]) -> set[date]:
        return set(self.db.scalars(select(MenuIntakeRow.event_date).where(MenuIntakeRow.event_date.in_(dates))))

    def upsert_menu_intake(self, event_date: date, extracted_items: list[dict], source_image_id: str, uploaded_by) -> None:
        stmt = pg_insert(MenuIntakeRow).values(
            event_date=event_date, source_image_id=source_image_id,
            extracted_items=extracted_items, uploaded_by=uploaded_by,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[MenuIntakeRow.event_date],
            set_={"source_image_id": source_image_id, "extracted_items": extracted_items, "uploaded_by": uploaded_by},
        )
        self.db.execute(stmt)
        self.db.commit()

    # --- pending uploads (the upload -> preview -> confirm holding area) -----

    def create_pending_upload(self, user_id, week_start: date, extracted_items: list[dict], sha256: str) -> uuid.UUID:
        row = PendingMenuUpload(user_id=user_id, week_start=week_start, extracted_items=extracted_items, sha256=sha256)
        self.db.add(row)
        self.db.commit()
        return row.id

    def get_pending_upload(self, pending_id) -> Optional[PendingMenuUpload]:
        return self.db.get(PendingMenuUpload, pending_id)

    def delete_pending_upload(self, pending_id) -> None:
        row = self.db.get(PendingMenuUpload, pending_id)
        if row is not None:
            self.db.delete(row)
            self.db.commit()

    # --- scheduled meals (per-user idempotency claim rows) -------------------

    def claim_scheduled_meal(self, user_id, event_date: date, meal_slot: str) -> Optional[uuid.UUID]:
        """The idempotency guard: only the run that successfully inserts this
        claim row goes on to touch the Calendar API for this user/date/slot."""
        stmt = (
            pg_insert(ScheduledMealRow)
            .values(user_id=user_id, event_date=event_date, meal_slot=meal_slot)
            .on_conflict_do_nothing(index_elements=["user_id", "event_date", "meal_slot"])
            .returning(ScheduledMealRow.id)
        )
        row = self.db.execute(stmt).first()
        self.db.commit()
        return row[0] if row else None

    def update_scheduled_meal(self, meal_id, **fields) -> None:
        row = self.db.get(ScheduledMealRow, meal_id)
        if row is None:
            return
        for key, value in fields.items():
            setattr(row, key, value)
        self.db.commit()

    def scheduled_meal_exists(self, user_id, event_date: date, meal_slot: str) -> bool:
        return self.db.scalar(
            select(ScheduledMealRow.id).where(
                ScheduledMealRow.user_id == user_id,
                ScheduledMealRow.event_date == event_date,
                ScheduledMealRow.meal_slot == meal_slot,
            )
        ) is not None

    def list_unresolved_scheduled_meals(self, before_date: date) -> list[ScheduledMealRow]:
        """ALL past, unresolved rows — not just the most recent — so a run
        that got skipped one week still gets its feedback checked later."""
        return list(self.db.scalars(
            select(ScheduledMealRow).where(
                ScheduledMealRow.feedback_applied.is_(False),
                ScheduledMealRow.event_date < before_date,
            )
        ))

    def get_scheduled_meal(self, meal_id) -> Optional[ScheduledMealRow]:
        return self.db.get(ScheduledMealRow, meal_id)

    def list_pending_manual_feedback(self, user_id, before_date: date, limit: int = 10) -> list[ScheduledMealRow]:
        """Past, unresolved meals for one user, for the manual "how was it"
        feedback page — the only path left that can raise a rating from real
        experience (collect_feedback.py's automated check can only ever
        produce a decline, on cancellation)."""
        return list(self.db.scalars(
            select(ScheduledMealRow).where(
                ScheduledMealRow.user_id == user_id,
                ScheduledMealRow.feedback_applied.is_(False),
                ScheduledMealRow.calendar_event_id.is_not(None),
                ScheduledMealRow.event_date <= before_date,
            ).order_by(ScheduledMealRow.event_date.desc()).limit(limit)
        ))

    def mark_feedback_applied(self, meal_id) -> None:
        row = self.db.get(ScheduledMealRow, meal_id)
        if row is not None:
            row.feedback_applied = True
            self.db.commit()
