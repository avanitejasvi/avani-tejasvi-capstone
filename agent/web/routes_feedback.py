"""Manual "how was it" feedback — a self-serve way to rate a past meal right
now, without waiting for a Calendar RSVP to land or the next weekly
collect_feedback.py pass to check it. Real Calendar RSVPs (see
GoogleCalendarSkill.get_response) are the primary automated signal again as
of the meal's own attendee entry being restored — this page is a
complementary, immediate channel, not the only one.
"""
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from agent.db import get_db
from agent.feedback_sentiment import classify_sentiment
from agent.models_db import User
from agent.react_agent import MenuPreferenceMatchingSkill
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
        prefs = repo.load_preferences(user.id)
        for dish_name in row.top_picks:
            sentiment = classify_sentiment(repo, user.id, dish_name, response, note)
            prefs = matcher.apply_feedback(prefs, dish_name, response, note, sentiment, row.event_date)
        repo.save_preferences(user.id, prefs)

    repo.mark_feedback_applied(row.id)
    return RedirectResponse("/feedback", status_code=302)
