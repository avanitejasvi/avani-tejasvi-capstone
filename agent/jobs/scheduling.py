"""Shared week-scheduling logic — used by jobs/weekly_run.py's Monday cron
run (every student) AND by web/routes_menu.py's post-confirm trigger (just
the student who confirmed), so a menu uploaded late in the week still
results in scheduled meals without waiting for the next Monday's cron.

Reads BASE_URL directly from the environment rather than via agent.config,
since this runs as the separate cron service, which shouldn't need the
web-only OAuth vars (GOOGLE_REDIRECT_URI, SESSION_SECRET) that module also
requires at import time.
"""
import logging
import os
from datetime import date, datetime, time as dt_time, timedelta

from googleapiclient.errors import HttpError

from agent.db import get_sessionmaker
from agent.react_agent import (
    FilesystemTool, GeminiSkill, GoogleAuthUnavailable, GoogleCalendarSkill,
    MenuPreferenceMatchingSkill, ReActMealAgent, deterministic_event_id, load_mess_timings,
)
from agent.repository import Repository
from agent.timezone import IST, now_ist

logger = logging.getLogger("agent.jobs.scheduling")

BASE_URL = os.environ["BASE_URL"]


def _reminder_event_id(user_id, week_start: date) -> str:
    return deterministic_event_id(str(user_id), week_start.isoformat(), "menu-reminder")


def _remind_user(repo: Repository, user_id, week_start: date) -> None:
    try:
        credentials = repo.build_user_credentials(user_id)
    except GoogleAuthUnavailable:
        return
    try:
        GoogleCalendarSkill(credentials=credentials).create_reminder_event(
            _reminder_event_id(user_id, week_start),
            week_start,
            "Upload this week's mess menu",
            f"No mess menu has been uploaded for the week of {week_start.isoformat()} yet. "
            f"Upload it at {BASE_URL}/menu/upload so meals can be scheduled.",
        )
    except HttpError:
        logger.exception("failed to create menu reminder for user=%s week=%s", user_id, week_start)


def _clear_reminder(repo: Repository, user_id, week_start: date) -> None:
    try:
        credentials = repo.build_user_credentials(user_id)
    except GoogleAuthUnavailable:
        return
    try:
        GoogleCalendarSkill(credentials=credentials).delete_event(_reminder_event_id(user_id, week_start))
    except HttpError:
        logger.exception("failed to clear menu reminder for user=%s week=%s", user_id, week_start)


def _schedule_one(repo: Repository, user_id, event_date: date, meal_slot: str, extracted_items: list, claim_id) -> None:
    credentials = repo.build_user_credentials(user_id)  # raises GoogleAuthUnavailable -> caller sets needs_reauth
    user = repo.get_user(user_id)
    agent = ReActMealAgent(
        user_id=str(user_id),
        calendar_skill=GoogleCalendarSkill(credentials=credentials),
        gemini_skill=GeminiSkill(),
        matcher=MenuPreferenceMatchingSkill(),
        fs=FilesystemTool(),
        repo=repo,
        live=True,
        # A real attendee entry is what makes the RSVP actionable — see
        # create_event_with_id's docstring.
        attendee_email=user.email if user else None,
    )
    scheduled = agent.run(meal_slot, event_date, cached_items=extracted_items)
    repo.update_scheduled_meal(
        claim_id,
        calendar_event_id=scheduled.calendar_event_id,
        selected_items=scheduled.selected_items,
        top_picks=scheduled.top_picks,
        scheduled_time=scheduled.scheduled_time,
        conflict_resolved=scheduled.conflict_resolved,
    )


# Live progress for the onboarding/upload "Almost there" screen, keyed by
# str(user_id). In-process only: the web service runs schedule_user_week as a
# background task in the same process that serves the progress endpoint.
# After a restart it's simply empty, and the endpoint falls back to what the
# scheduled_meals table shows (see routes_menu.scheduling_status).
PROGRESS: dict[str, dict] = {}


def _schedule_user(repo: Repository, user, week_start: date, dry_run: bool, summary: dict) -> None:
    """One student's week: their own uploaded menu, their own skip slots."""
    dates = [week_start + timedelta(days=i) for i in range(7)]
    present = repo.weeks_menu_dates_present(dates, user.id)
    progress = PROGRESS.setdefault(str(user.id), {})
    progress.update(week_start=week_start.isoformat(), total=0, done=0, finished=False, error=None)

    if not present:
        if not dry_run:
            _remind_user(repo, user.id, week_start)
        summary["reminded"] += 1
        progress.update(finished=True, error="no_menu")
        return

    skip_slots = set(repo.load_preferences(user.id).skip_meal_slots)
    timings = load_mess_timings(FilesystemTool())
    now = now_ist()
    work = []
    for d in sorted(present):
        intake = repo.get_menu_intake(d, user.id)
        for meal_slot in sorted({item.meal_slot for item in intake.extracted_items}):
            summary["attempted"] += 1
            window = timings.get(meal_slot)
            # A menu confirmed mid-week must not put already-finished meals
            # on the Calendar (they'd show up straight away as "waiting for
            # your rating"). The Monday cron run is unaffected.
            ended = window is not None and datetime.combine(d, window[1], tzinfo=IST) <= now
            if meal_slot in skip_slots or ended:
                summary["skipped"] += 1
                continue
            work.append((d, meal_slot, intake.extracted_items))
    progress["total"] = len(work)

    succeeded_before = summary["succeeded"]
    for d, meal_slot, items in work:
        try:
            if dry_run:
                logger.info("[dry-run] would schedule user=%s date=%s slot=%s", user.id, d, meal_slot)
                summary["succeeded"] += 1
                continue
            claim_id = repo.claim_scheduled_meal(user.id, d, meal_slot)
            if claim_id is None:
                summary["skipped"] += 1  # already scheduled by an earlier run — idempotency guard
                continue
            try:
                _schedule_one(repo, user.id, d, meal_slot, items, claim_id)
                summary["succeeded"] += 1
            except GoogleAuthUnavailable:
                repo.set_needs_reauth(user.id, True)
                summary["failed"] += 1
                progress["error"] = "needs_reauth"
                break  # every remaining slot would fail the same way
            except Exception:
                logger.exception("schedule_week: run failed for user=%s date=%s slot=%s", user.id, d, meal_slot)
                summary["failed"] += 1
        finally:
            progress["done"] += 1

    if not dry_run and summary["succeeded"] > succeeded_before:
        # Catch-up path: a late upload just got this student scheduled —
        # clear any reminder event this week may already have gotten.
        _clear_reminder(repo, user.id, week_start)
    progress["finished"] = True


def _new_summary() -> dict:
    return {"attempted": 0, "succeeded": 0, "skipped": 0, "failed": 0, "reminded": 0}


def schedule_user_week(user_id, week_start: date) -> dict:
    """The post-upload-confirm trigger: schedules only the student who just
    confirmed their menu. Opens its own DB session — it runs as a
    background task that outlives the request that started it."""
    db = get_sessionmaker()()
    summary = _new_summary()
    try:
        repo = Repository(db)
        user = repo.get_user(user_id)
        if user is None:
            return summary
        if not repo.has_valid_token(user.id):
            PROGRESS[str(user.id)] = {
                "week_start": week_start.isoformat(), "total": 0, "done": 0, "finished": True, "error": "needs_reauth",
            }
            return summary
        _schedule_user(repo, user, week_start, dry_run=False, summary=summary)
        logger.info("schedule_user_week user=%s %s: %s", user.id, week_start, summary)
        return summary
    except Exception:
        logger.exception("schedule_user_week failed for user=%s week=%s", user_id, week_start)
        PROGRESS.setdefault(str(user_id), {}).update(finished=True, error="failed")
        return summary
    finally:
        db.close()


def schedule_week(week_start: date, dry_run: bool = False) -> dict:
    """The Monday cron run: every active student with a valid Calendar
    token, each against their own uploaded menu (or a reminder if they
    haven't uploaded one for this week yet)."""
    db = get_sessionmaker()()
    try:
        repo = Repository(db)
        users = [u for u in repo.list_active_users() if repo.has_valid_token(u.id)]
        summary = _new_summary()
        for user in users:
            _schedule_user(repo, user, week_start, dry_run, summary)
        logger.info("schedule_week %s: %s", week_start, summary)
        return summary
    finally:
        db.close()
