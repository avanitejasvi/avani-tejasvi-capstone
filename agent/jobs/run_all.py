"""The single Railway Cron service target: python -m agent.jobs.run_all,
scheduled "0 4 * * 1" (04:00 UTC = 9:30am IST, Mondays). Feedback on last
week's events runs before scheduling this week's, in that order.
"""
import argparse

from agent.jobs import collect_feedback, weekly_run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("collect_feedback:", collect_feedback.run(dry_run=args.dry_run))
    print("weekly_run:", weekly_run.run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
