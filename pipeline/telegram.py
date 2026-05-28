"""
PodclickBot — Telegram notification sender.
Used by the pipeline to push status messages to JP's Telegram.
"""

import httpx

TG_API = "https://api.telegram.org"


def _token() -> str:
    from config import settings
    return settings.telegram_bot_token

def _chat_id() -> str:
    from config import settings
    return settings.telegram_chat_id

def is_configured() -> bool:
    return bool(_token() and _chat_id())


async def send(text: str, parse_mode: str = "Markdown") -> bool:
    """Send a message to JP's Telegram. Returns True on success."""
    if not is_configured():
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{TG_API}/bot{_token()}/sendMessage",
                json={
                    "chat_id":                  _chat_id(),
                    "text":                     text,
                    "parse_mode":               parse_mode,
                    "disable_web_page_preview": True,
                },
            )
        return resp.status_code == 200
    except Exception:
        return False


def send_sync(text: str) -> bool:
    """Synchronous wrapper for use inside thread executors."""
    import asyncio
    try:
        loop = asyncio.new_event_loop()
        return loop.run_until_complete(send(text))
    except Exception:
        return False
