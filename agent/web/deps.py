from fastapi import Depends, Request
from sqlalchemy.orm import Session

from agent.db import get_db
from agent.models_db import User

__all__ = ["get_db", "get_current_user", "NotAuthenticated"]


class NotAuthenticated(Exception):
    """Raised by get_current_user; app.py's exception handler turns this
    into a redirect to /auth/login."""


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    # Re-checked on every request, not just at login, so deactivating a user
    # (is_active=False) takes effect immediately rather than on next login.
    user_id = request.session.get("user_id")
    if not user_id:
        raise NotAuthenticated()
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        request.session.clear()
        raise NotAuthenticated()
    return user
