"""The meal-preference intake — split out from account-level /settings so
it reads as its own thing, and so it's what a first-time login actually
lands on (see agent/web/auth.py's post-login redirect).
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from agent.db import get_db
from agent.models_db import User
from agent.preference_questions import LEGACY_RESTRICTIONS, QUESTIONS, apply_answers, free_text_restrictions, selected_excludes
from agent.repository import Repository
from agent.web.deps import get_current_user

router = APIRouter(prefix="/preferences", tags=["preferences"])
templates = Jinja2Templates(directory="agent/web/templates")

MEAL_SLOTS = ["breakfast", "lunch", "evening_snacks", "dinner", "sunday_brunch"]


@router.get("", response_class=HTMLResponse)
def preferences_form(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    prefs = Repository(db).load_preferences(user.id)
    return templates.TemplateResponse(
        request, "preferences.html",
        {
            "prefs": prefs, "meal_slots": MEAL_SLOTS, "user": user, "questions": QUESTIONS,
            "selected_excludes": selected_excludes(prefs),
            "free_text_restrictions": free_text_restrictions(prefs),
        },
    )


@router.post("")
async def save_preferences(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    form = await request.form()
    dietary_restrictions = [
        r.strip() for r in (form.get("dietary_restrictions") or "").split(",")
        if r.strip() and r.strip().lower() not in LEGACY_RESTRICTIONS
    ]
    skip_meal_slots = [s for s in form.getlist("skip_meal_slots") if s in MEAL_SLOTS]

    repo = Repository(db)
    prefs = repo.load_preferences(user.id)
    # Reset to this submission's free-text list first, then let the
    # baseline questions layer their own restriction keywords (Jain ->
    # onion/garlic/..., dairy -> paneer/curd/..., etc.) on top fresh — so
    # unchecking one of those on a later visit actually removes it instead
    # of leaving a stale entry behind. The form only pre-fills the free-text
    # field with restrictions no ticked checkbox covers, which is what makes
    # that work; legacy abstract labels ("jain", ...) are dropped here.
    prefs.dietary_restrictions = dietary_restrictions
    prefs.skip_meal_slots = skip_meal_slots
    prefs = apply_answers(prefs, form)
    repo.save_preferences(user.id, prefs)
    repo.mark_onboarded(user.id)

    return RedirectResponse("/", status_code=302)
