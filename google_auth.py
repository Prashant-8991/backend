import os
from pathlib import Path
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from dotenv import load_dotenv

# Load .env from the same directory as this file so it works regardless of cwd
load_dotenv(Path(__file__).resolve().parent / ".env")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()

if not GOOGLE_CLIENT_ID:
    import warnings
    warnings.warn("GOOGLE_CLIENT_ID is not set. Google sign-in will fail.")


def verify_google_token(token: str) -> dict | None:
    if not GOOGLE_CLIENT_ID:
        return None
    try:
        idinfo = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=60,
        )
        if idinfo["iss"] not in ["accounts.google.com", "https://accounts.google.com"]:
            return None
        return {
            "google_id": idinfo["sub"],
            "email": idinfo.get("email", ""),
            "name": idinfo.get("name", ""),
            "picture": idinfo.get("picture", ""),
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Google token verification failed: {e}")
        return None
