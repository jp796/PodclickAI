"""
YouTube Data API v3 integration for PodClickAI.
Handles OAuth 2.0 authorization and video uploads.

Setup:
  1. Go to Google Cloud Console → APIs & Services → Credentials
  2. Create an OAuth 2.0 Client ID (Web Application type)
  3. Add http://localhost:8765/api/youtube/callback as Authorized redirect URI
  4. Download the JSON and set in .env:
       YOUTUBE_CLIENT_SECRETS_JSON=/path/to/client_secrets.json
       OR place it at data/youtube_client_secrets.json
  5. Click "Connect YouTube" in Settings to authorize

Scopes used:
  https://www.googleapis.com/auth/youtube.upload
  https://www.googleapis.com/auth/youtube.readonly
"""

import json
import os
from pathlib import Path

BASE_DIR   = Path(__file__).parent.parent
DATA_DIR   = BASE_DIR / "data"
TOKEN_FILE = DATA_DIR / "youtube_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

REDIRECT_URI = "http://localhost:8765/api/youtube/callback"


def _secrets_path() -> str:
    """Locate client secrets JSON file."""
    env_path = os.getenv("YOUTUBE_CLIENT_SECRETS_JSON", "")
    if env_path and Path(env_path).exists():
        return env_path
    default = DATA_DIR / "youtube_client_secrets.json"
    if default.exists():
        return str(default)
    return ""


def is_configured() -> bool:
    """True if client secrets file exists."""
    return bool(_secrets_path())


def is_authorized() -> bool:
    """True if we have a valid (non-expired) token on disk."""
    if not TOKEN_FILE.exists():
        return False
    try:
        data = json.loads(TOKEN_FILE.read_text())
        return bool(data.get("token"))
    except Exception:
        return False


def get_credentials():
    """
    Return valid google.oauth2.credentials.Credentials, refreshing if needed.
    Raises if not authorized.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GRequest

    if not TOKEN_FILE.exists():
        raise RuntimeError("Not authorized. Connect YouTube in Settings.")

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


def _save_token(creds):
    """Persist credentials to disk."""
    TOKEN_FILE.write_text(json.dumps({
        "token":         creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri":     creds.token_uri,
        "client_id":     creds.client_id,
        "client_secret": creds.client_secret,
        "scopes":        list(creds.scopes or SCOPES),
    }, indent=2))


def get_auth_url() -> str:
    """
    Build the OAuth 2.0 authorization URL for the user to visit.
    Returns the URL string.
    """
    secrets = _secrets_path()
    if not secrets:
        raise FileNotFoundError("YouTube client secrets not found. Set YOUTUBE_CLIENT_SECRETS_JSON in .env")

    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(
        secrets,
        scopes       = SCOPES,
        redirect_uri = REDIRECT_URI,
    )
    auth_url, state = flow.authorization_url(
        access_type            = "offline",
        include_granted_scopes = "true",
        prompt                 = "consent",
    )
    # Persist code_verifier (PKCE) and state so exchange_code can use them.
    # Without this, the new Flow in exchange_code is missing the verifier →
    # Google returns "Missing code verifier" (invalid_grant).
    _flow_state = {
        "state":          state,
        "code_verifier":  getattr(flow, "code_verifier", None),
    }
    (DATA_DIR / "youtube_flow_state.json").write_text(json.dumps(_flow_state))
    return auth_url


def exchange_code(code: str) -> dict:
    """
    Exchange auth code for tokens, save to disk.
    Returns {"ok": True, "channel": {...}} or {"ok": False, "error": str}
    """
    secrets = _secrets_path()
    if not secrets:
        return {"ok": False, "error": "Client secrets not configured"}

    try:
        from google_auth_oauthlib.flow import Flow
        from googleapiclient.discovery import build

        flow = Flow.from_client_secrets_file(
            secrets,
            scopes       = SCOPES,
            redirect_uri = REDIRECT_URI,
        )
        # Restore PKCE code_verifier saved during get_auth_url()
        _state_file = DATA_DIR / "youtube_flow_state.json"
        if _state_file.exists():
            try:
                _saved = json.loads(_state_file.read_text())
                if _saved.get("code_verifier"):
                    flow.code_verifier = _saved["code_verifier"]
                _state_file.unlink(missing_ok=True)
            except Exception:
                pass
        flow.fetch_token(code=code)
        _save_token(flow.credentials)

        # Fetch ALL channels on this account
        yt      = build("youtube", "v3", credentials=flow.credentials)
        result  = yt.channels().list(part="snippet,statistics", mine=True, maxResults=50).execute()
        items   = result.get("items", [])
        channels = []
        for ch in items:
            channels.append({
                "id":          ch["id"],
                "title":       ch["snippet"]["title"],
                "description": ch["snippet"].get("description", ""),
                "thumbnail":   ch["snippet"]["thumbnails"].get("default", {}).get("url", ""),
                "subscribers": ch["statistics"].get("subscriberCount", "0"),
                "videos":      ch["statistics"].get("videoCount", "0"),
                "url":         f"https://www.youtube.com/channel/{ch['id']}",
            })

        # Save all channels
        channels_file = DATA_DIR / "youtube_channels.json"
        channels_file.write_text(json.dumps(channels, indent=2))

        # Default active channel = first one (user can switch via picker)
        active = channels[0] if channels else {}
        _save_active_channel(active.get("id", ""))

        # Keep legacy single-channel file for backwards compat
        channel_file = DATA_DIR / "youtube_channel.json"
        channel_file.write_text(json.dumps(active, indent=2))

        return {"ok": True, "channel": active, "channels": channels}

    except Exception as e:
        return {"ok": False, "error": str(e)}


_ACTIVE_CHANNEL_FILE = DATA_DIR / "youtube_active_channel.json"


def _save_active_channel(channel_id: str) -> None:
    _ACTIVE_CHANNEL_FILE.write_text(json.dumps({"channel_id": channel_id}))


def get_active_channel_id() -> str:
    """Return the stored active channel ID, or '' if not set."""
    if _ACTIVE_CHANNEL_FILE.exists():
        try:
            return json.loads(_ACTIVE_CHANNEL_FILE.read_text()).get("channel_id", "")
        except Exception:
            pass
    return ""


def list_channels() -> list:
    """Return the cached list of all channels for this account."""
    f = DATA_DIR / "youtube_channels.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    # Fall back to single channel
    ch = get_channel_info()
    return [ch] if ch else []


def get_channel_info() -> dict:
    """Return cached channel info or fetch live."""
    channel_file = DATA_DIR / "youtube_channel.json"
    if channel_file.exists():
        try:
            return json.loads(channel_file.read_text())
        except Exception:
            pass

    if not is_authorized():
        return {}

    try:
        from googleapiclient.discovery import build
        creds  = get_credentials()
        yt     = build("youtube", "v3", credentials=creds)
        result = yt.channels().list(part="snippet,statistics", mine=True).execute()
        items  = result.get("items", [])
        if items:
            ch = items[0]
            info = {
                "id":          ch["id"],
                "title":       ch["snippet"]["title"],
                "description": ch["snippet"].get("description", ""),
                "thumbnail":   ch["snippet"]["thumbnails"].get("default", {}).get("url", ""),
                "subscribers": ch["statistics"].get("subscriberCount", "0"),
                "videos":      ch["statistics"].get("videoCount", "0"),
                "url":         f"https://www.youtube.com/channel/{ch['id']}",
            }
            channel_file.write_text(json.dumps(info, indent=2))
            return info
    except Exception:
        pass
    return {}


def upload_video(
    video_path: str,
    title: str,
    description: str = "",
    tags: list = None,
    category_id: str = "22",       # 22 = People & Blogs
    privacy_status: str = "private",  # "private"|"unlisted"|"public"
    publish_at: str = "",           # RFC 3339 e.g. "2026-06-15T09:00:00Z" — auto-publishes at this time
    thumbnail_path: str = "",
    made_for_kids: bool = False,
    progress_cb=None,
) -> dict:
    """
    Upload a video to YouTube via resumable upload.
    Returns {"ok": True, "video_id": str, "url": str} or {"ok": False, "error": str}

    privacy_status:
      "private"   — only you can see it (default, safest)
      "unlisted"  — anyone with the link
      "public"    — everyone

    category_id 22 = People & Blogs (good for podcasts)
    Other common: 28 = Science & Technology, 27 = Education, 25 = News & Politics
    """
    if not is_authorized():
        return {"ok": False, "error": "Not authorized. Connect YouTube in Settings first."}

    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        import httplib2

        if progress_cb:
            progress_cb("Connecting to YouTube…")

        creds = get_credentials()
        yt    = build("youtube", "v3", credentials=creds)

        body = {
            "snippet": {
                "title":       title[:100],   # YouTube max 100 chars
                "description": description,
                "tags":        tags or [],
                "categoryId":  category_id,
            },
            "status": {
                # When publish_at is set, use "private" privacy so YouTube schedules
                # the video to go public automatically at that time.
                "privacyStatus":          "private" if publish_at else privacy_status,
                "selfDeclaredMadeForKids": made_for_kids,
                **({"publishAt": publish_at} if publish_at else {}),
            },
        }

        media = MediaFileUpload(
            video_path,
            mimetype    = "video/*",
            resumable   = True,
            chunksize   = 10 * 1024 * 1024,  # 10MB chunks
        )

        if progress_cb:
            progress_cb("Uploading video to YouTube…")

        request  = yt.videos().insert(
            part  = "snippet,status",
            body  = body,
            media_body = media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status and progress_cb:
                pct = int(status.progress() * 100)
                progress_cb(f"YouTube upload: {pct}%")

        video_id  = response["id"]
        video_url = f"https://www.youtube.com/watch?v={video_id}"

        # Set thumbnail if provided
        if thumbnail_path and Path(thumbnail_path).exists():
            try:
                if progress_cb:
                    progress_cb("Setting thumbnail…")
                yt.thumbnails().set(
                    videoId    = video_id,
                    media_body = MediaFileUpload(thumbnail_path, mimetype="image/jpeg"),
                ).execute()
            except Exception as thumb_err:
                print(f"[youtube] thumbnail upload failed: {thumb_err}")

        return {"ok": True, "video_id": video_id, "url": video_url}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def revoke_token() -> bool:
    """Revoke and delete the stored token."""
    try:
        if TOKEN_FILE.exists():
            TOKEN_FILE.unlink()
        channel_file = DATA_DIR / "youtube_channel.json"
        if channel_file.exists():
            channel_file.unlink()
        return True
    except Exception:
        return False
