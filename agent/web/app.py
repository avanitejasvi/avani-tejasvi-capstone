from datetime import timedelta

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from agent.config import SESSION_SECRET
from agent.db import get_db
from agent.models_db import User
from agent.repository import Repository
from agent.timezone import today_ist, week_start_ist
from agent.web import auth as auth_routes
from agent.web import routes_checkin, routes_feedback, routes_menu, routes_onboarding, routes_preferences, routes_settings
from agent.web.deps import NotAuthenticated, get_current_user
from agent.web.templating import shell_context, templates
from agent.web.routes_checkin import _pick_candidates, _recently_repeated
from agent.web.views import meals_to_rate, missing_days_label, week_label, week_meals

app = FastAPI(title="meal-match-agent")

# Railway terminates TLS in front of this app; run uvicorn with
# --proxy-headers --forwarded-allow-ips="*" so https_only cookies still work.
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=True,
    same_site="lax",
    max_age=60 * 60 * 24 * 30,
)

app.mount("/static", StaticFiles(directory="agent/web/static"), name="static")

app.include_router(auth_routes.router)
app.include_router(routes_onboarding.router)
app.include_router(routes_preferences.router)
app.include_router(routes_checkin.router)
app.include_router(routes_settings.router)
app.include_router(routes_menu.router)
app.include_router(routes_feedback.router)
app.include_router(routes_feedback.tastes_router)


@app.exception_handler(NotAuthenticated)
def _redirect_to_welcome(request: Request, exc: NotAuthenticated) -> RedirectResponse:
    return RedirectResponse("/welcome", status_code=302)


@app.get("/", response_class=HTMLResponse)
def this_week(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    repo = Repository(db)
    if not repo.is_onboarded(user.id):
        return RedirectResponse("/onboarding/how", status_code=302)

    week_start = week_start_ist()
    week_dates = [week_start + timedelta(days=i) for i in range(7)]
    present_dates = repo.weeks_menu_dates_present(week_dates, user.id)
    missing = sorted(d for d in week_dates if d not in present_dates and d >= today_ist())
    next_monday = week_start + timedelta(days=7)
    next_week_ready = bool(repo.weeks_menu_dates_present([next_monday + timedelta(days=i) for i in range(7)], user.id))
    meals = week_meals(repo, user.id, week_start)
    upcoming = [m for m in meals if not m["past"]]
    next_week = week_meals(repo, user.id, next_monday) if next_week_ready else []
    to_rate = meals_to_rate(repo, user.id, today_ist())
    # Same candidate logic as the check-in page itself (read-only here), so
    # the card only appears when the check-in actually has questions.
    prefs = repo.load_preferences(user.id)
    candidates, _ = _pick_candidates(repo.get_week_menu_items(user.id, week_start), prefs, today_ist())
    checkin_ready = bool(to_rate or candidates or _recently_repeated(prefs, today_ist()))

    return templates.TemplateResponse(request, "week.html", {
        **shell_context(user, "week", week_start),
        # A live DB query, not a stored flag, so it stays correct even if a
        # reminder Calendar event itself failed to be created.
        "needs_reauth": not repo.has_valid_token(user.id),
        "no_menu": not present_dates,
        # Uploaded for next week only: say so instead of "no menu".
        "next_week_ready": f"{next_monday.day} {next_monday.strftime('%b')}" if next_week_ready else "",
        "missing_label": missing_days_label(missing) if present_dates else "",
        "to_rate": to_rate,
        "checkin_ready": checkin_ready,
        "open_rating": request.query_params.get("rate") == "1",
        "has_menu": bool(present_dates),
        "next_meal": (upcoming or next_week or [None])[0],
        "rest_meals": upcoming[1:],
        "next_week_meals": next_week if upcoming else next_week[1:],
        "week_label": week_label(week_start),
    })
