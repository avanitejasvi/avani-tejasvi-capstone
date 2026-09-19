"""Shared week-scheduling logic — used by jobs/weekly_run.py's Monday cron
run AND by web/routes_menu.py's post-confirm catch-up trigger, so a menu
uploaded late in the week still results in scheduled meals without waiting
for the next Monday's cron.

Reads BASE_URL directly from the environment rather than via agent.config,
since this runs as the separate cron service, which shouldn't need the
web-only OAuth vars (GOOGLE_REDIRECT_URI, SESSION_SECRET) that module also
requires at import time.
"""
import logging
import os
from datetime import date, time as dt_time, timedelta

from googleapiclient.errors import HttpError

from agent.db import get_sessionmaker
from agent.react_agent import (
    FilesystemTool, GeminiSkill, GoogleAuthUnavailable, GoogleCalendarSkill,
    MenuPreferenceMatchingSkill, ReActMealAgent, deterministic_event_id,
)
from agent.repository import Repository

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
    agent = ReActMealAgent(
        user_id=str(user_id),
        calendar_skill=GoogleCalendarSkill(credentials=credentials),
        gemini_skill=GeminiSkill(),
        matcher=MenuPreferenceMatchingSkill(),
        fs=FilesystemTool(),
        repo=repo,
        live=True,
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


def schedule_week(week_start: date, dry_run: bool = False) -> dict:
    db = get_sessionmaker()()
    try:
        repo = Repository(db)
        dates = [week_start + timedelta(days=i) for i in range(7)]
        present = repo.weeks_menu_dates_present(dates)
        missing_dates = sorted(d for d in dates if d not in present)
        users = [u for u in repo.list_active_users() if repo.has_valid_token(u.id)]

        summary = {"attempted": 0, "succeeded": 0, "skipped": 0, "failed": 0, "missing_dates": [d.isoformat() for d in missing_dates]}

        if not present:
            for user in users:
                if not dry_run:
                    _remind_user(repo, user.id, week_start)
            logger.info("schedule_week %s: skipped: no menu (%s active users reminded)", week_start, len(users))
            return summary

        skip_slots_by_user = {user.id: set(repo.load_preferences(user.id).skip_meal_slots) for user in users}

        for d in sorted(present):
            intake = repo.get_menu_intake(d)
            slots_today = sorted({item.meal_slot for item in intake.extracted_items})
            for meal_slot in slots_today:
                for user in users:
                    summary["attempted"] += 1
                    if meal_slot in skip_slots_by_user.get(user.id, set()):
                        summary["skipped"] += 1
                        continue
                    if dry_run:
                        logger.info("[dry-run] would schedule user=%s date=%s slot=%s", user.id, d, meal_slot)
                        summary["succeeded"] += 1
                        continue
                    claim_id = repo.claim_scheduled_meal(user.id, d, meal_slot)
                    if claim_id is None:
                        summary["skipped"] += 1  # already scheduled by an earlier run — idempotency guard
                        continue
                    try:
                        _schedule_one(repo, user.id, d, meal_slot, intake.extracted_items, claim_id)
                        summary["succeeded"] += 1
                    except GoogleAuthUnavailable:
                        repo.set_needs_reauth(user.id, True)
                        summary["failed"] += 1
                    except Exception:
                        logger.exception("schedule_week: run failed for user=%s date=%s slot=%s", user.id, d, meal_slot)
                        summary["failed"] += 1

        if not dry_run and summary["succeeded"] > 0:
            # Catch-up path: a late upload just got everyone scheduled — clear
            # any reminder event this week may already have gotten.
            for user in users:
                _clear_reminder(repo, user.id, week_start)

        logger.info("schedule_week %s: %s", week_start, summary)
        return summary
    finally:
        db.close()
