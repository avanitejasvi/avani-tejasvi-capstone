"""One-off script: migrates the existing single-user preferences_u001.json
into Postgres. Run once, locally or via `railway run`, before the first
real login — e.g.:

    python3 -m agent.migrate_json_to_db --email student.design15@flame.edu.in

Leaves agent/data/preferences_u001.json untouched on disk as an archival
backup; does not touch preferences_u001_extended_spec_recovered.json at all
(out of scope — see BUILD_LOG.md / [[capstone-status]]).
"""
import argparse
import json
from typing import Optional

from agent.db import get_sessionmaker
from agent.react_agent import DATA_DIR, KnownDish, UserPreferences
from agent.repository import Repository


def migrate_preferences(repo: Repository, user_id_slug: str, email: str, display_name: Optional[str]) -> None:
    path = DATA_DIR / f"preferences_{user_id_slug}.json"
    raw = json.loads(path.read_text())
    known_dishes = [KnownDish(**d) for d in raw.get("known_dishes", [])]

    user = repo.seed_user_by_email(email=email, display_name=display_name)
    prefs = UserPreferences(
        user_id=str(user.id),
        dietary_restrictions=raw.get("dietary_restrictions", []),
        skip_meal_slots=raw.get("skip_meal_slots", []),
        known_dishes=known_dishes,
        comment=raw.get("comment"),
        tag_weights_note=raw.get("tag_weights_note"),
    )
    repo.save_preferences(user.id, prefs)

    reloaded = repo.load_preferences(user.id)
    if len(reloaded.known_dishes) != len(known_dishes):
        raise RuntimeError(
            f"migration mismatch for {path}: wrote {len(known_dishes)} known_dishes, read back {len(reloaded.known_dishes)}"
        )
    print(f"Migrated {len(known_dishes)} known_dishes from {path.name} to {email} (user_id={user.id}).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id-slug", default="u001", help="matches preferences_<slug>.json in agent/data/")
    parser.add_argument("--email", required=True, help="the real student's @flame.edu.in address")
    parser.add_argument("--display-name", default=None)
    args = parser.parse_args()

    db = get_sessionmaker()()
    try:
        repo = Repository(db)
        migrate_preferences(repo, args.user_id_slug, args.email, args.display_name)
    finally:
        db.close()


if __name__ == "__main__":
    main()
