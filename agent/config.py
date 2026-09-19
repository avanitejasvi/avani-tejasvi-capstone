"""Small, explicit env-var surface for the web app and jobs. Kept separate
from react_agent.py's own module-level env reads (GEMINI_API_KEY etc.),
which stay local to that file since they're not web/OAuth concerns.
"""
import os

GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
GOOGLE_REDIRECT_URI = os.environ["GOOGLE_REDIRECT_URI"]
ALLOWED_EMAIL_DOMAIN = os.getenv("ALLOWED_EMAIL_DOMAIN", "flame.edu.in")
SESSION_SECRET = os.environ["SESSION_SECRET"]
BASE_URL = os.environ["BASE_URL"]
