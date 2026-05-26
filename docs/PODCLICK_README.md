# PodClick — Build Handoff Bundle

This is the complete handoff package for building PodClick. Read this file first.

## What's in this bundle

```
podclick-handoff/
├── README.md                          ← you are here
├── PODCLICK_MASTER_SOW.md             ← architectural spec (start here for the big picture)
├── PODCLICK_PHASES.md                 ← phased build plan with exit criteria
└── skills/
    ├── brick-voice/SKILL.md           ← Brick's character and tone rules
    ├── foundation-intake/SKILL.md     ← onboarding flow that pours the voice fingerprint
    ├── foundation-retrieval/SKILL.md  ← getBrandContext contract
    ├── social-publish-stagger/SKILL.md← publish queue + retry pattern
    └── vocabulary/SKILL.md            ← construction-language enforcement
```

## How to use this with Claude Code

### One-time setup
1. Drop the entire `podclick-handoff/` folder into your PodClick project root, OR
2. Put `skills/*` into `.claude/skills/` so Claude Code auto-loads them as project skills

### Starting any phase
Begin every Claude Code session with:

> "Read README.md, PODCLICK_MASTER_SOW.md, and PODCLICK_PHASES.md. Then read all four files in skills/. Confirm understanding of the construction vocabulary, the getBrandContext contract, and Brick's voice. We're executing Phase {N} today."

Claude Code should refuse to write code until it has read these files. The skills control quality across the entire codebase; skipping them produces drift that's expensive to fix later.

## The five contracts these documents enforce

1. **Every user-facing string uses construction vocabulary** (vocabulary skill)
2. **Foundation is poured from real user content, never AI summaries** (foundation-intake skill)
3. **Every AI generation for user-attributed content passes through getBrandContext()** (foundation-retrieval skill)
4. **Brick's character voice is consistent across every Brick-attributed message** (brick-voice skill)
5. **Every social publish goes through the stagger queue with proper retry logic** (social-publish-stagger skill)

If a phase delivery violates any of these, it's not done. Re-open and fix before moving on.

## Reading order

For first-time read-through:

1. **This README** (you're here) — 2 minutes
2. **PODCLICK_PHASES.md** — overall plan, what ships when — 10 minutes
3. **PODCLICK_MASTER_SOW.md** — full architecture and data model — 30-45 minutes
4. **skills/vocabulary/SKILL.md** — the construction language — 10 minutes
5. **skills/brick-voice/SKILL.md** — Brick's character — 10 minutes
6. **skills/foundation-retrieval/SKILL.md** — the AI generation contract — 15 minutes
7. **skills/social-publish-stagger/SKILL.md** — the publish queue rules — 10 minutes

Total first read: ~90 minutes. Worth every minute. Doing the reading prevents weeks of rework downstream.

## Existing state to be aware of

JP already has working:
- GHL integration (OAuth + publishing + token refresh)
- Studio (recording with device check)
- Audio Assembly (intro/main/commercial/outro)
- Guest CRM with Prospect/Booked/Recorded/Aired pipeline
- Sponsor library with affiliate tracking
- Release Queue with scheduled/published states
- Social Studio (Post Forge, Content Calendar week-view, Hashtag Lab, Repurpose Hub, Connections, Screen Recorder)
- Brand Studio with three intake paths
- Market Scout with virality scoring
- Script Lab, Cover Forge, Channel Advisor, Repurpose Engine, Lead Page, Pillar Planner

The build plan does NOT recreate any of the above. It refactors them to:
- Pull from Foundation (Phase 1)
- Publish through SocialService abstraction (Phase 2)
- Speak construction vocabulary (every phase)
- Be addressable by Brick (Phase 3)

## Decisions JP still needs to make

Per Section 17 of the SOW, before Phase 0 closes:

- Hosting region (US-East primary? multi-region?)
- Pricing tiers and trial length
- Recording engine: keep current or wrap LiveKit/Daily.co?
- Confirm domain (app.podclick.ai assumed)
- Initial beta agencies (3-5 friendly)

## Questions during build

If Claude Code is uncertain about anything during execution:

1. **Architectural question** → check PODCLICK_MASTER_SOW.md
2. **What ships when** → check PODCLICK_PHASES.md
3. **What does this say to the user** → check skills/vocabulary/
4. **How does Brick say this** → check skills/brick-voice/
5. **Should this generator call Foundation** → yes (always), check skills/foundation-retrieval/
6. **Edge case in publish flow** → check skills/social-publish-stagger/

If none of the above answers it, ask JP. Don't guess.

## One last principle

The construction metaphor is not a feature, it's the brand. Foundation is not a feature, it's the moat. Brick is not a feature, he's the relationship. Treat these three things with disproportionate care relative to everything else in the build.

Good site management starts here.

— Build handoff prepared in conversation, May 2026
