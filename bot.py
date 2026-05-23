"""
PodclickBot — Telegram command listener for Podcast Studio.
Runs as a separate process alongside main.py.

Commands:
  /start             Welcome message
  /help              List commands
  /status            What's currently processing
  /episode           Latest episode info
  /episodes          Last 5 episodes
  /queue             List scheduled release queue
  /schedule ID time  Reschedule an entry (e.g. /schedule ab12 "May 12 9am")
  /publish ID        Publish a queued episode immediately
  /cancel ID         Remove episode from queue
  /clips             Queued TikTok clips

Run:  python bot.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

TG_API        = "https://api.telegram.org"
BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID       = os.getenv("TELEGRAM_CHAT_ID", "")
STUDIO_URL    = "http://localhost:8765"
DATA_DIR      = Path(__file__).parent / "data"
EPISODES_FILE = DATA_DIR / "episodes.json"

POLL_INTERVAL = 2
TIMEOUT_SECS  = 30


# ── Telegram API helpers ──────────────────────────────────────────────────────

async def tg_get(client: httpx.AsyncClient, method: str, **params):
    resp = await client.get(f"{TG_API}/bot{BOT_TOKEN}/{method}", params=params)
    return resp.json()

async def send(client: httpx.AsyncClient, text: str, parse_mode="Markdown"):
    await client.post(
        f"{TG_API}/bot{BOT_TOKEN}/sendMessage",
        json={
            "chat_id":                  CHAT_ID,
            "text":                     text,
            "parse_mode":               parse_mode,
            "disable_web_page_preview": True,
        },
        timeout=10,
    )


# ── Command handlers ──────────────────────────────────────────────────────────

def load_episodes() -> list:
    if EPISODES_FILE.exists():
        return json.loads(EPISODES_FILE.read_text())
    return []


async def cmd_start(client):
    await send(client, (
        "🎙️ *PodclickBot online!*\n\n"
        "Your Podcast Command Center assistant.\n\n"
        "Type /help to see all commands."
    ))


async def cmd_help(client):
    await send(client, (
        "*PodclickBot Commands*\n\n"
        "📊 `/status` — what's processing right now\n"
        "🎙️ `/episode` — your latest episode\n"
        "📋 `/episodes` — last 5 episodes\n"
        "📅 `/queue` — release schedule\n"
        "📅 `/schedule ID time` — reschedule an entry\n"
        "🚀 `/publish ID` — publish a queued episode now\n"
        "❌ `/cancel ID` — remove from queue\n"
        "✂️ `/clips` — TikTok clip studio\n"
        "🎙️ `/guest` — upcoming booked guests\n\n"
        "_Open the studio:_ http://localhost:8765"
    ))


async def cmd_status(client: httpx.AsyncClient):
    try:
        resp = await client.get(f"{STUDIO_URL}/api/status", timeout=5)
        data = resp.json()
        active = data.get("active_jobs", [])
        if not active:
            await send(client, "✅ *Studio idle* — no jobs running.\n\nDrop files at http://localhost:8765")
        else:
            lines = ["⚙️ *Active jobs:*\n"]
            for job in active:
                step = job.get("step", "processing")
                name = job.get("filename", "unknown")
                lines.append(f"• `{name}` — {step}")
            await send(client, "\n".join(lines))
    except Exception:
        await send(client, "⚠️ Couldn't reach the studio.\n`cd ~/podcast-studio && ./run.sh`")


async def cmd_episode(client):
    episodes = load_episodes()
    if not episodes:
        await send(client, "No episodes yet. Process one at http://localhost:8765")
        return
    ep = episodes[-1]
    url = ep.get("spotify_url", "")
    link = f"\n[Listen on Buzzsprout]({url})" if url else ""
    await send(client, (
        f"🎙️ *EP. {ep.get('episode_number', '?')} — {ep.get('title', 'Untitled')}*\n"
        f"Fillers removed: {ep.get('fillers_removed', 0)} · "
        f"Time saved: {ep.get('duration_saved', 0):.1f}s"
        f"{link}"
    ))


async def cmd_episodes(client):
    episodes = load_episodes()
    if not episodes:
        await send(client, "No episodes yet.")
        return
    recent = list(reversed(episodes))[:5]
    lines  = ["📋 *Last 5 episodes:*\n"]
    for ep in recent:
        url  = ep.get("spotify_url", "")
        link = f" [↗]({url})" if url else ""
        lines.append(f"*EP.{ep.get('episode_number','?')}* {ep.get('title','Untitled')}{link}")
    await send(client, "\n".join(lines))


async def cmd_queue(client: httpx.AsyncClient):
    try:
        resp = await client.get(f"{STUDIO_URL}/api/queue", timeout=5)
        entries = resp.json()
        if not entries:
            await send(client, "📅 *Release queue is empty.*\n\nSchedule an episode at http://localhost:8765")
            return
        lines = ["📅 *Release Queue:*\n"]
        for e in entries:
            dt  = datetime.fromisoformat(e["scheduled_at_iso"]).strftime("%b %-d @ %-I:%M %p")
            st  = {"scheduled": "⏳", "publishing": "🔄", "published": "✅", "failed": "❌"}.get(e["status"], "•")
            lines.append(f"{st} `{e['entry_id']}` *EP.{e['episode_number']}* — {dt}\n   _{e['title']}_")
        await send(client, "\n".join(lines))
    except Exception as exc:
        await send(client, f"⚠️ Couldn't fetch queue: {exc}")


async def cmd_schedule(client: httpx.AsyncClient, args: list):
    """Usage: /schedule ENTRY_ID "May 12 9am" """
    if len(args) < 2:
        await send(client, "Usage: `/schedule ENTRY_ID \"May 12 9am\"`")
        return
    entry_id  = args[0]
    time_str  = " ".join(args[1:]).strip('"\'')
    # Try to parse the date
    formats = ["%B %d %I%p", "%b %d %I%p", "%B %d %I:%M%p", "%b %d %I:%M%p",
               "%m/%d/%Y %I:%M%p", "%Y-%m-%dT%H:%M"]
    new_dt = None
    for fmt in formats:
        try:
            new_dt = datetime.strptime(time_str, fmt).replace(year=datetime.now().year)
            break
        except ValueError:
            pass
    if not new_dt:
        await send(client, f"⚠️ Couldn't parse `{time_str}`\nTry: `May 12 9am` or `May 12 9:30am`")
        return
    try:
        resp = await client.patch(
            f"{STUDIO_URL}/api/queue/{entry_id}",
            json={"scheduled_at": new_dt.timestamp()},
            timeout=5,
        )
        if resp.status_code == 200:
            dt_str = new_dt.strftime("%b %-d at %-I:%M %p")
            await send(client, f"✅ `{entry_id}` rescheduled to *{dt_str}*")
        else:
            await send(client, f"⚠️ {resp.json().get('error', 'Unknown error')}")
    except Exception as exc:
        await send(client, f"⚠️ Error: {exc}")


async def cmd_publish(client: httpx.AsyncClient, args: list):
    if not args:
        await send(client, "Usage: `/publish ENTRY_ID`")
        return
    entry_id = args[0]
    try:
        resp = await client.post(f"{STUDIO_URL}/api/queue/{entry_id}/publish", timeout=15)
        if resp.status_code == 200:
            await send(client, f"🚀 `{entry_id}` published immediately!")
        else:
            await send(client, f"⚠️ {resp.json().get('error', 'Unknown error')}")
    except Exception as exc:
        await send(client, f"⚠️ Error: {exc}")


async def cmd_cancel(client: httpx.AsyncClient, args: list):
    if not args:
        await send(client, "Usage: `/cancel ENTRY_ID`")
        return
    entry_id = args[0]
    try:
        resp = await client.delete(f"{STUDIO_URL}/api/queue/{entry_id}", timeout=5)
        if resp.status_code == 200:
            await send(client, f"❌ `{entry_id}` removed from queue.")
        else:
            await send(client, f"⚠️ {resp.json().get('error', 'Entry not found')}")
    except Exception as exc:
        await send(client, f"⚠️ Error: {exc}")


async def cmd_clips(client):
    await send(client, "✂️ *TikTok Clip Studio* — open the studio to view clips.\nhttp://localhost:8765")


async def cmd_guest(client: httpx.AsyncClient):
    """Show all booked/upcoming guests."""
    try:
        resp  = await client.get(f"{STUDIO_URL}/api/guests", timeout=5)
        guests = resp.json()
        if not guests:
            await send(client, "🎙️ *No guests on record yet.*\n\nAdd guests at http://localhost:8765 → Guests tab")
            return
        booked    = [g for g in guests if g.get("status") in ("booked", "recorded")]
        aired     = [g for g in guests if g.get("status") == "aired"]
        prospects = [g for g in guests if g.get("status") == "prospect"]
        lines = ["🎙️ *Guest CRM*\n"]
        if booked:
            lines.append("*📅 Upcoming / In Pipeline:*")
            for g in booked:
                ep = f" — EP.{g['episode_number']}" if g.get("episode_number") else ""
                lines.append(f"• *{g['name']}*{ep} [{g['status']}]\n  _{g.get('topic','')}_")
        if aired:
            lines.append(f"\n*✅ Recently Aired:* {len(aired)} episode(s)")
            for g in aired[:3]:
                lines.append(f"• {g['name']} — EP.{g.get('episode_number','?')}")
        if prospects:
            lines.append(f"\n*💡 Prospects:* {len(prospects)} to reach out to")
        await send(client, "\n".join(lines))
    except Exception as exc:
        await send(client, f"⚠️ Couldn't fetch guests: {exc}")


async def cmd_unknown(client, text: str):
    await send(client, f"🤔 I don't know `{text}`\n\nTry /help for all commands.")


# ── Message router ────────────────────────────────────────────────────────────

async def handle(client: httpx.AsyncClient, message: dict):
    chat_id = str(message.get("chat", {}).get("id", ""))
    text    = (message.get("text") or "").strip()

    if chat_id != CHAT_ID:
        return

    parts = text.split() if text else []
    cmd   = parts[0].lower().split("@")[0] if parts else ""
    args  = parts[1:]

    if cmd == "/start":             await cmd_start(client)
    elif cmd == "/help":            await cmd_help(client)
    elif cmd == "/status":          await cmd_status(client)
    elif cmd == "/episode":         await cmd_episode(client)
    elif cmd == "/episodes":        await cmd_episodes(client)
    elif cmd == "/queue":           await cmd_queue(client)
    elif cmd == "/schedule":        await cmd_schedule(client, args)
    elif cmd == "/publish":         await cmd_publish(client, args)
    elif cmd == "/cancel":          await cmd_cancel(client, args)
    elif cmd == "/clips":           await cmd_clips(client)
    elif cmd == "/guest":           await cmd_guest(client)
    elif cmd == "/guests":          await cmd_guest(client)
    elif text:                      await cmd_unknown(client, text)


# ── Long-poll loop ────────────────────────────────────────────────────────────

async def poll():
    if not BOT_TOKEN or not CHAT_ID:
        print("❌  TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in .env")
        sys.exit(1)

    print("🤖  PodclickBot started — polling for commands…")
    print(f"    Chat ID: {CHAT_ID}  |  Studio: {STUDIO_URL}")

    offset = 0
    async with httpx.AsyncClient(timeout=TIMEOUT_SECS + 5) as client:
        await send(client, "🟢 *PodclickBot online* — type /help for commands.")

        while True:
            try:
                data = await tg_get(
                    client, "getUpdates",
                    offset=offset,
                    timeout=TIMEOUT_SECS,
                    allowed_updates=["message"],
                )
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg    = update.get("message")
                    if msg:
                        await handle(client, msg)
            except httpx.ReadTimeout:
                pass
            except Exception as exc:
                print(f"⚠️  Poll error: {exc}")
                await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(poll())
