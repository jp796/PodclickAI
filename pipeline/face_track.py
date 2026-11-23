"""
face_track.py — face-aware framing for vertical clips ("auto" crop mode).

Looks at the clip's own frames (2 per second, downscaled, grayscale) with
OpenCV's bundled Haar face detector and decides how to frame the 9:16 output:

  solo — one person on screen. A 9:16 window follows their face like a camera
         operator would: it holds still while the face stays inside a dead zone
         and eases to a new spot only when the person really moves. Few, smooth
         moves instead of constant jitter.
  duo  — two people side by side. Keeps the top/bottom stack, but each half is
         cropped around that person's face instead of a fixed slice.
  none — no face found (title card, screen share, dark shot). Returns None so the
         caller keeps the old fixed crop.

Everything runs locally; nothing leaves the machine.
"""

import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

SAMPLE_FPS = 2.0
ANALYSIS_W = 480          # detection runs on a 480px-wide copy for speed


@dataclass
class CropPlan:
    kind: str                       # "solo" | "duo"
    filter_chain: str               # filtergraph from [0:v] up to (not incl.) captions
    detail: str = ""                # human-readable summary for logs


# ── Frame sampling + detection ───────────────────────────────────────────────

def _probe_wh(source: str) -> Tuple[int, int]:
    """Display size (after rotation). Phone recordings store 2160x3840 + a rotate flag."""
    import json
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:stream_tags=rotate:stream_side_data=rotation",
         "-of", "json", source],
        capture_output=True, text=True,
    )
    try:
        st = json.loads(r.stdout)["streams"][0]
    except Exception:
        return 1920, 1080
    w, h = int(st.get("width") or 1920), int(st.get("height") or 1080)
    rot = st.get("tags", {}).get("rotate")
    for sd in st.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = sd["rotation"]
    try:
        if abs(int(float(rot or 0))) % 180 == 90:
            w, h = h, w
    except ValueError:
        pass
    return w, h


def _sample_frames(source: str, start: float, duration: float,
                   src_w: int, src_h: int) -> Tuple[List[np.ndarray], float, float]:
    """
    Grayscale frames ANALYSIS_W wide. Returns (frames, scale, seconds_between_frames).

    Decodes keyframes only when they come at least every ~1.5s (phone and
    camera recordings: ~1s) — about 3-4x faster than decoding every frame of
    4K HEVC. Sources with sparse keyframes fall back to a full decode at
    SAMPLE_FPS.
    """
    aw = ANALYSIS_W
    ah = int(round(src_h * aw / src_w / 2)) * 2
    size = aw * ah

    def run(pre: List[str], vf: str) -> List[np.ndarray]:
        cmd = ["ffmpeg", "-v", "error", *pre, "-ss", str(start), "-t", str(duration),
               "-i", source, "-vf", vf, "-fps_mode", "passthrough",
               "-f", "rawvideo", "pipe:1"]
        out = subprocess.run(cmd, capture_output=True).stdout
        return [np.frombuffer(out[i * size:(i + 1) * size], dtype=np.uint8).reshape(ah, aw)
                for i in range(len(out) // size)]

    frames = run(["-skip_frame", "nokey"], f"scale={aw}:{ah},format=gray")
    if len(frames) < duration / 1.5:
        frames = run([], f"fps={SAMPLE_FPS},scale={aw}:{ah},format=gray")
    dt = duration / max(1, len(frames))
    return frames, src_w / aw, dt


_DETECTOR = None


def _detector():
    global _DETECTOR
    if _DETECTOR is None:
        import cv2
        _DETECTOR = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return _DETECTOR


def _faces(frame: np.ndarray) -> List[Tuple[float, float, float, float]]:
    """Faces as (cx, cy, w, h) in analysis pixels, largest first, small noise dropped."""
    min_side = max(24, frame.shape[0] // 12)
    found = _detector().detectMultiScale(frame, scaleFactor=1.1, minNeighbors=6,
                                         minSize=(min_side, min_side))
    faces = [(x + w / 2.0, y + h / 2.0, float(w), float(h)) for (x, y, w, h) in found]
    faces.sort(key=lambda f: -f[2])
    if faces:
        biggest = faces[0][2]
        faces = [f for f in faces if f[2] >= biggest * 0.55]
    return faces


# ── Camera path for a single speaker ─────────────────────────────────────────

def _fill_gaps(values: List[Optional[float]]) -> Optional[np.ndarray]:
    idx = [i for i, v in enumerate(values) if v is not None]
    if len(idx) < max(2, len(values) // 5):
        return None
    xs = np.array(idx, dtype=float)
    ys = np.array([values[i] for i in idx], dtype=float)
    return np.interp(np.arange(len(values), dtype=float), xs, ys)


def _median_smooth(arr: np.ndarray, k: int = 5) -> np.ndarray:
    pad = k // 2
    padded = np.pad(arr, pad, mode="edge")
    return np.array([np.median(padded[i:i + k]) for i in range(len(arr))])


def _camera_keyframes(centers: np.ndarray, dead_zone: float, dt: float,
                      move_secs: float = 0.6, settle_secs: float = 1.0) -> List[Tuple[float, float]]:
    """
    Turn a per-sample face-centre track into a few (time, centre) keyframes.

    The camera holds its position while the face stays within ±dead_zone. When
    the face sits outside it for `settle_secs`, the camera eases to the face's
    new resting spot over `move_secs`. Brief leans/gestures never move it.
    """
    n = len(centers)
    settle = max(1, int(round(settle_secs / dt)))
    cam = float(np.median(centers[:max(1, min(n, int(2 / dt)))]))
    keys = [(0.0, cam)]
    i = 0
    while i < n:
        if abs(centers[i] - cam) > dead_zone:
            window = centers[i:i + settle]
            if len(window) >= settle and np.all(np.abs(window - cam) > dead_zone):
                target = float(np.median(centers[i:i + settle * 2]))
                t0 = i * dt
                keys.append((t0, cam))
                keys.append((t0 + move_secs, target))
                cam = target
                i += settle
                continue
        i += 1
    return keys


def _lerp_expr(keys: List[Tuple[float, float]], lo: float, hi: float) -> str:
    """ffmpeg expression (variable t) for a piecewise-linear path, clamped to [lo, hi]."""
    def clamp(v):
        return max(lo, min(hi, v))

    keys = [(t, clamp(v)) for t, v in keys]
    expr = f"{keys[-1][1]:.1f}"
    for (t0, v0), (t1, v1) in reversed(list(zip(keys, keys[1:]))):
        if abs(v1 - v0) < 0.5 or t1 <= t0:
            seg = f"{v0:.1f}"
        else:
            seg = f"{v0:.1f}+({v1 - v0:.1f})*(t-{t0:.2f})/{t1 - t0:.2f}"
        expr = f"if(lt(t,{t1:.2f}),{seg},{expr})"
    return expr


# ── Public entry point ───────────────────────────────────────────────────────

def plan_auto_crop(source: str, start: float, end: float,
                   content: Tuple[int, int, int, int],
                   out_w: int = 1080, out_h: int = 1920) -> Optional[CropPlan]:
    """
    Decide framing for one clip. `content` = (x, y, w, h) of the picture area
    inside the source (letterbox removed). Returns None when no faces are found.
    """
    cx0, cy0, cw, ch = content
    src_w, src_h = _probe_wh(source)
    frames, scale, dt = _sample_frames(source, start, max(0.5, end - start), src_w, src_h)
    if not frames:
        return None

    per_frame = [[(fx * scale, fy * scale, fw * scale, fh * scale) for fx, fy, fw, fh in _faces(f)]
                 for f in frames]
    counts = [len(f) for f in per_frame if f]
    if len(counts) < max(2, len(frames) // 5):
        return None
    two_up = sum(1 for c in counts if c >= 2)

    def fill(w, h):
        return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"

    # ── duo: two faces side by side, most of the time ──
    if two_up >= len(counts) * 0.6:
        mid = cx0 + cw / 2.0
        left = [min(fs, key=lambda f: f[0]) for fs in per_frame if len(fs) >= 2]
        right = [max(fs, key=lambda f: f[0]) for fs in per_frame if len(fs) >= 2]
        if np.median([f[0] for f in left]) < mid < np.median([f[0] for f in right]):
            half_w = int(cw // 2) // 2 * 2
            cell_h = int(round(half_w * (out_h / 2.0) / out_w)) // 2 * 2
            cell_h = min(cell_h, ch // 2 * 2)

            def cell_y(faces):
                fy = float(np.median([f[1] for f in faces]))
                fh = float(np.median([f[3] for f in faces]))
                y = fy - fh * 0.2 - cell_h * 0.42        # eyes a little above centre
                return int(max(cy0, min(cy0 + ch - cell_h, y)))

            top = f"crop={half_w}:{cell_h}:{cx0}:{cell_y(left)}"
            bot = f"crop={half_w}:{cell_h}:{cx0 + half_w}:{cell_y(right)}"
            chain = (f"[0:v]{top},{fill(out_w, out_h // 2)}[top];"
                     f"[0:v]{bot},{fill(out_w, out_h // 2)}[bot];"
                     f"[top][bot]vstack=inputs=2")
            return CropPlan("duo", chain, "two speakers, each framed on their face")

    # ── solo: follow the main face ──
    track: List[Optional[float]] = []
    prev = None
    for fs in per_frame:
        if not fs:
            track.append(None)
            continue
        if prev is None:
            pick = fs[0]
        else:   # stay on the same person unless someone much bigger appears
            pick = min(fs, key=lambda f: abs(f[0] - prev) - f[2] * 0.5)
        prev = pick[0]
        track.append(pick[0])
    filled = _fill_gaps(track)
    if filled is None:
        return None
    centers = _median_smooth(filled, 5)

    win_w = int(round(ch * out_w / out_h)) // 2 * 2
    win_w = min(win_w, cw // 2 * 2)
    keys = _camera_keyframes(centers, dead_zone=win_w * 0.14, dt=dt)
    lo, hi = float(cx0), float(cx0 + cw - win_w)
    # keyframes are face centres → convert to window left edges
    keys = [(t, c - win_w / 2.0) for t, c in keys]
    x_expr = _lerp_expr(keys, lo, hi)
    chain = f"[0:v]crop=w={win_w}:h={ch // 2 * 2}:x='{x_expr}':y={cy0},{fill(out_w, out_h)}"
    moves = (len(keys) - 1) // 2
    return CropPlan("solo", chain, f"one speaker, face-centred, {moves} camera move(s)")
