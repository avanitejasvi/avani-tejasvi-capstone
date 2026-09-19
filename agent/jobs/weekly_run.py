"""Cron entrypoint (Monday 9:30am IST) — computes this week's Monday in IST
and schedules it for every active user. The actual scheduling logic lives in
scheduling.py, shared with the post-upload-confirm catch-up trigger.
"""
import argparse

from agent.jobs.scheduling import schedule_week
from agent.timezone import week_start_ist


def run(dry_run: bool = False) -> dict:
    return schedule_week(week_start_ist(), dry_run=dry_run)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print("weekly_run:", run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
