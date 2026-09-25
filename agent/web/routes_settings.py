"""The Settings tab — account, Calendar connection status, the
bring-your-own AI key, sign out and account deletion. Meal preferences live
at /preferences instead (agent/web/routes_preferences.py).
"""
import requests
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from agent.db import get_db
from agent.llm_providers import SUPPORTED_PROVIDERS
from agent.models_db import User
from agent.repository import Repository
from agent.timezone import week_start_ist
from agent.web.deps import get_current_user
from agent.web.templating import flash, shell_context, templates

router = APIRouter(prefix="/settings", tags=["settings"])

PROVIDER_LABELS = {"gemini": "Gemini", "anthropic": "Anthropic", "openai": "OpenAI", "groq": "Groq"}

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
    current = repo.has_llm_key(user.id)
    return templates.TemplateResponse(request, "settings.html", {
        **shell_context(user, "settings", week_start_ist()),
        "calendar_connected": repo.has_valid_token(user.id),
        "calendar_url": "https://calendar.google.com/calendar/r/week",
        "llm_providers": [(p, PROVIDER_LABELS.get(p, p)) for p in ["gemini", "anthropic", "openai", "groq"] if p in SUPPORTED_PROVIDERS],
        "current_llm_provider": PROVIDER_LABELS.get(current, current) if current else None,
    })


@router.post("")
async def save_settings(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    form = await request.form()
    repo = Repository(db)

    # Leaving the key field blank keeps whatever's already saved untouched;
    # the "remove" checkbox is the only way to clear it.
    if form.get("remove_llm_key") == "on":
        repo.delete_llm_key(user.id)
        flash(request, "Key removed")
    else:
        llm_api_key = (form.get("llm_api_key") or "").strip()
        llm_provider = form.get("llm_provider") or ""
        if llm_api_key and llm_provider in SUPPORTED_PROVIDERS:
            repo.save_llm_key(user.id, llm_provider, llm_api_key)
            flash(request, "Key saved")

    return RedirectResponse("/settings", status_code=302)


@router.post("/delete")
def delete_account(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    repo = Repository(db)
    refresh_token = repo.get_raw_refresh_token(user.id)
    if refresh_token:
        _revoke_google_token(refresh_token)
    repo.delete_user(user.id)
    request.session.clear()
    return RedirectResponse("/welcome", status_code=302)
