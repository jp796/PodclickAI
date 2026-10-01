"""The Crew roster — all 12 agent specs from AGENTS_HUB_SPEC §1. Pure data.

No imports of main or brick_agent: brick_agent imports THIS module to register
`agent_run:{id}` tier gates, so anything heavier would create a cycle.

Runner modules are built by other lanes. A spec whose runner file is missing is
simply `not_built` (see jobs.load_runner) — lanes can merge in any order.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Dict, List, Optional

from services.agents.contract import AgentSpec, CATEGORY_IDS, FieldSpec

AUDIENCES = ("Relocation Buyers", "Home Sellers", "First-Time Buyers", "Investors", "Luxury")
PLATFORMS = ("linkedin", "facebook", "instagram", "tiktok", "youtube", "x")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

_R = "services.agents.runners."

_SPECS: List[AgentSpec] = [
    # ── Research ──────────────────────────────────────────────────────────────
    AgentSpec(
        id="market_scout",
        name="Market Scout",
        category="research",
        job="Find which local real-estate videos are beating their channel's size, and why.",
        icon="🔭",
        runner=_R + "market_scout",
        fields=(
            FieldSpec("city", "City", "text", required=True, max_len=120,
                      help="The market to scout, e.g. Springfield, MO."),
            FieldSpec("audience", "Audience", "select", default="Relocation Buyers",
                      options=AUDIENCES),
            FieldSpec("channels", "Competitor channels", "text", max_len=500,
                      help="Optional handles, comma separated."),
            FieldSpec("remix_top", "Rewrite the top concepts in my voice", "number",
                      default=0, min_value=0, max_value=3),
        ),
        run_tier="draftsman",
        spend_tier="bricklayer",
        foundation="optional",          # remix only
        requires=("youtube_api",),
        spends=("YouTube quota",),
        wraps=("/api/yt/competitor-spy", "/api/yt/scout-remix"),
    ),
    AgentSpec(
        id="trend_radar",
        name="Trend Radar",
        category="research",
        job="Pick 8 local video topics that are about to peak, mapped to your pillars.",
        icon="📡",
        runner=_R + "trend_radar",
        fields=(
            FieldSpec("city", "City", "text", required=True, max_len=120),
            FieldSpec("audience", "Audience", "select", default="Relocation Buyers",
                      options=AUDIENCES),
            FieldSpec("scout_job", "Use a Market Scout report", "job_ref",
                      job_ref_agents=("market_scout",)),
        ),
        foundation="required",
        wraps=("/api/yt/content-calendar",),
    ),
    # ── Plan ──────────────────────────────────────────────────────────────────
    AgentSpec(
        id="pillar_planner",
        name="Pillar Planner",
        category="plan",
        job="Lay out a 90-day video plan across your content pillars.",
        icon="📐",
        runner=_R + "pillar_planner",
        fields=(
            FieldSpec("market", "Market", "text", required=True, max_len=120),
            FieldSpec("agent_name", "Your name", "text", max_len=120,
                      help="Defaults to the name on your Blueprint."),
            FieldSpec("months_in_market", "Time in this market", "select",
                      options=("Just Starting", "6–12 months", "1–3 years", "3+ years")),
        ),
        foundation="required",
        wraps=("/api/yt/pillar-plan",),
    ),
    # ── Create ────────────────────────────────────────────────────────────────
    AgentSpec(
        id="click_studio",
        name="Click Studio",
        category="create",
        job="Take a recorded project from raw take to a finished, review-ready episode.",
        icon="🎬",
        runner=_R + "click_studio",
        fields=(
            FieldSpec("project_id", "Project", "project", required=True),
            FieldSpec("auto_edit", "Auto-edit the take", "checkbox", default=True),
            FieldSpec("ship_it", "Ship It", "checkbox", default=True),
            FieldSpec("broll", "Add b-roll", "checkbox", default=False),
            FieldSpec("force_reedit", "Re-edit even if it's already cut", "checkbox",
                      default=False),
        ),
        run_tier="bricklayer",
        spend_tier="bricklayer",
        foundation="inherited",
        requires=("openai",),
        spends=("OpenAI transcription", "Pexels stock footage (b-roll only)"),
        wraps=("/api/projects/{id}/auto-edit", "/api/projects/{id}/ship-it",
               "/api/projects/{id}/auto-broll"),
    ),
    AgentSpec(
        id="draftsman",
        name="Draftsman",
        category="create",
        job="Write platform-ready social posts in your voice from an idea, an episode, or a template.",
        icon="✍️",
        runner=_R + "draftsman",
        fields=(
            FieldSpec("mode", "Start from", "select", default="idea",
                      options=("idea", "episode", "template")),
            FieldSpec("topic", "Topic", "text", max_len=300),
            FieldSpec("project_id", "Episode", "project"),
            FieldSpec("template", "Template", "select",
                      options=("Just Listed", "Market Update", "Client Win", "Hot Take",
                               "Tip of the Week")),
            FieldSpec("market", "Market", "text", max_len=120),
        ),
        commit_tier="bricklayer",
        foundation="required",
        wraps=("/api/social/forge",),
    ),
    AgentSpec(
        id="thumbnail",
        name="Painter",
        category="create",
        job="Make click-worthy thumbnails or episode posters with your face on them.",
        icon="🎨",
        runner=_R + "thumbnail",
        fields=(
            FieldSpec("format", "Format", "select", default="youtube_thumbnail",
                      options=("youtube_thumbnail", "episode_poster")),
            FieldSpec("title", "Title", "text", required=True, max_len=150),
            FieldSpec("project_id", "Episode (posters)", "project"),
            FieldSpec("persona_photo", "Your photo", "persona"),
            FieldSpec("reference_url", "Reference thumbnail", "text", max_len=500),
        ),
        spend_tier="bricklayer",
        foundation="inherited",
        spends=("image generation (only if an image provider is connected)",),
        wraps=("/api/yt/cover-forge",),
    ),
    AgentSpec(
        id="voiceover",
        name="Voiceover",
        category="create",
        job="Turn your script into a clean voice track in your cloned voice.",
        icon="🎙️",
        runner=_R + "voiceover",
        fields=(
            FieldSpec("script", "Script", "textarea", required=True, max_len=5000,
                      help="Spoken word for word."),
            FieldSpec("voice", "Voice", "select"),
            FieldSpec("purpose", "Purpose", "select", default="narration",
                      options=("intro", "outro", "ad read", "narration")),
            FieldSpec("with_timestamps", "Word timings for captions", "checkbox",
                      default=True),
        ),
        spend_tier="bricklayer",
        foundation="none",
        requires=("elevenlabs",),
        spends=("ElevenLabs characters",),
        wraps=("media:tts",),
    ),
    AgentSpec(
        id="avatar_video",
        name="Avatar Video",
        category="create",
        job="Produce a vertical talking-head video of you delivering a script, without filming.",
        icon="🧑‍💼",
        runner=_R + "avatar_video",
        fields=(
            FieldSpec("script", "Script", "textarea", max_len=5000),
            FieldSpec("topic", "Topic (I'll draft the script)", "text", max_len=300),
            FieldSpec("persona_photo", "Your photo", "persona", required=True),
            FieldSpec("length", "Length (seconds)", "select", default="45",
                      options=("30", "45", "60")),
            FieldSpec("captions", "Burn in captions", "checkbox", default=True),
        ),
        run_tier="bricklayer",
        spend_tier="bricklayer",
        foundation="optional",          # required only when it drafts the script
        requires=("elevenlabs", "higgsfield"),
        spends=("ElevenLabs characters", "Higgsfield credits"),
        wraps=("media:tts", "media:avatar_video"),
        require_one_of=(("script", "topic"),),
    ),
    AgentSpec(
        id="home_tour",
        name="Home Tour Video",
        category="create",
        job="Turn a listing's photos into a vertical tour video with voiceover and captions.",
        icon="🏡",
        runner=_R + "home_tour",
        fields=(
            FieldSpec("photos", "Listing photos", "files", required=True,
                      min_value=4, max_value=25),
            FieldSpec("address", "Address", "text", max_len=200),
            FieldSpec("price", "Price", "text", max_len=40),
            FieldSpec("beds", "Beds", "number", min_value=0, max_value=50),
            FieldSpec("baths", "Baths", "number", min_value=0, max_value=50),
            FieldSpec("sqft", "Square feet", "number", min_value=0, max_value=100000),
            FieldSpec("highlights", "Highlights", "textarea", max_len=1500),
            FieldSpec("length", "Length (seconds)", "select", default="30",
                      options=("30", "60")),
            FieldSpec("voice", "Voiceover", "select", default="on", options=("on", "off")),
        ),
        spend_tier="bricklayer",
        foundation="required",
        spends=("Higgsfield credits (if connected)", "ElevenLabs characters (if connected)"),
        wraps=("media:image_to_video", "media:tts", "media:ken_burns"),
    ),
    AgentSpec(
        id="market_reel",
        name="Market Update Reel",
        category="create",
        job="Produce a 30–90 second vertical neighborhood or market-update reel in your voice.",
        icon="📈",
        runner=_R + "market_reel",
        fields=(
            FieldSpec("area", "City or neighborhood", "text", required=True, max_len=120),
            FieldSpec("angle", "Angle", "text", max_len=300),
            FieldSpec("numbers", "Your numbers", "textarea", max_len=1500,
                      help="Only stats you supply are spoken. Leave blank for trends only."),
            FieldSpec("length", "Length (seconds)", "select", default="60",
                      options=("30", "60", "90")),
        ),
        spend_tier="bricklayer",
        foundation="required",
        requires=("pexels",),
        spends=("ElevenLabs characters (if connected)",),
        wraps=("pipeline/broll.py", "media:tts"),
    ),
    # ── Publish ───────────────────────────────────────────────────────────────
    AgentSpec(
        id="content_scheduler",
        name="Content Scheduler",
        category="publish",
        job="Place topics onto real dates on your calendar and shoot schedule.",
        icon="🗓️",
        runner=_R + "content_scheduler",
        fields=(
            FieldSpec("topics_job", "Topics from a work order", "job_ref",
                      job_ref_agents=("trend_radar", "pillar_planner")),
            FieldSpec("topics", "Or type topics, one per line", "textarea", max_len=5000),
            FieldSpec("start_date", "Start date", "date",
                      help="Defaults to tomorrow."),
            FieldSpec("cadence", "Cadence", "select", default="3/week",
                      options=("3/week", "5/week", "daily")),
            FieldSpec("shoot_days", "Shoot days", "multiselect", options=WEEKDAYS,
                      help="Defaults to your saved shoot days."),
            FieldSpec("platforms", "Platforms", "multiselect", options=PLATFORMS),
        ),
        commit_tier="bricklayer",
        foundation="none",
        wraps=("/api/yt/scheduler", "/api/yt/scheduler/save", "/calendar"),
        require_one_of=(("topics_job", "topics"),),
    ),
    AgentSpec(
        id="repurpose",
        name="Clip Dispatcher",
        category="publish",
        job="Turn an episode's best moments into Instagram and TikTok drafts.",
        icon="📲",
        runner=_R + "repurpose",
        fields=(
            FieldSpec("project_id", "Project", "project", required=True),
            FieldSpec("max_clips", "How many clips", "number", default=3,
                      min_value=1, max_value=12),
            FieldSpec("platforms", "Platforms", "multiselect",
                      default=["instagram", "tiktok"], options=("instagram", "tiktok"),
                      min_value=1),
        ),
        commit_tier="bricklayer",
        foundation="inherited",
        requires=("ghl",),
        wraps=("/api/projects/{id}/distribute-shorts",),
    ),
]

REGISTRY: "OrderedDict[str, AgentSpec]" = OrderedDict((s.id, s) for s in _SPECS)


def get_agent(agent_id: str) -> Optional[AgentSpec]:
    return REGISTRY.get(agent_id)


def agents_in_category(category: str) -> List[AgentSpec]:
    return [s for s in REGISTRY.values() if s.category == category]


_TIER_RANK: Dict[str, int] = {
    "owner_builder": 0, "draftsman": 1, "bricklayer": 2, "foreman": 3, "gc": 4,
}


def agent_run_tier(spec: AgentSpec) -> str:
    """
    The tier Brick needs to START this agent on his own (§2.3): the higher of
    run_tier and spend_tier. Kept here (not in brick_agent) so the registry can be
    checked without importing the Brick service.
    """
    if spec.spend_tier and _TIER_RANK.get(spec.spend_tier, 99) > _TIER_RANK.get(spec.run_tier, 99):
        return spec.spend_tier
    return spec.run_tier


assert all(s.category in CATEGORY_IDS for s in _SPECS), "registry category typo"
