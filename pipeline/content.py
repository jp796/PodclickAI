"""
Generate episode title and description from transcript using OpenAI GPT-4o.
- Extracts URLs/links mentioned in the podcast (spoken or written)
- Appends a configurable show footer to every episode (falls back to DEFAULT_FOOTER)
- Supports per-show profile: patreon_url, merch_url, apple_podcasts_url, footer_template
"""

import os
import re
from openai import OpenAI

# ── Default footer — used when no profile or no footer_template is set ────────
DEFAULT_FOOTER = """\
---
📚 Book: "The Real Estate Brain" by JP Fluellen
🐼 Panda Dash CRM (Lifetime Access): https://get.pandadash.io/panda4life

ABOUT SUCCESS AGENT PODCAST:
The Success Agent Podcast helps real estate professionals build better businesses through proven strategies, expert interviews, and actionable advice.
HOST: JP
🌐 Website: https://api.meetpaddy.co/widget/bookings/successagent
Want to be a guest? Visit https://api.meetpaddy.co/widget/bookings/successagent"""

# Keep old name as alias for backwards compatibility
EPISODE_FOOTER = DEFAULT_FOOTER

# ── URL regex (catches https://, http://, www.) ────────────────────────────────
_URL_RE = re.compile(
    r'https?://[^\s<>"{}|\\^`\[\]\']+|www\.[a-zA-Z0-9\-]+\.[a-zA-Z]{2,}[^\s<>"{}|\\^`\[\]\']*'
)


def _get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not set in .env file")
    return OpenAI(api_key=api_key)


def extract_links(transcript: str, client: OpenAI) -> list[str]:
    """
    Extract all links / resources mentioned in the podcast.

    Two-pass approach:
      1. Regex: find any literal URLs in the Whisper transcript.
      2. GPT: find verbally mentioned resources (e.g. "check out my website at ...")
         that Whisper may have transcribed without a proper URL format.

    Returns a deduplicated list of strings (URLs or descriptions of resources).
    """
    found: list[str] = []

    # Pass 1 — regex scan
    regex_hits = _URL_RE.findall(transcript)
    # Clean up trailing punctuation that Whisper sometimes attaches
    for url in regex_hits:
        url = url.rstrip(".,!?;:)")
        if url not in found:
            found.append(url)

    # Pass 2 — GPT extraction for verbally mentioned resources
    prompt = f"""You are a podcast editor. Carefully read the transcript below and extract every website, URL, book, tool, app, social media handle, or online resource that is explicitly mentioned or recommended.

Rules:
- If a full URL was spoken, include it exactly.
- If only a website name was mentioned (e.g. "go to PandaDash dot io"), reconstruct the most likely URL.
- If a book, course, or tool is mentioned without a URL, include it as: "Book: Title by Author" or "Tool: Name".
- Do NOT include the show's own recurring links (pandadash.io, successagent, meetpaddy).
- Return one item per line, no bullet points, no numbering, no extra explanation.
- If nothing was mentioned, return exactly: NONE

Transcript:
{transcript[:5000]}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.2,
        )
        gpt_text = response.choices[0].message.content.strip()
        if gpt_text.upper() != "NONE":
            for line in gpt_text.splitlines():
                line = line.strip().strip("-•").strip()
                if line and line not in found:
                    found.append(line)
    except Exception:
        pass  # GPT extraction is best-effort

    return found


def generate_title(transcript: str, podcast_name: str = "", client: OpenAI = None) -> str:
    if client is None:
        client = _get_client()
    podcast_context = f' for the podcast "{podcast_name}"' if podcast_name else ""

    prompt = f"""You are a podcast producer and SEO expert. Based on the transcript below, generate a single compelling episode title{podcast_context}.

Rules:
- 5–10 words, punchy and specific
- Optimize for podcast search discovery: include the guest's name OR the core topic keyword people would search for
- If there is a guest, lead with what they teach or their bold claim, not just their name
- If no guest, lead with the core insight or actionable takeaway
- No clickbait, no ALL CAPS, no excessive punctuation
- Do NOT include "Episode X" or any episode number in the title
- Format: "[Topic/Insight]: [Guest Name]" OR "[Compelling question or claim]"
- Return ONLY the title, nothing else

Transcript:
{transcript[:4000]}"""

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=60,
        temperature=0.7,
    )
    return response.choices[0].message.content.strip().strip('"').strip("'")


def generate_description_body(
    transcript: str,
    podcast_name: str = "",
    client: OpenAI = None,
) -> str:
    """Generate the main body of the description (without links section or footer)."""
    if client is None:
        client = _get_client()
    podcast_context = f' for the podcast "{podcast_name}"' if podcast_name else ""

    prompt = f"""You are a podcast producer. Based on the transcript below, write the main body of an episode description{podcast_context}.

Rules:
- 2–3 short paragraphs
- First sentence MUST name the guest (if there is one) AND their key credential or most impressive fact about them (e.g. "In this episode, JP sits down with [Guest Name], [credential — e.g. top 1% realtor in Texas, author of X, founder of Y]...")
- If there is no guest, open with the strongest hook or insight from the episode
- Second paragraph: 3–5 key topics or takeaways as a short bulleted list (use • bullets), starting each bullet with a strong action verb or insight
- Third paragraph: 1 punchy sentence inviting listeners to subscribe and share if they got value
- Friendly, direct, conversational tone — write like a person, not a press release
- Do NOT include any links, URLs, or "links mentioned" section — those are added separately
- Do NOT make up facts not in the transcript
- Return only the description body, no extra commentary

Transcript:
{transcript[:6000]}"""

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=550,
        temperature=0.6,
    )
    return response.choices[0].message.content.strip()


def _fmt_time(seconds: float) -> str:
    """Format seconds as MM:SS or H:MM:SS."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def generate_timestamps(segments: list[dict], client: OpenAI = None) -> str:
    """
    Use GPT to identify major topic shifts in the segment list and return
    a formatted Chapters block like:
        ⏱ Chapters:
        0:00 - Introduction
        4:30 - Topic One
        ...
    """
    if not segments:
        return ""

    if client is None:
        client = _get_client()

    # Build a compact segment summary: "0:00 | first 120 chars of text"
    # Sample at most 80 segments so the prompt stays manageable
    step     = max(1, len(segments) // 80)
    sampled  = segments[::step]
    seg_lines = "\n".join(
        f"{_fmt_time(s['start'])} | {s['text'][:120]}"
        for s in sampled
    )

    prompt = f"""You are a podcast editor creating YouTube-style chapter timestamps.

Below is a podcast transcript broken into timed segments (MM:SS | text).
Identify 6–12 major topic shifts and write a chapters list.

Rules:
- First chapter MUST start at 0:00 and be called "Introduction" (or similar)
- Use the exact timestamp of the segment where the topic starts (copy from the list)
- Chapter titles: 2–5 words, concise and descriptive
- Do NOT make up timestamps — only use ones from the list below
- Return ONLY the chapters, one per line, format: M:SS - Title
- No extra text, no numbering, no bullet points

Segments:
{seg_lines}"""

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=400,
        temperature=0.3,
    )
    raw = response.choices[0].message.content.strip()
    return "⏱ Chapters:\n" + raw


def build_full_description(
    body: str,
    links: list[str],
    timestamps: str = "",
    profile: dict = None,
    sponsor: dict = None,
) -> str:
    """
    Combine description body + timestamps + links section + footer + monetisation CTAs.

    Final structure:
      <body>

      ⏱ Chapters:          (if timestamps generated)
      0:00 - Introduction

      🔗 Links Mentioned:  (if links found)
      • link1

      ---
      <footer_template or DEFAULT_FOOTER>

      ⭐ Ratings CTA       (always — Apple Podcasts URL if configured, else generic)

      ❤️ Patreon CTA       (only if profile.patreon_url is set)

      🛒 Merch link        (only if profile.merch_url is set)
    """
    p = profile or {}
    parts = [body]

    # Sponsor block — injected right after the body (ISC-53)
    if sponsor and sponsor.get("company"):
        company = sponsor["company"]
        website = sponsor.get("affiliate_url") or sponsor.get("website") or ""
        sponsor_block = f"💼 This episode is brought to you by {company}."
        if website:
            sponsor_block += f"\n👉 {website}"
        parts.append(sponsor_block)

    if timestamps:
        parts.append(timestamps)

    if links:
        links_section = "🔗 Links Mentioned:\n" + "\n".join(f"• {link}" for link in links)
        parts.append(links_section)

    # Footer: profile template overrides hardcoded default
    footer = p.get("footer_template") or DEFAULT_FOOTER
    parts.append(footer)

    # Social proof / ratings CTA (ISC-35, ISC-37)
    apple_url = p.get("apple_podcasts_url", "").strip()
    if apple_url:
        parts.append(
            f"⭐ Enjoying the show? Leave us a rating & review on Apple Podcasts — it helps more people find us!\n{apple_url}"
        )
    else:
        parts.append(
            "⭐ Enjoying the show? Leave us a rating & review on Apple Podcasts — it helps more people find us!"
        )

    # Patreon CTA (ISC-19, ISC-28)
    patreon_url = p.get("patreon_url", "").strip()
    if patreon_url:
        patreon_cta = p.get("patreon_cta", "").strip() or (
            f"❤️ Love the show? Support us on Patreon for bonus episodes, behind-the-scenes content, and more:\n{patreon_url}"
        )
        parts.append(patreon_cta)

    # Merch / swag store (ISC-20, ISC-30, ISC-31)
    merch_url = p.get("merch_url", "").strip()
    if merch_url:
        merch_label = p.get("merch_label", "").strip() or "🛒 Grab your gear →"
        parts.append(f"{merch_label}\n{merch_url}")

    return "\n\n".join(parts)


def generate_content(
    transcript: str,
    podcast_name: str = "",
    segments: list = None,
    progress_cb=None,
    profile: dict = None,
    sponsor: dict = None,
) -> dict:
    """
    Generate title, extract links, generate timestamps, build full description.
    profile: optional show-profile dict with footer_template, patreon_url, merch_url, etc.
    Returns: {"title": str, "description": str, "links": list[str]}
    """
    client = _get_client()

    if progress_cb:
        progress_cb("Extracting links mentioned in the podcast...")
    links = extract_links(transcript, client)
    if progress_cb and links:
        progress_cb(f"Found {len(links)} link(s) mentioned: {', '.join(links[:3])}{'...' if len(links) > 3 else ''}")

    if progress_cb:
        progress_cb("Generating episode title with GPT-4o...")
    title = generate_title(transcript, podcast_name, client)

    if progress_cb:
        progress_cb("Generating episode description with GPT-4o...")
    body = generate_description_body(transcript, podcast_name, client)

    timestamps = ""
    if segments:
        if progress_cb:
            progress_cb("Generating chapter timestamps…")
        try:
            timestamps = generate_timestamps(segments, client)
        except Exception:
            timestamps = ""

    description = build_full_description(body, links, timestamps, profile=profile, sponsor=sponsor)

    if progress_cb:
        progress_cb(f'Content ready: "{title}"')

    return {"title": title, "description": description, "links": links}
