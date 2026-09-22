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

MAX_CANDIDATES = 4  # "a small number of high-value items", not the old full-shortlist form
MAX_REPEATS = 4
RECENT_DAYS = 14
# How long a dish stays off the check-in candidate list after being asked
# about, whether or not the student actually answered — otherwise the same
# still-unresolved unknown/inferred dish gets re-shown every single week.
CHECKIN_COOLDOWN_DAYS = 14


def _this_weeks_items(repo: Repository) -> list[ExtractedDish]:
    """Every date in the current week holds the same one extraction (see
    weekly_run.py) — the first date that actually has a row is enough."""
    week_start = week_start_ist()
    for offset in range(7):
        intake = repo.get_menu_intake(week_start + timedelta(days=offset))
        if intake is not None:
            return intake.extracted_items
    return []


def _pick_candidates(items, prefs, today):
    """Targets genuinely unresolved items — confidence "unknown" or
    "inferred" only, per the confidence model in react_agent.py. A
    "confirmed" dish (real feedback already given, from any channel,
    regardless of score) never needs more information, so it's never a
    candidate here, no matter how it scores. Recently-asked dishes are
    cooled down so the same unresolved item isn't re-shown every week."""
    cooldown_cutoff = today - timedelta(days=CHECKIN_COOLDOWN_DAYS)
    asked_recently = {
        normalize_dish_name(dish.name)
        for dish in prefs.known_dishes
        if dish.last_checkin_asked is not None and dish.last_checkin_asked >= cooldown_cutoff
    }

    seen = set()
    unknown, inferred = [], []
    for item in items:
        key = normalize_dish_name(item.name)
        if key in seen or key in asked_recently:
            continue
        seen.add(key)
        tags = derive_tags_from_category(item.category, item.name)
        scored = matcher.match_dish(item.name, item.mess, tags, prefs)
        if scored.confidence == "unknown":
            unknown.append((item, scored))
        elif scored.confidence == "inferred":
            inferred.append((item, scored))
    random.shuffle(unknown)
    random.shuffle(inferred)
    # Truly unknown items add the most new information, so they're prioritized
    # ahead of already-inferred-but-unconfirmed ones.
    picks = (unknown + inferred)[:MAX_CANDIDATES]
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
    today = today_ist()
    candidates, scored = _pick_candidates(items, prefs, today)

    if candidates:
        # Register any genuinely-new candidate now, using the existing
        # register_new_dishes — so the POST handler's apply_feedback calls
        # (which only ever update an EXISTING known_dish) have something to
        # find, exactly the same as a real scheduling run would.
        prefs = matcher.register_new_dishes(prefs, scored, today)
        # Stamp the cooldown on every dish actually shown, new or not, so
        # _pick_candidates skips it next time regardless of whether the
        # student answers.
        asked = {normalize_dish_name(item.name) for item in candidates}
        for dish in prefs.known_dishes:
            if normalize_dish_name(dish.name) in asked:
                dish.last_checkin_asked = today
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
