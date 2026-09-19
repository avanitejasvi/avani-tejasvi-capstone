"""Manual "how was it" feedback.

Once STUDENT_EMAIL/the single attendee was removed for multi-tenancy, the
calendar owner's own RSVP carries no signal — collect_feedback.py's
automated check can only ever detect a *cancelled* event and apply a
decline. Without this page, a dish's rating could only ever go down, never
up from real experience: the preference model would stop being a real
self-improving loop (plan.md's "it updates itself" criterion) and become a
one-way ratchet. This restores the positive direction, and is also the only
remaining production caller of GeminiSkill.interpret_feedback_note.
"""
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from agent.db import get_db
from agent.llm_providers import classify_note_sentiment
from agent.models_db import User
from agent.react_agent import GeminiSkill, MenuPreferenceMatchingSkill
from agent.repository import Repository
from agent.timezone import today_ist
from agent.web.deps import get_current_user

router = APIRouter(prefix="/feedback", tags=["feedback"])
templates = Jinja2Templates(directory="agent/web/templates")


@router.get("", response_class=HTMLResponse)
def feedback_list(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = [r for r in Repository(db).list_pending_manual_feedback(user.id, today_ist()) if r.top_picks]
    return templates.TemplateResponse(request, "feedback.html", {"rows": rows})


@router.post("/{meal_id}")
async def submit_feedback(meal_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        meal_uuid = uuid.UUID(meal_id)
    except ValueError:
        return RedirectResponse("/feedback", status_code=302)

    form = await request.form()
    response = form.get("response")
    note = (form.get("note") or "").strip() or None

    repo = Repository(db)
    row = repo.get_scheduled_meal(meal_uuid)
    if row is None or row.user_id != user.id:
        return RedirectResponse("/feedback", status_code=302)

    if response in {"yes", "no", "maybe"}:
        matcher = MenuPreferenceMatchingSkill()
        own_key = repo.get_llm_key(user.id)  # (provider, api_key), or None -> fall back to the shared Gemini key
        gemini = GeminiSkill()
        prefs = repo.load_preferences(user.id)
        for dish_name in row.top_picks:
            sentiment = "neutral"
            if note:
                try:
                    if own_key is not None:
                        provider, api_key = own_key
                        sentiment = classify_note_sentiment(provider, api_key, dish_name, response, note)
                    elif gemini.available:
                        sentiment = gemini.interpret_feedback_note(dish_name, response, note)
                except Exception:
                    sentiment = "neutral"
            prefs = matcher.apply_feedback(prefs, dish_name, response, note, sentiment, row.event_date)
        repo.save_preferences(user.id, prefs)

    repo.mark_feedback_applied(row.id)
    return RedirectResponse("/feedback", status_code=302)
