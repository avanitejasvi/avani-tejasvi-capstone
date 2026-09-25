"""Rating past meals (the self-serve "how was it" channel — a complement to
the automated weekly collect_feedback.py Calendar-RSVP pass, not the only
one) and the My tastes tab: the baseline answers plus an editable summary of
what the system currently understands — likes/avoids/still-learning, grouped
by each dish's confidence tier (react_agent.KnownDish.confidence).

Past meals are rated from This week's rating sheet and the weekly check-in,
both of which post here without leaving the page.
"""
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from agent.db import get_db
from agent.feedback_sentiment import classify_sentiment
from agent.models_db import User
from agent.preference_questions import answer_summary
from agent.react_agent import EAT_THRESHOLD, MenuPreferenceMatchingSkill
from agent.repository import Repository
from agent.timezone import today_ist, week_start_ist
from agent.web.deps import get_current_user
from agent.web.templating import flash, shell_context, templates

router = APIRouter(prefix="/feedback", tags=["feedback"])
tastes_router = APIRouter(tags=["tastes"])

# What the profile-edit dropdown's real choices mean in terms of the same
# yes/no/maybe scale every other real feedback channel already uses. Neutral
# is a genuine preference ("I'll eat it, just not a favorite") — deliberately
# a different word from the template's "unrated" sentinel (the Still-learning
# rows' default), which means "no opinion given" and is intentionally NOT a
# key here, so it's never mistaken for a real edit and never applied.
EDIT_RESPONSE = {"like": "yes", "neutral": "maybe", "avoid": "no"}


def _preference_summary(prefs):
    """Groups known_dishes by confidence tier for the editable summary.
    Unknown dishes aren't listed individually — there can be many, and
    they carry no real signal yet — just counted for transparency."""
    likes, avoids, learning, unknown_count = [], [], [], 0
    for dish in prefs.known_dishes:
        if dish.confidence == "confirmed":
            (likes if dish.rating >= EAT_THRESHOLD else avoids).append(dish)
        elif dish.confidence == "inferred":
            learning.append(dish)
        else:
            unknown_count += 1
    likes.sort(key=lambda d: d.rating, reverse=True)
    avoids.sort(key=lambda d: d.rating)
    learning.sort(key=lambda d: d.name)
    return {"likes": likes, "avoids": avoids, "learning": learning, "unknown_count": unknown_count}


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


@router.get("")
def feedback_list():
    """Old links (and Calendar reminders) point here — past meals are rated
    from This week's rating sheet now."""
    return RedirectResponse("/?rate=1", status_code=302)


@tastes_router.get("/tastes", response_class=HTMLResponse)
def my_tastes(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    repo = Repository(db)
    prefs = repo.load_preferences(user.id)
    return templates.TemplateResponse(request, "tastes.html", {
        **shell_context(user, "tastes", week_start_ist()),
        "answers": answer_summary(repo.load_intake_answers(user.id), prefs),
        "summary": _preference_summary(prefs),
    })


@router.post("/edit")
async def edit_preference(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """A batch profile edit — one submit for the whole Likes/Avoids/Still
    learning summary instead of a button per dish. Each row posts its dish
    name, a "current" value (which section it started in), and the
    selected "choice"; only rows where choice != current get applied.
    apply_feedback isn't idempotent (it bumps times_eaten/rating on every
    real call), so resubmitting every row's already-current value on a
    plain "Save changes" with nothing touched must be a no-op."""
    form = await request.form()
    dish_names = form.getlist("dish_name")
    currents = form.getlist("current")
    choices = form.getlist("choice")

    if len(dish_names) == len(currents) == len(choices):
        matcher = MenuPreferenceMatchingSkill()
        repo = Repository(db)
        prefs = repo.load_preferences(user.id)
        today = today_ist()
        changed = False
        for dish_name, current, choice in zip(dish_names, currents, choices):
            if choice == current or choice not in EDIT_RESPONSE:
                continue
            prefs = matcher.apply_feedback(prefs, dish_name.strip(), EDIT_RESPONSE[choice], None, "neutral", today)
            changed = True
        if changed:
            repo.save_preferences(user.id, prefs)
            flash(request, "Changes saved")
    return RedirectResponse("/tastes", status_code=302)


@router.post("/{meal_id}")
async def submit_feedback(meal_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        meal_uuid = uuid.UUID(meal_id)
    except ValueError:
        return JSONResponse({"ok": False}, status_code=404) if _wants_json(request) else RedirectResponse("/", status_code=302)

    form = await request.form()
    response = form.get("response")
    note = (form.get("note") or "").strip() or None

    repo = Repository(db)
    row = repo.get_scheduled_meal(meal_uuid)
    if row is None or row.user_id != user.id:
        return JSONResponse({"ok": False}, status_code=404) if _wants_json(request) else RedirectResponse("/", status_code=302)

    if response in {"yes", "no", "maybe"}:
        matcher = MenuPreferenceMatchingSkill()
        prefs = repo.load_preferences(user.id)
        for dish_name in row.top_picks:
            sentiment = classify_sentiment(repo, user.id, dish_name, response, note)
            prefs = matcher.apply_feedback(prefs, dish_name, response, note, sentiment, row.event_date)
        repo.save_preferences(user.id, prefs)

    repo.mark_feedback_applied(row.id)
    if _wants_json(request):
        return {"ok": True}
    flash(request, "Feedback saved")
    return RedirectResponse("/", status_code=302)
