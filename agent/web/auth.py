"""The web OAuth flow — replaces authorize_google.py's one-shot desktop
InstalledAppFlow. Each login both authenticates the user (openid/email/
profile) and grants this app Calendar access for that user's own calendar,
in one consent screen.
"""
import base64
import hashlib
import secrets

import logging

import google.oauth2.id_token
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from google.auth.transport import requests as google_requests
from google_auth_oauthlib.flow import Flow
from sqlalchemy.orm import Session

from agent.config import ALLOWED_EMAIL_DOMAIN, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI
from agent.db import get_db
from agent.repository import CALENDAR_SCOPE, Repository

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger("agent.web.auth")

SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/userinfo.profile", CALENDAR_SCOPE]


def _build_flow() -> Flow:
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }
    return Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=GOOGLE_REDIRECT_URI)


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


# Every failed or cancelled sign-in lands back on the Home screen with one
# of these reasons in a banner (see routes_onboarding.welcome) — the code
# goes in the URL, never the message, so nothing user-supplied is echoed.
SIGN_IN_ERRORS = {
    "cancelled": "Sign-in was cancelled. Try again when you're ready.",
    "domain": f"Only @{ALLOWED_EMAIL_DOMAIN} accounts can use Forkcast.",
    "calendar": "Calendar access is required — keep Calendar ticked and try again.",
    "refresh": "Google didn't finish connecting your Calendar. Please try again.",
    "expired": "That sign-in attempt expired. Please try again.",
    "failed": "Something went wrong signing you in. Please try again.",
}


def _fail(code: str) -> RedirectResponse:
    return RedirectResponse(f"/welcome?error={code}", status_code=302)


@router.get("/login")
def login(request: Request):
    flow = _build_flow()
    code_verifier, code_challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    request.session["oauth_code_verifier"] = code_verifier
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        # Forces a refresh_token on every grant, including a returning user
        # re-consenting — without this, a silent re-auth can omit it entirely.
        prompt="consent",
        state=state,
        hd=ALLOWED_EMAIL_DOMAIN,  # narrows Google's account chooser; not itself a security boundary
        code_challenge=code_challenge,
        code_challenge_method="S256",
    )
    return RedirectResponse(authorization_url)


@router.get("/callback")
def callback(request: Request, db: Session = Depends(get_db)):
    if request.query_params.get("error"):
        return _fail("cancelled")

    expected_state = request.session.pop("oauth_state", None)
    code_verifier = request.session.pop("oauth_code_verifier", None)
    if not expected_state or expected_state != request.query_params.get("state"):
        return _fail("expired")

    code = request.query_params.get("code")
    if not code:
        return _fail("failed")

    try:
        flow = _build_flow()
        flow.fetch_token(code=code, code_verifier=code_verifier)
        credentials = flow.credentials
        id_info = google.oauth2.id_token.verify_oauth2_token(
            credentials.id_token, google_requests.Request(), audience=GOOGLE_CLIENT_ID
        )
    except Exception:
        logger.exception("OAuth token exchange failed")
        return _fail("failed")

    email = (id_info.get("email") or "").lower()
    email_verified = id_info.get("email_verified")
    hd = id_info.get("hd")
    domain_ok = (hd == ALLOWED_EMAIL_DOMAIN) or email.endswith("@" + ALLOWED_EMAIL_DOMAIN)
    if not email_verified or not domain_ok:
        return _fail("domain")

    granted_scopes = set(getattr(credentials, "granted_scopes", None) or credentials.scopes or [])
    if CALENDAR_SCOPE not in granted_scopes:
        return _fail("calendar")

    if not credentials.refresh_token:
        return _fail("refresh")

    repo = Repository(db)
    user = repo.upsert_user(email=email, google_sub=id_info["sub"], display_name=id_info.get("name"))
    repo.save_oauth_token(user.id, credentials.refresh_token, " ".join(sorted(granted_scopes)))

    request.session.clear()
    request.session["user_id"] = str(user.id)

    if not repo.is_onboarded(user.id):
        return RedirectResponse("/onboarding/how", status_code=302)
    return RedirectResponse("/", status_code=302)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/welcome", status_code=302)
