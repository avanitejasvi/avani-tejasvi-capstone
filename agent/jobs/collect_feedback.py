"""Checked weekly for every unresolved scheduled_meals row: reads the
student's real Calendar RSVP (GoogleCalendarSkill.get_response — each
event carries a real attendee entry again as of scheduling.py passing the
student's own email through, restoring the exact mechanism this project's
single-user build already proved out live, per BUILD_LOG.md) and applies
it through the same apply_feedback the manual /feedback page uses. A
deleted/cancelled event is treated as a decline. If the student genuinely
hasn't responded yet, the row is left unresolved for a later run to check
again, rather than guessing.
"""
import argparse
import logging

from agent.db import get_sessionmaker
from agent.feedback_sentiment import classify_sentiment
from agent.react_agent import GoogleAuthUnavailable, GoogleCalendarSkill, MenuPreferenceMatchingSkill
from agent.repository import Repository
from agent.timezone import today_ist

logger = logging.getLogger("agent.jobs.collect_feedback")


def run(dry_run: bool = False) -> dict:
    db = get_sessionmaker()()
    matcher = MenuPreferenceMatchingSkill()
    summary = {"checked": 0, "applied": 0, "pending": 0, "no_signal": 0, "failed": 0}
    try:
        repo = Repository(db)
        rows = repo.list_unresolved_scheduled_meals(today_ist())
        for row in rows:
            summary["checked"] += 1
            if dry_run:
                continue

            if not row.calendar_event_id or not row.top_picks:
                repo.mark_feedback_applied(row.id)
                summary["no_signal"] += 1
                continue

            try:
                credentials = repo.build_user_credentials(row.user_id)
                calendar = GoogleCalendarSkill(credentials=credentials)
                status = calendar.get_event_status(row.calendar_event_id)
            except GoogleAuthUnavailable:
                summary["failed"] += 1
                continue
            except Exception:
                logger.exception("collect_feedback: status check failed for scheduled_meal=%s", row.id)
                summary["failed"] += 1
                continue

            if status is None or status == "cancelled":
                response, note = "no", None  # deleted/cancelled -> the one signal left without a real RSVP
            else:
                try:
                    response, note = calendar.get_response(row.calendar_event_id)
                except Exception:
                    logger.exception("collect_feedback: RSVP read failed for scheduled_meal=%s", row.id)
                    summary["failed"] += 1
                    continue
                if response is None:
                    summary["pending"] += 1
                    continue  # hasn't responded yet — check again on a later run

            prefs = repo.load_preferences(row.user_id)
            for dish_name in row.top_picks:
                sentiment = classify_sentiment(repo, row.user_id, dish_name, response, note)
                prefs = matcher.apply_feedback(prefs, dish_name, response, note, sentiment, row.event_date)
            repo.save_preferences(row.user_id, prefs)
            repo.mark_feedback_applied(row.id)
            summary["applied"] += 1

        logger.info("collect_feedback: %s", summary)
        return summary
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print("collect_feedback:", run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
