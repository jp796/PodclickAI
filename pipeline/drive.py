"""
Google Drive integration for PodClickAI — OAuth 2.0 (user-delegated).

Reuses the SAME Google OAuth client as YouTube
(data/youtube_client_secrets.json, or YOUTUBE_CLIENT_SECRETS_JSON in .env).
The user clicks "Connect Google Drive" once; tokens are stored at
data/drive_token.json and refreshed automatically. No service-account key file
is needed — org policy (iam.disableServiceAccountKeyCreation) blocks those.

Setup:
  1. The OAuth client already exists (shared with YouTube).
  2. Visit /api/drive/auth → grant Drive access → token saved.
  3. (Optional) Set GOOGLE_DRIVE_PARENT_ID in .env to nest episode folders
     under a specific Drive folder. Without it, folders are created at the
     root of the connected account's My Drive.

Scope: https://www.googleapis.com/auth/drive  (create folders, upload, share)
"""

import json
import os
from pathlib import Path

BASE_DIR   = Path(__file__).parent.parent
DATA_DIR   = BASE_DIR / "data"
TOKEN_FILE = DATA_DIR / "drive_token.json"

SCOPES = ["https://www.googleapis.com/auth/drive"]
REDIRECT_URI = "http://localhost:8765/api/drive/callback"


# ── OAuth client secrets (shared with YouTube) ────────────────────────────────

def _secrets_path() -> str:
    """Locate the shared Google OAuth client secrets JSON."""
    env_path = os.getenv("YOUTUBE_CLIENT_SECRETS_JSON", "")
    if env_path and Path(env_path).exists():
        return env_path
    for cand in (DATA_DIR / "google_client_secrets.json", DATA_DIR / "youtube_client_secrets.json"):
        if cand.exists():
            return str(cand)
    return ""


def is_authorized() -> bool:
    """True if we have a Drive OAuth token on disk."""
    if not TOKEN_FILE.exists():
        return False
    try:
        return bool(json.loads(TOKEN_FILE.read_text()).get("token"))
    except Exception:
        return False


def is_configured() -> bool:
    """
    True when Drive is usable right now — i.e. the user has connected via OAuth.
    Callers (e.g. _build_guest_asset_package) only care 'can we upload now'.
    """
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
        raise RuntimeError("Drive not connected. Visit /api/drive/auth to connect.")

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
    """Build the OAuth 2.0 authorization URL for the user to visit."""
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
    # Persist PKCE verifier + state so exchange_code can complete the flow.
    (DATA_DIR / "drive_flow_state.json").write_text(json.dumps({
        "state":         state,
        "code_verifier": getattr(flow, "code_verifier", None),
    }))
    return auth_url


def exchange_code(code: str) -> dict:
    """Exchange auth code for tokens, save to disk. Returns {ok, email} or {ok:False, error}."""
    secrets = _secrets_path()
    if not secrets:
        return {"ok": False, "error": "Client secrets not configured"}
    try:
        from google_auth_oauthlib.flow import Flow

        flow = Flow.from_client_secrets_file(secrets, scopes=SCOPES, redirect_uri=REDIRECT_URI)
        _state_file = DATA_DIR / "drive_flow_state.json"
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

        # Best-effort: fetch the connected account email for display.
        email = ""
        try:
            from googleapiclient.discovery import build
            svc   = build("drive", "v3", credentials=flow.credentials)
            about = svc.about().get(fields="user(emailAddress)").execute()
            email = about.get("user", {}).get("emailAddress", "")
        except Exception:
            pass
        return {"ok": True, "email": email}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def account_email() -> str:
    """Return the connected Drive account email, or '' if unavailable."""
    try:
        svc   = _get_service()
        about = svc.about().get(fields="user(emailAddress)").execute()
        return about.get("user", {}).get("emailAddress", "")
    except Exception:
        return ""


# ── Drive operations (unchanged API — now backed by OAuth creds) ──────────────

def _get_service():
    """Build and return an authenticated Google Drive service, or raise."""
    from googleapiclient.discovery import build
    creds = get_credentials()
    return build("drive", "v3", credentials=creds)


def create_episode_folder(
    episode_title: str,
    episode_number: int,
    parent_folder_id: str = "",
) -> dict:
    """
    Create a Google Drive folder named "EP.{N} — {title}".
    Returns {"folder_id": str, "folder_url": str, "ok": True} or {"ok": False, "error": str}
    """
    try:
        service     = _get_service()
        parent_id   = parent_folder_id or os.getenv("GOOGLE_DRIVE_PARENT_ID", "")
        folder_name = f"EP.{episode_number} — {episode_title}"

        meta = {
            "name":     folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            meta["parents"] = [parent_id]

        folder = service.files().create(body=meta, fields="id").execute()
        fid    = folder["id"]

        # Make it publicly viewable (anyone with link)
        make_folder_public(fid)

        url = f"https://drive.google.com/drive/folders/{fid}?usp=sharing"
        return {"folder_id": fid, "folder_url": url, "ok": True}

    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"Drive error: {e}"}


def upload_file_to_folder(
    folder_id: str,
    file_path: str,
    mime_type: str = "application/octet-stream",
    filename: str = "",
) -> dict:
    """
    Upload a file into a Drive folder.
    Returns {"file_id": str, "ok": True} or {"ok": False, "error": str}
    """
    try:
        from googleapiclient.http import MediaFileUpload

        service  = _get_service()
        name     = filename or Path(file_path).name
        media    = MediaFileUpload(file_path, mimetype=mime_type, resumable=True)
        meta     = {"name": name, "parents": [folder_id]}
        uploaded = service.files().create(body=meta, media_body=media, fields="id").execute()
        return {"file_id": uploaded["id"], "ok": True}

    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"Upload error: {e}"}


def make_folder_public(folder_id: str) -> dict:
    """Grant 'anyone with link' reader access to a folder."""
    try:
        service = _get_service()
        service.permissions().create(
            fileId=folder_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
