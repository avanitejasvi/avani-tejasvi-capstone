"""Account-level settings — the bring-your-own AI key and account deletion.
Meal preferences live at /preferences instead (agent/web/routes_preferences.py).
"""
import requests
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from agent.db import get_db
from agent.llm_providers import SUPPORTED_PROVIDERS
from agent.models_db import User
from agent.repository import Repository
from agent.web.deps import get_current_user

router = APIRouter(prefix="/settings", tags=["settings"])
templates = Jinja2Templates(directory="agent/web/templates")

GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"


def _revoke_google_token(refresh_token: str) -> None:
    """Best-effort — a network hiccup here must not block account deletion,
    since removing our own stored copy of the token (delete_user) is what
    actually matters if this call fails."""
    try:
        requests.post(GOOGLE_REVOKE_URL, params={"token": refresh_token}, timeout=10)
    except requests.RequestException:
        pass


@router.get("", response_class=HTMLResponse)
def settings_form(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    repo = Repository(db)
    return templates.TemplateResponse(
        request, "settings.html",
        {"user": user, "llm_providers": sorted(SUPPORTED_PROVIDERS), "current_llm_provider": repo.has_llm_key(user.id)},
    )


@router.post("")
async def save_settings(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    form = await request.form()
    repo = Repository(db)

    # Leaving the key field blank keeps whatever's already saved untouched;
    # the "remove" checkbox is the only way to clear it.
    if form.get("remove_llm_key") == "on":
        repo.delete_llm_key(user.id)
    else:
        llm_api_key = (form.get("llm_api_key") or "").strip()
        llm_provider = form.get("llm_provider") or ""
        if llm_api_key and llm_provider in SUPPORTED_PROVIDERS:
            repo.save_llm_key(user.id, llm_provider, llm_api_key)

    return RedirectResponse("/settings", status_code=302)


@router.post("/delete")
def delete_account(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    repo = Repository(db)
    refresh_token = repo.get_raw_refresh_token(user.id)
    if refresh_token:
        _revoke_google_token(refresh_token)
    repo.delete_user(user.id)
    request.session.clear()
    return RedirectResponse("/auth/login", status_code=302)
