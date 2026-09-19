"""Weekly check-in — short, high-signal questions generated from the
ACTUAL current week's uploaded menu (menu_intake), not the fixed
historical board /preferences is grounded in. Answers feed the SAME
MenuPreferenceMatchingSkill.apply_feedback the manual /feedback page and
the automated weekly collect_feedback.py job (real Calendar RSVPs) already
use — a check-in response is treated exactly like a real yes/no/maybe, so it becomes real experience
(times_eaten increments, rating adjusts) layered on top of the baseline,
never overwriting it.

  Baseline (/preferences):    "I generally like paneer" — stable, seeded once.
  Learned (known_dishes):     "I usually choose paneer, but not too often" —
                               the running rating/times_eaten built from
                               real responses over time.
  Weekly (this file):         "I've had paneer twice already this week,
                               give me something else" — a fresh nudge on
                               top, this week only.
"""
import random
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from agent.db import get_db
from agent.models_db import User
from agent.react_agent import ExtractedDish, MenuPreferenceMatchingSkill, derive_tags_from_category, normalize_dish_name
from agent.repository import Repository
from agent.timezone import today_ist, week_start_ist
from agent.web.deps import get_current_user

router = APIRouter(prefix="/checkin", tags=["checkin"])
templates = Jinja2Templates(directory="agent/web/templates")

matcher = MenuPreferenceMatchingSkill()

MAX_CANDIDATES = 6
MAX_REPEATS = 4
RECENT_DAYS = 14


def _this_weeks_items(repo: Repository) -> list[ExtractedDish]:
    """Every date in the current week holds the same one extraction (see
    weekly_run.py) — the first date that actually has a row is enough."""
    week_start = week_start_ist()
    for offset in range(7):
        intake = repo.get_menu_intake(week_start + timedelta(days=offset))
        if intake is not None:
            return intake.extracted_items
    return []


def _pick_candidates(items, prefs):
    """Prioritizes genuinely uncertain items — the ones a check-in answer
    can actually resolve — over ones already well understood, using the
    existing match_dish/scoring logic directly, not a parallel heuristic."""
    seen = set()
    uncertain, borderline = [], []
    for item in items:
        key = normalize_dish_name(item.name)
        if key in seen:
            continue
        seen.add(key)
        tags = derive_tags_from_category(item.category, item.name)
        scored = matcher.match_dish(item.name, item.mess, tags, prefs)
        if scored.source in ("no_data", "tag_weight_estimate"):
            uncertain.append((item, scored))
        elif scored.source == "known_dish" and 2.0 <= scored.score <= 4.0:
            borderline.append((item, scored))
    random.shuffle(uncertain)
    random.shuffle(borderline)
    picks = (uncertain + borderline)[:MAX_CANDIDATES]
    return [item for item, _ in picks], [scored for _, scored in picks]


def _recently_repeated(prefs, today):
    cutoff = today - timedelta(days=RECENT_DAYS)
    recent = [d for d in prefs.known_dishes if d.times_eaten > 1 and d.last_seen >= cutoff]
    recent.sort(key=lambda d: d.times_eaten, reverse=True)
    return recent[:MAX_REPEATS]


@router.get("", response_class=HTMLResponse)
def checkin_form(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    repo = Repository(db)
    prefs = repo.load_preferences(user.id)
    items = _this_weeks_items(repo)
    candidates, scored = _pick_candidates(items, prefs)

    if candidates:
        # Register any genuinely-new candidate now, using the existing
        # register_new_dishes — so the POST handler's apply_feedback calls
        # (which only ever update an EXISTING known_dish) have something to
        # find, exactly the same as a real scheduling run would.
        prefs = matcher.register_new_dishes(prefs, scored, today_ist())
        repo.save_preferences(user.id, prefs)

    return templates.TemplateResponse(request, "checkin.html", {
        "user": user,
        "has_menu": bool(items),
        "candidates": candidates,
        "repeats": _recently_repeated(prefs, today_ist()),
    })


@router.post("")
async def submit_checkin(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    form = await request.form()
    repo = Repository(db)
    prefs = repo.load_preferences(user.id)
    today = today_ist()

    for name in form.getlist("would_choose"):
        prefs = matcher.apply_feedback(prefs, name, "yes", None, "neutral", today)
    for name in form.getlist("would_skip"):
        prefs = matcher.apply_feedback(prefs, name, "no", None, "neutral", today)
    favorite = form.get("pick_favorite")
    if favorite:
        prefs = matcher.apply_feedback(prefs, favorite, "yes", None, "neutral", today)
    for name in form.getlist("too_often"):
        prefs = matcher.apply_feedback(prefs, name, "maybe", "having this too often this week", "negative", today)

    repo.save_preferences(user.id, prefs)
    return RedirectResponse("/", status_code=302)
