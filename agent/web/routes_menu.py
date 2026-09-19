"""Two-step menu upload: upload -> preview the Gemini extraction -> explicit
confirm. Nothing lands in the shared menu_intake table until confirmed, so
one bad photo can't silently clobber the whole college's menu. No image is
ever persisted to disk — extracted in memory, only its sha256 is kept.
"""
import hashlib
from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from agent.db import get_db
from agent.models_db import User
from agent.react_agent import FilesystemTool, GeminiSkill, load_mess_structure
from agent.repository import Repository
from agent.timezone import week_start_ist
from agent.web.deps import get_current_user

router = APIRouter(prefix="/menu", tags=["menu"])
templates = Jinja2Templates(directory="agent/web/templates")

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@router.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse(request, "upload.html", {"default_week_start": week_start_ist()})


@router.post("/upload", response_class=HTMLResponse)
async def upload_menu(
    request: Request,
    week_start: str = Form(...),
    image: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if image.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(400, f"Unsupported file type {image.content_type!r} — upload a JPEG, PNG, or WEBP image.")

    image_bytes = await image.read()
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "Image is larger than 10 MB — please upload a smaller file.")

    # Snap whatever date was picked to that week's Monday, rather than rejecting
    # a non-Monday pick outright.
    monday = week_start_ist(date.fromisoformat(week_start))
    dates = [monday + timedelta(days=i) for i in range(7)]

    sha256 = hashlib.sha256(image_bytes).hexdigest()
    mess_structure = load_mess_structure(FilesystemTool())
    # GeminiSkill.extract_dishes_from_image is used completely unchanged from
    # react_agent.py's own live path — only the source of the bytes changed
    # (an upload, not a Drive fetch). image_bytes is discarded after this call;
    # only its hash is ever stored.
    items = GeminiSkill().extract_dishes_from_image(image_bytes, image.content_type, mess_structure)

    repo = Repository(db)
    pending_id = repo.create_pending_upload(
        user_id=user.id,
        week_start=monday,
        extracted_items=[item.model_dump(mode="json") for item in items],
        sha256=sha256,
    )
    existing_dates = repo.weeks_menu_dates_present(dates)

    return templates.TemplateResponse(request, "upload_preview.html", {
        "pending_id": pending_id,
        "week_start": monday,
        "dates": dates,
        "item_count": len(items),
        "sample_names": [item.name for item in items[:15]],
        "existing_dates": sorted(existing_dates),
    })


@router.post("/upload/confirm")
async def confirm_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    form = await request.form()
    pending_id = form.get("pending_id")
    overwrite = form.get("overwrite") == "on"

    repo = Repository(db)
    pending = repo.get_pending_upload(pending_id)
    if pending is None:
        raise HTTPException(400, "This upload preview has expired — please upload again.")

    monday = pending.week_start
    dates = [monday + timedelta(days=i) for i in range(7)]
    existing_dates = repo.weeks_menu_dates_present(dates)
    if existing_dates and not overwrite:
        raise HTTPException(
            400,
            f"{sorted(d.isoformat() for d in existing_dates)} already have a menu — "
            "tick 'overwrite existing days' to replace them.",
        )

    # The same full extraction is stored under every date in the week; the
    # existing per-run day filter in act_filter_and_score (unchanged) is what
    # narrows it down to one weekday at scoring time — same pattern the
    # earlier multi-day batch runs already relied on.
    for d in dates:
        repo.upsert_menu_intake(d, pending.extracted_items, pending.sha256, user.id)
    repo.delete_pending_upload(pending_id)

    # schedule_week opens its own DB session — it must outlive this request's,
    # since it's still running as a background task after the response is sent.
    from agent.jobs.scheduling import schedule_week
    background_tasks.add_task(schedule_week, monday)

    return RedirectResponse("/", status_code=302)
