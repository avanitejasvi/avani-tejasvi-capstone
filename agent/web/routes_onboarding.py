"""The signed-out Home screen and the onboarding screens that aren't forms
of their own. The flow, in order:

  /welcome            Home — "Sign in with Google" (real OAuth, auth.py)
  /onboarding/how     How it works (new students only)
  /menu/upload        Add this week's menu        (routes_menu, flow=onboarding)
  /preferences        The baseline questions      (routes_preferences)
  /menu/check/<id>    Check your menu             (routes_menu)
  /menu/scheduling    Almost there                (routes_menu)
  /onboarding/done    You're all set
"""
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from agent.db import get_db
from agent.models_db import User
from agent.repository import Repository
from agent.timezone import week_start_ist
from agent.web.auth import SIGN_IN_ERRORS
from agent.web.deps import get_current_user
from agent.web.templating import templates
from agent.web.views import week_meals

router = APIRouter(tags=["onboarding"])


def calendar_week_url(week_start: date) -> str:
    return f"https://calendar.google.com/calendar/r/week/{week_start.year}/{week_start.month}/{week_start.day}"


@router.get("/welcome", response_class=HTMLResponse)
def welcome(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "welcome.html", {
        "error": SIGN_IN_ERRORS.get(request.query_params.get("error", "")),
    })


@router.get("/onboarding")
def onboarding_start():
    return RedirectResponse("/onboarding/how", status_code=302)


@router.get("/onboarding/how", response_class=HTMLResponse)
def how_it_works(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse(request, "how.html", {"user": user})


@router.get("/onboarding/done", response_class=HTMLResponse)
def all_set(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        week_start = week_start_ist(date.fromisoformat(request.query_params.get("week", "")))
    except ValueError:
        week_start = week_start_ist()
    meals = [m for m in week_meals(Repository(db), user.id, week_start) if not m["past"]]
    return templates.TemplateResponse(request, "done.html", {
        "user": user,
        "meals": meals[:2],
        # The student tapped "Continue" on the Almost there screen before the
        # first upcoming meals were on their Calendar yet.
        "still_scheduling": not meals,
        "calendar_url": calendar_week_url(week_start),
    })
