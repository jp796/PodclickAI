"""
Google Drive integration for PodClickAI.
Creates episode asset folders and uploads files.

Setup:
  1. Create a Google Cloud Service Account with Drive API enabled
  2. Download the JSON key file
  3. Add to .env:  GOOGLE_SERVICE_ACCOUNT_JSON=/path/to/key.json
                   GOOGLE_DRIVE_PARENT_ID=<optional parent folder ID>
  4. Share your target Drive folder with the service account email (Editor)
"""

import json
import os
from pathlib import Path

# ── Lazy-load Google libs so missing deps don't crash the whole app ───────────

def _get_service():
    """Build and return an authenticated Google Drive service, or raise."""
    key_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not key_path:
        # Check default location
        default = Path(__file__).parent.parent / "data" / "service_account.json"
        if default.exists():
            key_path = str(default)
    if not key_path or not Path(key_path).exists():
        raise FileNotFoundError("Google service account JSON not found. Set GOOGLE_SERVICE_ACCOUNT_JSON in .env")

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        key_path,
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("drive", "v3", credentials=creds)


def is_configured() -> bool:
    """Return True if Google Drive credentials are available."""
    key_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if key_path and Path(key_path).exists():
        return True
    default = Path(__file__).parent.parent / "data" / "service_account.json"
    return default.exists()


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
