#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Check Python ───────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo "Error: python3 not found. Install Python 3.10+ first."
  exit 1
fi

# ── Create .env if missing ─────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo ""
  echo "⚠️  Created .env from .env.example"
  echo "   Please open .env and add your OPENAI_API_KEY, then run this script again."
  echo ""
  exit 1
fi

# ── Virtual environment ────────────────────────────────────────────────────────
if [ ! -d "venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv venv
fi

source venv/bin/activate

# ── Install dependencies ───────────────────────────────────────────────────────
echo "Checking dependencies..."
pip install -q -r requirements.txt

# ── Check ffmpeg ───────────────────────────────────────────────────────────────
if ! command -v ffmpeg &>/dev/null; then
  echo ""
  echo "⚠️  ffmpeg not found. Install it with:"
  echo "   brew install ffmpeg"
  echo ""
  exit 1
fi

# ── Cleanup on exit ───────────────────────────────────────────────────────────
BOT_PID=""
cleanup() {
  echo ""
  echo "Shutting down..."
  if [ -n "$BOT_PID" ] && kill -0 "$BOT_PID" 2>/dev/null; then
    kill "$BOT_PID"
  fi
}
trap cleanup EXIT INT TERM

# ── Launch PodclickBot in background ──────────────────────────────────────────
echo ""
echo "🤖 Starting PodclickBot (Telegram)..."
"$SCRIPT_DIR/venv/bin/python3" -u "$SCRIPT_DIR/bot.py" >> "$SCRIPT_DIR/bot.log" 2>&1 &
BOT_PID=$!

# ── Open browser ──────────────────────────────────────────────────────────────
echo "🎙️ Starting Podcast Studio..."
echo "   Opening http://localhost:8765"
echo ""
(sleep 1.5 && open "http://localhost:8765") &

# ── Launch studio server ──────────────────────────────────────────────────────
python3 -m uvicorn main:app --host 0.0.0.0 --port 8765 --reload
