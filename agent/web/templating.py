"""The one Jinja2 environment every route renders through, plus the small
bits of context the app shell (tab bar / sidebar) needs on every page."""
from datetime import date
from typing import Optional

from fastapi import Request
from fastapi.templating import Jinja2Templates

from agent.web.views import week_label


def flash(request: Request, message: str) -> None:
    """A one-shot toast shown on the next page that renders (e.g. "Preferences saved")."""
    request.session["toast"] = message


def pop_toast(request: Request) -> Optional[str]:
    return request.session.pop("toast", None)


def _toast_context(request: Request) -> dict:
    return {"toast": pop_toast(request)}


templates = Jinja2Templates(directory="agent/web/templates", context_processors=[_toast_context])


def first_name(user) -> str:
    name = (user.display_name or "").strip()
    return name.split()[0] if name else user.email.split("@")[0]


def shell_context(user, tab: str, week_start: date) -> dict:
    return {
        "user": user,
        "tab": tab,
        "first_name": first_name(user),
        "initial": first_name(user)[:1].upper(),
        "shell_week_label": f"{week_label(week_start)} {week_start.year}",
    }
