"""Design system v2 contract: icon bundle, emoji safety net, nav wiring, CSS tokens, contrast.

The icon bundle (frontend/static/pc-icons.js) is generated from Lucide; these tests pin the
properties pages rely on so a regeneration or hand edit cannot silently break them.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICONS_JS = ROOT / "frontend" / "static" / "pc-icons.js"
NAV_JS = ROOT / "frontend" / "static" / "podclick-nav.js"
CSS = ROOT / "frontend" / "podclick-design.css"
DOC = ROOT / "docs" / "DESIGN_SYSTEM.md"

# Emoji the brief explicitly requires the safety net to cover.
REQUIRED_EMOJI = list(
    "✓✅✗❌✕⚠🎬🎙🎤✦✨📅📋⚡📝🔗✂🔒🔄📱📁📂🎵📚📦💼💾📧🔍🚀🤖🏷📷📸👁🌐📹🗑📢✍"
    "▶⏸⬇⬆→←↻🎯🔥💡⭐📊📈🏠⚙🔔👤👥"
)
# Containers whose text must never be rewritten (user input, AI-written copy).
REQUIRED_SKIP = ["'textarea'", "'input'", "'pre'", "'code'", "'script'", "'style'",
                 "[contenteditable]", "'[data-keep-emoji]'", "'[data-ai-output]'",
                 "'.output'", "'.result-body'"]


def _src():
    return ICONS_JS.read_text(encoding="utf-8")


def _json_var(src, name):
    m = re.search(r"var " + name + r" = (\{.*?\});\n", src, re.S)
    assert m, "could not find var %s in pc-icons.js" % name
    return json.loads(m.group(1))


def _strip_comments(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def _css_tokens():
    css = CSS.read_text(encoding="utf-8")
    return css, dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", css))


def _luminance(hex_):
    h = hex_.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    chans = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        chans.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * chans[0] + 0.7152 * chans[1] + 0.0722 * chans[2]


def contrast(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# ── icon bundle ─────────────────────────────────────────────────────────────

def test_every_emoji_target_exists_in_icon_set():
    src = _src()
    icons, emoji = _json_var(src, "ICONS"), _json_var(src, "EMOJI_MAP")
    missing = sorted({v for v in emoji.values() if v not in icons})
    assert not missing, "EMOJI_MAP points at icons that are not bundled: %s" % missing


def test_required_emoji_are_mapped():
    emoji = _json_var(_src(), "EMOJI_MAP")
    missing = [e for e in REQUIRED_EMOJI if e not in emoji]
    assert not missing, "required emoji not mapped: %s" % missing


def test_skip_list_protects_inputs_and_ai_output():
    src = _src()
    skip = re.search(r"var SKIP_SELECTOR = \[(.*?)\]\.join", src, re.S)
    assert skip, "SKIP_SELECTOR array missing"
    for sel in REQUIRED_SKIP:
        assert sel in skip.group(1), "skip list lost %s" % sel


def test_no_network_access_in_icon_bundle():
    code = _strip_comments(_src())
    assert not re.search(r"https?://", code), "pc-icons.js must not reference any URL"
    for banned in ("fetch(", "XMLHttpRequest", "importScripts", "createElement('script')",
                   'createElement("script")', "@import", "url("):
        assert banned not in code, "pc-icons.js must stay offline (%s found)" % banned


def test_licence_notice_present():
    head = _src()[:2000]
    assert "ISC License" in head and "Lucide" in head, "Lucide ISC licence comment missing"


def test_icons_are_stroke_only_lucide_grid():
    src = _src()
    icons = _json_var(src, "ICONS")
    assert len(icons) >= 90
    for name, inner in icons.items():
        assert "<svg" not in inner, "%s should be inner markup only" % name
        assert not re.search(r'fill="(?!none|currentColor)', inner), "%s has a hard-coded fill" % name
        assert not re.search(r"#[0-9a-fA-F]{3,6}\b", inner), "%s has a hard-coded colour" % name
    for attr in ('viewBox="0 0 24 24"', 'stroke="currentColor"', 'stroke-width="1.75"',
                 'stroke-linecap="round"', 'stroke-linejoin="round"', 'fill="none"', 'aria-hidden'):
        assert attr in src, "svg() wrapper lost %s" % attr


def test_public_api_surface():
    src = _src()
    assert "window.PodClickIcons = {" in src
    for key in ("svg:", "mount:", "replaceEmoji:", "names:", "iconFor:"):
        assert key in src.split("window.PodClickIcons = {", 1)[1], "PodClickIcons.%s missing" % key
    assert "MutationObserver" in src and "DOMContentLoaded" in src


# ── nav wiring ──────────────────────────────────────────────────────────────

EMOJI_RE = re.compile("[←-⇿⌀-➿⬀-⯿\U0001F000-\U0001FAFF]")


def test_nav_loads_icons_itself_and_has_no_emoji():
    nav = NAV_JS.read_text(encoding="utf-8")
    assert re.search(r"/static/pc-icons\.js\?v=[\w-]+", nav), "nav must inject pc-icons.js with its own ?v="
    assert "data-i=" in nav or "PodClickIcons" in nav, "nav items must use the shared icon set"
    code = _strip_comments(nav)
    assert not EMOJI_RE.search(code), "nav still renders emoji: %r" % EMOJI_RE.findall(code)[:5]


def test_nav_icon_names_exist():
    icons = _json_var(_src(), "ICONS")
    nav = NAV_JS.read_text(encoding="utf-8")
    used = set(re.findall(r"icon:\s*'([a-z0-9-]+)'", nav)) | set(re.findall(r"ico\('([a-z0-9-]+)'", nav))
    assert used, "no icon names found in nav"
    assert not (used - set(icons)), "nav uses unbundled icons: %s" % sorted(used - set(icons))


# ── CSS layer ───────────────────────────────────────────────────────────────

def test_design_system_v2_tokens_present():
    css, tokens = _css_tokens()
    assert "/* ===== Design system v2 ===== */" in css
    for n in range(1, 9):
        assert "--space-%d" % n in tokens
    for t in ("--shadow-card", "--shadow-pop", "--ring", "--ease-out", "--dur-fast", "--dur",
              "--red-hi", "--blue-hi", "--purple-hi"):
        assert t in tokens, "token %s missing" % t
    assert tokens["--dur-fast"].strip() == "120ms" and tokens["--dur"].strip() == "200ms"
    v2 = css.split("/* ===== Design system v2 ===== */", 1)[1]
    assert "prefers-reduced-motion" in v2
    assert ".pc-i" in v2 and ":focus-visible" in v2 and ".skeleton" in v2 and ".empty-state" in v2


def test_hi_variants_meet_aa_on_card():
    _, tokens = _css_tokens()
    bg = tokens["--bg-card"].strip()
    for t in ("--red-hi", "--blue-hi", "--purple-hi"):
        ratio = contrast(tokens[t].strip(), bg)
        assert ratio >= 4.5, "%s on --bg-card is %.2f:1 (< 4.5)" % (t, ratio)


def test_v2_layer_does_not_break_existing_shell_selectors():
    css, _ = _css_tokens()
    head = css.split("/* ===== Design system v2 ===== */", 1)[0]
    for sel in (".pc-shell", ".pc-link.pc-active", ".pc-subtabs", ".pc-upload-box", "body.podclick-studio"):
        assert sel in head, "pre-existing selector %s was removed" % sel


# ── docs ────────────────────────────────────────────────────────────────────

def test_doc_lists_every_emoji_mapping():
    doc = DOC.read_text(encoding="utf-8")
    emoji = _json_var(_src(), "EMOJI_MAP")
    missing = [e for e, name in emoji.items() if not re.search(re.escape(e) + r"\s*\|\s*`" + re.escape(name) + "`", doc)]
    assert not missing, "DESIGN_SYSTEM.md table missing: %s" % missing[:10]
