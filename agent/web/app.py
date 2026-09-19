from datetime import timedelta

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from agent.config import SESSION_SECRET
from agent.db import get_db
from agent.models_db import User
from agent.repository import Repository
from agent.timezone import today_ist, week_start_ist
from agent.web import auth as auth_routes
from agent.web import routes_feedback, routes_menu, routes_settings
from agent.web.deps import NotAuthenticated, get_current_user

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

templates = Jinja2Templates(directory="agent/web/templates")

app.include_router(auth_routes.router)
app.include_router(routes_settings.router)
app.include_router(routes_menu.router)
app.include_router(routes_feedback.router)


@app.exception_handler(NotAuthenticated)
def _redirect_to_login(request: Request, exc: NotAuthenticated) -> RedirectResponse:
    return RedirectResponse("/auth/login", status_code=302)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    repo = Repository(db)
    week_start = week_start_ist()
    week_dates = [week_start + timedelta(days=i) for i in range(7)]
    present_dates = repo.weeks_menu_dates_present(week_dates)
    pending_feedback = [r for r in repo.list_pending_manual_feedback(user.id, today_ist()) if r.top_picks]

    return templates.TemplateResponse(request, "dashboard.html", {
        "user": user,
        "no_menu_this_week": len(present_dates) == 0,
        "missing_dates": sorted(d for d in week_dates if d not in present_dates),
        # The banner is a live DB query, not a stored flag, so it stays correct
        # even if a reminder Calendar event itself failed to be created.
        "needs_reauth": not repo.has_valid_token(user.id),
        "pending_feedback_count": len(pending_feedback),
    })
