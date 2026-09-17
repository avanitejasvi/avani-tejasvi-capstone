import os
from pathlib import Path

from dotenv import load_dotenv, set_key
from google_auth_oauthlib.flow import InstalledAppFlow

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR.parent / ".env"
SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar",
]


def main() -> None:
    load_dotenv(ENV_PATH)
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit(
            "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env first — both come from the "
            "same OAuth 2.0 Client ID entry in Google Cloud Console under APIs & Services > Credentials."
        )

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    credentials = flow.run_local_server(port=0)

    set_key(str(ENV_PATH), "GOOGLE_REFRESH_TOKEN", credentials.refresh_token)
    print(f"GOOGLE_REFRESH_TOKEN written to {ENV_PATH}")


if __name__ == "__main__":
    main()
