"""Each student's own weekly menu: upload a photo -> Gemini reads it in the
background -> the student checks what was read -> confirm -> their week is
scheduled. Nothing lands in menu_intake until confirmed, and one student's
upload never touches anyone else's menu or Calendar. No image is ever
persisted — extracted in memory, only its sha256 is kept.

Used both inside onboarding (flow=onboarding: upload, then the preferences
questions while Gemini works, then check/confirm) and from the Menu tab
(flow=menu: upload, check, confirm).
"""
import hashlib
import logging
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from agent.db import get_db, get_sessionmaker
from agent.models_db import User
from agent.react_agent import ExtractedDish, FilesystemTool, GeminiSkill, load_mess_structure
from agent.repository import Repository
from agent.timezone import week_start_ist
from agent.web.deps import get_current_user
from agent.web.templating import flash, shell_context, templates
from agent.web.views import group_menu, week_label

router = APIRouter(prefix="/menu", tags=["menu"])
logger = logging.getLogger("agent.web.routes_menu")

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
WEEKS_AHEAD = 2  # the week picker offers this week plus the next two


def _flow(value) -> str:
    return "onboarding" if value == "onboarding" else "menu"


def _week_options(repo: Repository, user_id) -> list[dict]:
    this_monday = week_start_ist()
    options = []
    for i in range(WEEKS_AHEAD + 1):
        monday = this_monday + timedelta(weeks=i)
        dates = [monday + timedelta(days=d) for d in range(7)]
        options.append({
            "value": monday.isoformat(),
            "label": week_label(monday),
            "uploaded": bool(repo.weeks_menu_dates_present(dates, user_id)),
        })
    return options


def _own_pending(repo: Repository, pending_id: str, user: User):
    try:
        row = repo.get_pending_upload(uuid.UUID(str(pending_id)))
    except ValueError:
        return None
    return row if row is not None and row.user_id == user.id else None


def _extract_in_background(pending_id, image_bytes: bytes, content_type: str) -> None:
    """GeminiSkill.extract_dishes_from_image, unchanged from react_agent.py's
    own live path — just run after the upload request has returned, in its
    own DB session. image_bytes is dropped when this returns."""
    db = get_sessionmaker()()
    try:
        repo = Repository(db)
        try:
            items = GeminiSkill().extract_dishes_from_image(image_bytes, content_type, load_mess_structure(FilesystemTool()))
        except Exception:
            logger.exception("menu extraction failed for pending upload %s", pending_id)
            repo.finish_pending_upload(pending_id, [], error="unreadable")
            return
        if not items:
            repo.finish_pending_upload(pending_id, [], error="no_dishes")
            return
        repo.finish_pending_upload(pending_id, [item.model_dump(mode="json") for item in items])
    finally:
        db.close()


# --- the Menu tab -------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
def menu_tab(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    repo = Repository(db)
    week_start = week_start_ist()
    days = group_menu(repo.get_week_menu_items(user.id, week_start), week_start)
    return templates.TemplateResponse(request, "menu.html", {
        **shell_context(user, "menu", week_start),
        "days": days,
        "week_label": week_label(week_start),
    })


# --- upload ---------------------------------------------------------------------

@router.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    repo = Repository(db)
    options = _week_options(repo, user.id)
    requested = request.query_params.get("week")
    selected = next((o["value"] for o in options if o["value"] == requested), options[0]["value"])
    return templates.TemplateResponse(request, "upload.html", {
        "user": user,
        "flow": _flow(request.query_params.get("flow")),
        "weeks": options,
        "selected_week": selected,
    })


@router.post("/upload")
async def upload_menu(
    background_tasks: BackgroundTasks,
    week_start: str = Form(...),
    flow: str = Form("menu"),
    image: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns immediately with where to go next; Gemini reads the photo in
    the background (10–30s), and /menu/upload/<id>/status reports when it's done."""
    if image.content_type not in ALLOWED_CONTENT_TYPES:
        return JSONResponse({"error": "That file type isn't supported — upload a JPEG, PNG or WEBP photo."}, status_code=400)
    image_bytes = await image.read()
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        return JSONResponse({"error": "That photo is larger than 10 MB — please upload a smaller one."}, status_code=400)
    try:
        # Snap whatever date was sent to that week's Monday.
        monday = week_start_ist(date.fromisoformat(week_start))
    except ValueError:
        return JSONResponse({"error": "Pick which week this menu is for."}, status_code=400)

    repo = Repository(db)
    pending_id = repo.create_pending_upload(user.id, monday, hashlib.sha256(image_bytes).hexdigest())
    background_tasks.add_task(_extract_in_background, pending_id, image_bytes, image.content_type)

    flow = _flow(flow)
    if flow == "onboarding":
        next_url = f"/preferences?flow=onboarding&pending={pending_id}"
    else:
        next_url = f"/menu/check/{pending_id}"
    return JSONResponse({"pending_id": str(pending_id), "next": next_url})


@router.get("/upload/{pending_id}/status")
def upload_status(pending_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pending = _own_pending(Repository(db), pending_id, user)
    if pending is None:
        return JSONResponse({"status": "missing"}, status_code=404)
    return {"status": pending.status, "error": pending.error}


# --- check + confirm ------------------------------------------------------------------

@router.get("/check/{pending_id}", response_class=HTMLResponse)
def check_menu(pending_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    repo = Repository(db)
    flow = _flow(request.query_params.get("flow"))
    pending = _own_pending(repo, pending_id, user)
    if pending is None:
        # Already confirmed, or it expired — nothing to check any more.
        return RedirectResponse("/" if flow == "onboarding" else "/menu", status_code=302)

    days = []
    if pending.status == "ready":
        days = group_menu([ExtractedDish(**item) for item in pending.extracted_items], pending.week_start)
    # No replace prompt here: a week that already has a menu only reaches
    # the upload box through the upload screen's "Replace it" confirm.
    return templates.TemplateResponse(request, "menu_check.html", {
        "user": user,
        "flow": flow,
        "pending": pending,
        "days": days,
        "week_label": week_label(pending.week_start),
    })


@router.post("/upload/confirm")
async def confirm_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    form = await request.form()
    flow = _flow(form.get("flow"))
    repo = Repository(db)
    pending = _own_pending(repo, form.get("pending_id"), user)
    if pending is None or pending.status != "ready":
        return RedirectResponse("/" if flow == "onboarding" else "/menu", status_code=302)

    monday = pending.week_start
    dates = [monday + timedelta(days=i) for i in range(7)]
    replacing = bool(repo.weeks_menu_dates_present(dates, user.id))

    # The same full extraction is stored under every date in the week; the
    # existing per-run day filter in act_filter_and_score (unchanged) is what
    # narrows it down to one weekday at scoring time.
    for d in dates:
        repo.upsert_menu_intake(user.id, d, pending.extracted_items, pending.sha256)
    repo.delete_pending_upload(pending.id)

    # Imported here: jobs.scheduling reads BASE_URL at import time.
    from agent.jobs.refresh_descriptions import refresh_week
    from agent.jobs.scheduling import PROGRESS, schedule_user_week

    # Seed progress now so the Almost there screen's first poll can't see a
    # stale "finished" from an earlier run before the task starts.
    PROGRESS[str(user.id)] = {"week_start": monday.isoformat(), "total": 0, "done": 0, "finished": False, "error": None}
    # schedule_user_week opens its own DB session — it must outlive this
    # request's, since it's still running after the response is sent.
    background_tasks.add_task(schedule_user_week, user.id, monday)
    if replacing:
        # Meals already on the Calendar for this week keep their slots
        # (claim_scheduled_meal's idempotency guard), so re-score their
        # picks against the replacement menu.
        background_tasks.add_task(refresh_week, monday, False, user.id)

    return RedirectResponse(f"/menu/scheduling?flow={flow}&week={monday.isoformat()}", status_code=302)


# --- scheduling progress ---------------------------------------------------------------

@router.get("/scheduling", response_class=HTMLResponse)
def scheduling_screen(request: Request, user: User = Depends(get_current_user)):
    flow = _flow(request.query_params.get("flow"))
    week = request.query_params.get("week") or week_start_ist().isoformat()
    next_url = f"/onboarding/done?week={week}" if flow == "onboarding" else "/menu/scheduled"
    return templates.TemplateResponse(request, "scheduling.html", {"user": user, "next_url": next_url})


@router.get("/scheduling/status")
def scheduling_status(user: User = Depends(get_current_user)):
    from agent.jobs.scheduling import PROGRESS

    progress = PROGRESS.get(str(user.id))
    if progress is None:
        # The web process restarted since this run began — nothing left to wait for.
        return {"total": 0, "done": 0, "finished": True, "error": None}
    return {key: progress.get(key) for key in ("total", "done", "finished", "error")}


@router.get("/scheduled")
def after_menu_scheduled(request: Request):
    flash(request, "Menu confirmed ✓")
    return RedirectResponse("/menu", status_code=302)
