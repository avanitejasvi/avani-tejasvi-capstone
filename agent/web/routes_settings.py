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

MEAL_SLOTS = ["breakfast", "lunch", "evening_snacks", "dinner", "sunday_brunch"]


@router.get("", response_class=HTMLResponse)
def settings_form(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    repo = Repository(db)
    prefs = repo.load_preferences(user.id)
    return templates.TemplateResponse(
        request, "settings.html",
        {
            "prefs": prefs, "meal_slots": MEAL_SLOTS, "user": user,
            "llm_providers": sorted(SUPPORTED_PROVIDERS),
            "current_llm_provider": repo.has_llm_key(user.id),
        },
    )


@router.post("")
async def save_settings(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    form = await request.form()
    dietary_restrictions = [r.strip() for r in (form.get("dietary_restrictions") or "").split(",") if r.strip()]
    skip_meal_slots = [s for s in form.getlist("skip_meal_slots") if s in MEAL_SLOTS]

    repo = Repository(db)
    prefs = repo.load_preferences(user.id)
    prefs.dietary_restrictions = dietary_restrictions
    prefs.skip_meal_slots = skip_meal_slots
    repo.save_preferences(user.id, prefs)
    repo.mark_onboarded(user.id)

    # Bring-your-own AI key, for this student's own /feedback sentiment calls only
    # (see agent/llm_providers.py) — leaving the key field blank keeps whatever's
    # already saved untouched; the "remove" checkbox is the only way to clear it.
    if form.get("remove_llm_key") == "on":
        repo.delete_llm_key(user.id)
    else:
        llm_api_key = (form.get("llm_api_key") or "").strip()
        llm_provider = form.get("llm_provider") or ""
        if llm_api_key and llm_provider in SUPPORTED_PROVIDERS:
            repo.save_llm_key(user.id, llm_provider, llm_api_key)

    return RedirectResponse("/", status_code=302)
