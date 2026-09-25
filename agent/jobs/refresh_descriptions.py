"""On-demand refresh of already-scheduled meals' Calendar descriptions to
reflect current preferences — e.g. after a batch of profile edits or a
matching-logic change — without touching the event's time, attendee, or
RSVP state. Distinct from scheduling.py's schedule_week, which only ever
creates a meal once per (user, date, slot) via claim_scheduled_meal's
idempotency guard: this walks the OPPOSITE set — rows that already have a
calendar_event_id — and re-scores + patches their description in place.
"""
import argparse
import logging
from datetime import date, timedelta

from agent.db import get_sessionmaker
from agent.react_agent import (
    FilesystemTool, GeminiSkill, GoogleAuthUnavailable, GoogleCalendarSkill,
    MenuPreferenceMatchingSkill, ReActMealAgent,
)
from agent.repository import Repository
from agent.timezone import week_start_ist

logger = logging.getLogger("agent.jobs.refresh_descriptions")


def _refresh_one(repo: Repository, matcher: MenuPreferenceMatchingSkill, row) -> bool:
    credentials = repo.build_user_credentials(row.user_id)  # raises GoogleAuthUnavailable -> caller counts as failed
    intake = repo.get_menu_intake(row.event_date, row.user_id)
    if intake is None:
        return False  # menu since removed; nothing to re-score against

    agent = ReActMealAgent(
        user_id=str(row.user_id), calendar_skill=GoogleCalendarSkill(credentials=credentials),
        gemini_skill=GeminiSkill(), matcher=matcher, fs=FilesystemTool(), repo=repo, live=True,
    )
    prefs = agent.perceive_preferences()
    if agent.reason_skip_check(prefs, row.meal_slot):
        return False  # now a skip_meal_slots slot — leave the already-scheduled event's description alone

    scored = agent.act_filter_and_score(prefs, intake, row.meal_slot, row.event_date)
    prefs = agent.act_register_new_dishes(prefs, scored, row.event_date)
    agent.act_persist_preferences(prefs)

    top_picks = matcher.select_top_picks(scored)
    fallback_picks = [] if top_picks else matcher.select_fallback_picks(scored)
    selected = [s.name for s in scored if s.decision == "eat"]
    top_pick_names = [s.name for s in (top_picks or fallback_picks)]
    description = matcher.build_event_description(scored, top_picks, fallback_picks, conflict_flag=not row.conflict_resolved)

    agent.calendar.update_description(row.calendar_event_id, description)
    repo.update_scheduled_meal(row.id, selected_items=selected, top_picks=top_pick_names)
    return True


def refresh_week(week_start: date, dry_run: bool = False, user_id=None) -> dict:
    """user_id narrows the run to one student — routes_menu uses that after
    a student replaces their own menu for a week that's already scheduled."""
    db = get_sessionmaker()()
    try:
        repo = Repository(db)
        matcher = MenuPreferenceMatchingSkill()
        dates = [week_start + timedelta(days=i) for i in range(7)]
        rows = [r for r in repo.list_scheduled_meals_in_range(dates) if user_id is None or r.user_id == user_id]
        summary = {"attempted": len(rows), "refreshed": 0, "unchanged": 0, "failed": 0}

        for row in rows:
            if dry_run:
                continue
            try:
                if _refresh_one(repo, matcher, row):
                    summary["refreshed"] += 1
                else:
                    summary["unchanged"] += 1
            except GoogleAuthUnavailable:
                summary["failed"] += 1
            except Exception:
                logger.exception("refresh_descriptions: failed for scheduled_meal=%s", row.id)
                summary["failed"] += 1

        logger.info("refresh_week %s: %s", week_start, summary)
        return summary
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print("refresh_descriptions:", refresh_week(week_start_ist(), dry_run=args.dry_run))


if __name__ == "__main__":
    main()
