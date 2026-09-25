"""The baseline preference questions — step 4 of onboarding, and what My
tastes' "Update" opens later. A returning student sees their saved answers
already selected; anything they don't change stays as it was.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from agent.db import get_db
from agent.models_db import User
from agent.preference_questions import (
    CATEGORIES, HIDE_WHEN_EXCLUDED, LEGACY_RESTRICTIONS, QUESTIONS_BY_ID, STEPS,
    apply_answers, free_text_restrictions, record_answers, selected_excludes,
)
from agent.repository import Repository
from agent.web.deps import get_current_user
from agent.web.templating import flash, templates
from agent.web.views import slot_label

router = APIRouter(prefix="/preferences", tags=["preferences"])

MEAL_SLOTS = ["breakfast", "lunch", "evening_snacks", "dinner", "sunday_brunch"]


def _flow(value) -> str:
    return value if value in ("onboarding", "tastes") else "tastes"


@router.get("", response_class=HTMLResponse)
def preferences_form(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    repo = Repository(db)
    prefs = repo.load_preferences(user.id)
    answers = repo.load_intake_answers(user.id)
    # Hard excludes are read back from the saved restrictions themselves, so
    # they're right even for students who onboarded before answers were kept.
    answers["hard_excludes"] = sorted(selected_excludes(prefs))
    flow = _flow(request.query_params.get("flow"))
    return templates.TemplateResponse(request, "preferences.html", {
        "user": user,
        "flow": flow,
        "pending": request.query_params.get("pending") or "",
        "back_url": "/menu/upload?flow=onboarding" if flow == "onboarding" else "/tastes",
        "categories": CATEGORIES,
        "steps": STEPS,
        "questions": QUESTIONS_BY_ID,
        "hide_when": {qid: sorted(values) for qid, values in HIDE_WHEN_EXCLUDED.items()},
        "answers": answers,
        "other_excludes": ", ".join(free_text_restrictions(prefs)),
        "skip_slots": prefs.skip_meal_slots,
        "meal_slots": [(slot, slot_label(slot)) for slot in MEAL_SLOTS],
        "returning": repo.is_onboarded(user.id),
    })


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
    repo.save_intake_answers(user.id, record_answers(repo.load_intake_answers(user.id), form))
    repo.mark_onboarded(user.id)
    flash(request, "Preferences saved")

    flow = _flow(form.get("flow"))
    pending = form.get("pending")
    if flow == "onboarding":
        return RedirectResponse(f"/menu/check/{pending}?flow=onboarding" if pending else "/", status_code=302)
    return RedirectResponse("/tastes", status_code=302)
