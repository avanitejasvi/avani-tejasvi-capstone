"""Checked once, a week later, for every unresolved scheduled_meals row: did
the event survive? Deleted/cancelled -> a synthetic decline on its
top_picks — the only feedback signal left once the calendar owner's own RSVP
carries no information (see GoogleCalendarSkill.get_event_status). Still
present -> no signal; there's nothing to learn from silence, and there's no
future information from an event whose owner will never RSVP against
themselves, so it's simply marked resolved either way.
"""
import argparse
import logging

from agent.db import get_sessionmaker
from agent.react_agent import GoogleAuthUnavailable, GoogleCalendarSkill, MenuPreferenceMatchingSkill
from agent.repository import Repository
from agent.timezone import today_ist

logger = logging.getLogger("agent.jobs.collect_feedback")

# response="no" + a neutral note is the synthetic-decline signal — matches
# MenuPreferenceMatchingSkill.apply_feedback's existing "no" handling exactly,
# just triggered by event-cancellation instead of a real RSVP.
SYNTHETIC_DECLINE_RESPONSE = "no"
SYNTHETIC_DECLINE_SENTIMENT = "neutral"


def run(dry_run: bool = False) -> dict:
    db = get_sessionmaker()()
    matcher = MenuPreferenceMatchingSkill()
    summary = {"checked": 0, "declined": 0, "no_signal": 0, "failed": 0}
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
                status = GoogleCalendarSkill(credentials=credentials).get_event_status(row.calendar_event_id)
            except GoogleAuthUnavailable:
                summary["failed"] += 1
                continue
            except Exception:
                logger.exception("collect_feedback: status check failed for scheduled_meal=%s", row.id)
                summary["failed"] += 1
                continue

            if status is None or status == "cancelled":
                prefs = repo.load_preferences(row.user_id)
                for dish_name in row.top_picks:
                    prefs = matcher.apply_feedback(
                        prefs, dish_name, SYNTHETIC_DECLINE_RESPONSE, None, SYNTHETIC_DECLINE_SENTIMENT, row.event_date
                    )
                repo.save_preferences(row.user_id, prefs)
                summary["declined"] += 1
            else:
                summary["no_signal"] += 1
            repo.mark_feedback_applied(row.id)

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
