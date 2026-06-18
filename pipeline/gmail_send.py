"""
Gmail send-as for PodClickAI — OAuth 2.0 (user-delegated), Phase 6.

Lets PodClick send the guest asset email FROM the user's own Gmail
(e.g. jp@titanreteam.com) so it lands in the guest's primary inbox and
replies go back to the user. Reuses the SAME Google OAuth client as
YouTube/Drive (data/youtube_client_secrets.json). Token at
data/gmail_token.json, auto-refreshed.

Scopes:
  - https://www.googleapis.com/auth/gmail.send   (send only — low trust)
  - openid + userinfo.email                       (to show which account is connected)

Connect once at /api/gmail/auth. Nothing sends without an explicit
"Approve & Send" click in the Punch List — this module is just the hands.
"""

import base64
import json
import os
from email.mime.text import MIMEText
from pathlib import Path

BASE_DIR   = Path(__file__).parent.parent
DATA_DIR   = BASE_DIR / "data"
TOKEN_FILE = DATA_DIR / "gmail_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]
REDIRECT_URI = "http://localhost:8765/api/gmail/callback"


# ── OAuth client secrets (shared with YouTube/Drive) ──────────────────────────

def _secrets_path() -> str:
    env_path = os.getenv("YOUTUBE_CLIENT_SECRETS_JSON", "")
    if env_path and Path(env_path).exists():
        return env_path
    for cand in (DATA_DIR / "google_client_secrets.json", DATA_DIR / "youtube_client_secrets.json"):
        if cand.exists():
            return str(cand)
    return ""


def is_authorized() -> bool:
    """True if we have a Gmail OAuth token on disk."""
    if not TOKEN_FILE.exists():
        return False
    try:
        return bool(json.loads(TOKEN_FILE.read_text()).get("token"))
    except Exception:
        return False


def is_configured() -> bool:
    """Usable right now = connected via OAuth."""
    return is_authorized()


# ── Token storage + credentials ───────────────────────────────────────────────

def _save_token(creds) -> None:
    TOKEN_FILE.write_text(json.dumps({
        "token":         creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri":     creds.token_uri,
        "client_id":     creds.client_id,
        "client_secret": creds.client_secret,
        "scopes":        list(creds.scopes or SCOPES),
    }, indent=2))


def get_credentials():
    """Return valid OAuth Credentials, refreshing if expired. Raises if not connected."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GRequest

    if not TOKEN_FILE.exists():
        raise RuntimeError("Gmail not connected. Visit /api/gmail/auth to connect.")

    data  = json.loads(TOKEN_FILE.read_text())
    creds = Credentials(
        token         = data.get("token"),
        refresh_token = data.get("refresh_token"),
        token_uri     = data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id     = data.get("client_id"),
        client_secret = data.get("client_secret"),
        scopes        = data.get("scopes", SCOPES),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(GRequest())
        _save_token(creds)
    return creds


# ── OAuth flow ────────────────────────────────────────────────────────────────

def get_auth_url() -> str:
    secrets = _secrets_path()
    if not secrets:
        raise FileNotFoundError(
            "Google OAuth client secrets not found (data/youtube_client_secrets.json)."
        )
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(secrets, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    auth_url, state = flow.authorization_url(
        access_type            = "offline",
        include_granted_scopes = "true",
        prompt                 = "consent",
    )
    (DATA_DIR / "gmail_flow_state.json").write_text(json.dumps({
        "state":         state,
        "code_verifier": getattr(flow, "code_verifier", None),
    }))
    return auth_url


def exchange_code(code: str) -> dict:
    secrets = _secrets_path()
    if not secrets:
        return {"ok": False, "error": "Client secrets not configured"}
    try:
        from google_auth_oauthlib.flow import Flow

        flow = Flow.from_client_secrets_file(secrets, scopes=SCOPES, redirect_uri=REDIRECT_URI)
        _state_file = DATA_DIR / "gmail_flow_state.json"
        if _state_file.exists():
            try:
                _saved = json.loads(_state_file.read_text())
                if _saved.get("code_verifier"):
                    flow.code_verifier = _saved["code_verifier"]
                _state_file.unlink()
            except Exception:
                pass
        flow.fetch_token(code=code)
        _save_token(flow.credentials)
        return {"ok": True, "email": account_email()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def account_email() -> str:
    """Return the connected Gmail address, or '' if unavailable."""
    try:
        from googleapiclient.discovery import build
        creds = get_credentials()
        svc = build("oauth2", "v2", credentials=creds)
        info = svc.userinfo().get().execute()
        return info.get("email", "")
    except Exception:
        return ""


# ── Send ──────────────────────────────────────────────────────────────────────

def send_message(to_addr: str, subject: str, body_text: str, from_name: str = "") -> dict:
    """SYNC — send a plain-text email from the connected Gmail account.
    Run via run_in_executor (googleapiclient is blocking). Returns
    {ok, message_id, from} or {ok:False, error}."""
    try:
        from googleapiclient.discovery import build
        creds = get_credentials()
        svc   = build("gmail", "v1", credentials=creds)

        msg = MIMEText(body_text or "", _charset="utf-8")
        msg["To"] = to_addr
        msg["Subject"] = subject or "Your podcast episode is live"
        sender_email = account_email()
        if from_name and sender_email:
            msg["From"] = f"{from_name} <{sender_email}>"
        elif sender_email:
            msg["From"] = sender_email

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        sent = svc.users().messages().send(userId="me", body={"raw": raw}).execute()
        return {"ok": True, "message_id": sent.get("id", ""), "from": sender_email}
    except Exception as e:
        return {"ok": False, "error": str(e)}
