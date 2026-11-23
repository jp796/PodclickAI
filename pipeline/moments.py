"""
moments.py — pick clip-worthy moments from an episode transcript.

Replaces fixed 60-second windows with self-contained moments that start where
an idea starts and end on its payoff, anywhere from 20 seconds to 3 minutes.

  1. Split Whisper words into sentences (punctuation or a pause).
  2. Ask Claude to pick the moments, by sentence number, as a deliberate mix of
     lengths: quick hits (20-45s), standard (45-90s), deep cuts (90-180s).
  3. Validate in code: snap to sentence edges, enforce lengths, drop overlaps,
     trim leading filler, pad the edges so no word is clipped.
  4. If the AI call fails, fall back to the old scorer with its windows snapped
     to sentence boundaries — clips are always produced.

Each candidate keeps the fields the rest of Ship It already reads
(start, end, duration, transcript, score, hook_text, index) plus
title, why, kind and fits (the platforms whose length limits it fits).
hook_text stays the verbatim first sentence — no AI-written text is ever
attributed to the speaker here.
"""

import json
import os
import re
from typing import Dict, List, Optional

MIN_SECS = 20.0
MAX_SECS = 180.0
KINDS = {
    "quick":    (20.0, 45.0),
    "standard": (45.0, 90.0),
    "deep":     (90.0, 180.0),
}
# Leading words that make a clip open mid-thought.
_LEAD_FILLER = {"um", "uh", "so", "and", "but", "like", "yeah", "okay", "ok", "right",
                "well", "anyway", "you know", "i mean"}
# Platform length limits (seconds) used to tag each clip.
PLATFORM_MAX = {
    "tiktok": 600,
    "youtube_shorts": 180,
    "instagram_reels": 180,
    "facebook_reels": 10_000,     # Meta dropped the Reels cap in 2025
}
DEFAULT_MODEL = os.getenv("PODCLICK_CLIP_MODEL", "claude-sonnet-5-5")
FALLBACK_MODEL = "claude-sonnet-4-5"


# ── Sentences ────────────────────────────────────────────────────────────────

def build_sentences(words: List[dict], pause: float = 0.7) -> List[dict]:
    """Group words into sentences on . ! ? or a pause longer than `pause` seconds."""
    sentences: List[dict] = []
    cur: List[dict] = []
    for i, w in enumerate(words):
        if not (w.get("word") or "").strip():
            continue
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        ends = (w.get("word") or "").strip()[-1:] in (".", "!", "?")
        gap = (nxt["start"] - w["end"]) if nxt else 999
        if ends or gap > pause or len(cur) >= 60:
            sentences.append(_sentence(cur))
            cur = []
    if cur:
        sentences.append(_sentence(cur))
    for i, s in enumerate(sentences):
        s["i"] = i
    return sentences


def _sentence(ws: List[dict]) -> dict:
    return {
        "start": float(ws[0]["start"]),
        "end": float(ws[-1]["end"]),
        "text": " ".join((w.get("word") or "").strip() for w in ws).strip(),
        "words": ws,
    }


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


# ── AI selection ─────────────────────────────────────────────────────────────

_PROMPT = """You are a short-form video editor cutting clips from a podcast episode for TikTok, YouTube Shorts, Instagram Reels and Facebook.

Below is the full transcript, one sentence per line: [sentence number] (timestamp) text.

Pick the {n} best moments to post as standalone vertical clips. A great moment:
- Opens with a hook: a bold claim, a question, a surprising number, or the start of a story. Never opens mid-thought ("and so...", "like I said...", "that's why...").
- Makes one complete point or tells one complete story, understandable with zero context from the rest of the episode.
- Ends on the payoff: the punchline, the lesson, the answer. Not mid-sentence and not trailing into the next topic.
- Is something a viewer would stop scrolling for, share, or save.

Use a deliberate mix of lengths, letting the content decide which fits:
- "quick": 20-45 seconds — one sharp insight or punchline
- "standard": 45-90 seconds — a full point with an example
- "deep": 90-180 seconds — a complete story or step-by-step breakdown that earns the time
Aim for roughly a third of each, but never stretch or pad a moment to hit a length.

Moments must not overlap. Rank them best first.

Return ONLY a JSON array, no other text. Each item:
{{"start": <first sentence number>, "end": <last sentence number, inclusive>, "kind": "quick"|"standard"|"deep", "title": "<5-8 word internal label>", "why": "<one sentence on why it works>", "score": <1-10>}}

TRANSCRIPT:
{transcript}"""


def _parse_picks(text: str) -> List[dict]:
    """JSON array from the reply; if it was cut off, keep every complete object."""
    m = re.search(r"\[.*\]", text, re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return [d for d in data if isinstance(d, dict)]
        except ValueError:
            pass
    out = []
    for obj in re.findall(r"\{[^{}]*\}", text):
        try:
            d = json.loads(obj)
            if isinstance(d, dict) and "start" in d and "end" in d:
                out.append(d)
        except ValueError:
            continue
    return out


def _ai_pick(sentences: List[dict], n: int) -> List[dict]:
    """Ask Claude for moments. Returns raw picks (sentence indexes). Raises on failure."""
    import anthropic
    from config import settings

    lines = "\n".join(f"[{s['i']}] ({_mmss(s['start'])}) {s['text']}" for s in sentences)
    prompt = _PROMPT.format(n=n, transcript=lines)
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"))

    last_err: Optional[Exception] = None
    for model in dict.fromkeys([DEFAULT_MODEL, FALLBACK_MODEL]):
        try:
            resp = client.messages.create(
                # Large budget: newer models think before answering, and that
                # counts against max_tokens.
                model=model, max_tokens=16000,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(getattr(b, "text", "") for b in resp.content)
            picks = _parse_picks(text)
            if not picks:
                raise ValueError(f"no moments in model reply (stop={resp.stop_reason})")
            print(f"[moments] {model} picked {len(picks)} moments")
            return picks
        except anthropic.NotFoundError as e:      # model id not available → try fallback
            last_err = e
            continue
    raise RuntimeError(f"clip picker AI call failed: {last_err}")


# ── Validation / edge cleanup ────────────────────────────────────────────────

def _fit_length(si: int, ei: int, sentences: List[dict]) -> Optional[tuple]:
    """Grow or shrink a sentence range until it is MIN_SECS..MAX_SECS long."""
    n = len(sentences)
    si, ei = max(0, min(si, n - 1)), max(0, min(ei, n - 1))
    if ei < si:
        si, ei = ei, si

    def dur():
        return sentences[ei]["end"] - sentences[si]["start"]

    while dur() > MAX_SECS and ei > si:
        ei -= 1
    while dur() < MIN_SECS and ei + 1 < n:
        ei += 1
    if not (MIN_SECS <= dur() <= MAX_SECS):
        return None
    return si, ei


def _norm(w: dict) -> str:
    return re.sub(r"[^\w' ]", "", (w.get("word") or "").lower()).strip()


def _trim_lead(words: List[dict]) -> List[dict]:
    """Drop leading filler ('so', 'um', 'and') so the clip opens on the point."""
    i = 0
    while i < len(words) - 3 and _norm(words[i]) in _LEAD_FILLER | {"then"}:
        i += 1
    return words[i:]


# Transitions that announce the NEXT topic — a clip must not end on them.
_TAIL_PHRASES = [
    ("with", "that", "being", "said"), ("that", "being", "said"), ("having", "said", "that"),
    ("so", "here's", "the", "other", "thing"), ("here's", "the", "other", "thing"),
    ("here's", "the", "thing"), ("and", "then"), ("you", "know"), ("i", "mean"),
]
_TAIL_FILLER = {"and", "so", "but", "um", "uh", "like", "or", "because", "the", "a", "that"}


def _trim_tail(words: List[dict]) -> List[dict]:
    """Drop a dangling transition or filler so the clip ends on the payoff."""
    changed = True
    while changed and len(words) > 8:
        changed = False
        for ph in _TAIL_PHRASES:
            k = len(ph)
            if tuple(_norm(w) for w in words[-k:]) == ph:
                words, changed = words[:-k], True
                break
        while len(words) > 8 and _norm(words[-1]) in _TAIL_FILLER:
            words, changed = words[:-1], True
    return words


def _to_candidate(si: int, ei: int, sentences: List[dict], all_words: List[dict],
                  meta: Dict) -> dict:
    ws = [w for s in sentences[si:ei + 1] for w in s["words"]]
    ws = _trim_tail(_trim_lead(ws))
    first, last = ws[0], ws[-1]

    # Pad edges so the first/last word isn't clipped, without bleeding into
    # the neighbouring words.
    prev_end = max((w["end"] for w in all_words if w["end"] <= first["start"]), default=0.0)
    next_start = min((w["start"] for w in all_words if w["start"] >= last["end"]), default=last["end"] + 1.0)
    start = max(prev_end, first["start"] - 0.15, 0.0)
    end = min(next_start - 0.05, last["end"] + 0.4)
    end = max(end, last["end"])

    transcript = " ".join((w.get("word") or "").strip() for w in ws)
    hook = sentences[si]["text"] if ws[0] is sentences[si]["words"][0] else \
        " ".join((w.get("word") or "").strip() for w in ws[:20])
    if len(hook) > 120:
        hook = " ".join(hook.split()[:12]) + "..."
    duration = end - start
    return {
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(duration, 2),
        "transcript": transcript,
        "word_count": len(ws),
        "score": float(meta.get("score") or 5.0),
        "hook_text": hook.strip(),
        "title": (meta.get("title") or "").strip(),
        "why": (meta.get("why") or "").strip(),
        "kind": _kind_for(duration),
        "fits": [p for p, cap in PLATFORM_MAX.items() if duration <= cap],
    }


def _kind_for(duration: float) -> str:
    if duration < 45:
        return "quick"
    if duration < 90:
        return "standard"
    return "deep"


def _dedupe(cands: List[dict], limit: int) -> List[dict]:
    chosen: List[dict] = []
    for c in cands:
        if any(not (c["end"] <= k["start"] or c["start"] >= k["end"]) for k in chosen):
            continue
        chosen.append(c)
        if len(chosen) >= limit:
            break
    return chosen


# ── Fallback: old scorer, snapped to sentences ───────────────────────────────

def _fallback(words: List[dict], segments: List[dict], sentences: List[dict], n: int) -> List[dict]:
    from pipeline.clip import score_and_select_clips
    raw = score_and_select_clips(segments=segments, words=words, num_clips=n,
                                 min_dur=MIN_SECS, max_dur=90.0)
    out = []
    for r in raw:
        si = min(range(len(sentences)), key=lambda i: abs(sentences[i]["start"] - r["start"]))
        ei = min(range(len(sentences)), key=lambda i: abs(sentences[i]["end"] - r["end"]))
        fit = _fit_length(si, ei, sentences)
        if fit:
            out.append(_to_candidate(fit[0], fit[1], sentences, words,
                                     {"score": round(r.get("score", 0.5) * 10, 1)}))
    return out


# ── Public entry point ───────────────────────────────────────────────────────

def pick_moments(words: List[dict], segments: List[dict], num_clips: int = 12) -> List[dict]:
    """Return up to num_clips non-overlapping clip candidates in time order."""
    words = [w for w in (words or []) if (w.get("word") or "").strip()]
    if not words:
        return []
    sentences = build_sentences(words)

    cands: List[dict] = []
    try:
        picks = _ai_pick(sentences, num_clips)
        for p in picks:
            try:
                fit = _fit_length(int(p["start"]), int(p["end"]), sentences)
            except (KeyError, TypeError, ValueError):
                continue
            if fit:
                cands.append(_to_candidate(fit[0], fit[1], sentences, words, p))
    except Exception as e:
        print(f"[moments] AI picker unavailable ({e}) — using sentence-snapped scorer")

    if not cands:
        cands = _fallback(words, segments, sentences, num_clips)
        cands.sort(key=lambda c: -c["score"])

    chosen = _dedupe(cands, num_clips)      # AI order = rank, so best survive overlaps
    chosen.sort(key=lambda c: c["start"])
    for i, c in enumerate(chosen):
        c["index"] = i + 1
    return chosen
