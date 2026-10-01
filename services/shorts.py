"""
Shorts distribution bookkeeping — which clip/platform pairs are already drafted.

`POST /api/projects/{id}/distribute-shorts` had no idempotency: it sorted the
rendered clips by virality, sliced the top N and drafted every one, so pressing
Step 3's "Send to Instagram + TikTok" twice produced a second full set of drafts
in the GHL planner with nothing to distinguish them from the first. Same class of
bug as auto-plan only ever appending.

Both halves live here as pure functions so the behaviour is testable without a
database, a GHL account, or the route's ASGI machinery:

  partition_drafted() — split clips into (still to do, already drafted)
  merge_distributed() — fold newly created drafts into the stored record

The record is persisted at `project.legacy_metadata["shorts_distributed"]` as
{clip_id: [platform, ...]}. The route must assign a FRESH dict and call
flag_modified — mutating the JSONB in place does not persist (see
docs/BUGS_AND_FIXES.md, YouTube chapters).
"""

from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

SKIP_REASON = "already_drafted — pass force:true to repeat"


def partition_drafted(
    clip_ids: Sequence[str],
    requested_platforms: Iterable[str],
    distributed: Mapping[str, Iterable[str]],
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Split clip ids into (pending, skipped) against the stored draft record.

    A clip is skipped only when every requested platform is already recorded for
    it — asking for a platform it has not been drafted to yet still goes through,
    so adding TikTok to an Instagram-only clip works.

    Returns (pending clip ids in the given order, skip entries for the response).
    """
    wanted = {str(p).lower() for p in requested_platforms}
    pending: List[str] = []
    skipped: List[Dict[str, Any]] = []
    for cid in clip_ids:
        done = {str(p).lower() for p in (distributed.get(str(cid)) or [])}
        if wanted and done >= wanted:
            skipped.append({
                "platform": ",".join(sorted(done)),
                "clip_id": str(cid),
                "reason": SKIP_REASON,
            })
        else:
            pending.append(cid)
    return pending, skipped


def merge_distributed(
    distributed: Mapping[str, Iterable[str]],
    created: Iterable[Mapping[str, Any]],
) -> Dict[str, List[str]]:
    """Fold `created` draft records into the stored {clip_id: [platform]} map.

    Returns a NEW dict — the caller assigns it to the JSONB column so SQLAlchemy
    sees a changed value. Existing platforms for a clip are preserved, so a
    second run that adds one platform does not erase the first.
    """
    merged: Dict[str, List[str]] = {
        str(cid): sorted({str(p).lower() for p in (plats or [])})
        for cid, plats in distributed.items()
    }
    for item in created:
        cid = str(item.get("clip_id") or "")
        plat = str(item.get("platform") or "").lower()
        if not cid or not plat:
            continue
        merged[cid] = sorted(set(merged.get(cid) or []) | {plat})
    return merged
