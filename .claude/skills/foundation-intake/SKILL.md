---
name: foundation-intake
description: Use this skill when building or refactoring the Foundation intake flow — the onboarding experience where a new PodClick user contributes content that trains their personal voice fingerprint. Apply when implementing the voice interview, the audio/video upload pipeline, text ingestion, channel auto-pull (YouTube/RSS/blog), the Blueprint auto-generation from samples, the Foundation progress UI, the Foundation-ready thresholds, the tier-gated upgrade prompts, and the empty-state handling when users hit generators before Foundation is ready. Do NOT use this skill for ongoing sample ingestion from approved posts (that's part of foundation-retrieval) — this skill is specifically for the initial intake and any subsequent "add more material" flows.
---

# Foundation intake — pouring the concrete

PodClick's voice fingerprint is the moat. The intake flow is where that moat starts. Get this right and a new user goes from signup to "this AI sounds like me" within 15 minutes. Get it wrong and they bounce before Foundation has anything to work with.

This skill encodes the philosophy, the flow, the technical pipeline, and the thresholds.

## The philosophy in one sentence

**Foundation is poured from the user's real material — never from AI-generated summaries about them.**

Brand Briefs, Bio Packs, Conversion Packs, and other AI-generated outputs *describing* the user must NOT be ingested as voice samples. They were written by AI about the user, not by the user. Ingesting them would teach Foundation to sound like "AI talking about JP" instead of "JP talking."

What goes into Foundation must be content the user actually produced — spoken, written, recorded.

## The four intake sources (ranked by signal quality)

### Tier 1 — Native voice (gold standard)

**A. Voice interview** (the killer feature)
8-10 guided prompts recorded in the browser, 30-60 seconds each. Designed to surface the user's voice across different registers — formal, personal, opinionated, technical, casual. 5-10 minutes total, produces the cleanest training corpus possible.

**B. Audio/video upload**
Podcasts, YouTube videos, recorded talks, Loom recordings, webinar replays. Anything where the user is speaking unscripted (or lightly scripted). Transcribed via Whisper, chunked, ingested.

### Tier 2 — Written by the user

**C. Text upload or paste**
Blog posts, articles, LinkedIn posts they wrote themselves, books they've authored, long-form essays. Direct ingestion (no transcription needed).

### Tier 3 — Channel auto-pull

**D. URL/channel sync**
YouTube channel ID → auto-pull captions from recent videos
Podcast RSS feed → auto-pull transcripts (if available) or download audio and transcribe
Blog URL → scrape recent posts
LinkedIn profile URL → caveat: LinkedIn doesn't allow scraping, so this requires user copy/paste

Channel pulls are powerful but introduce noise — old content may not represent current voice, guest interviews may include guest speech, etc. Always tag with `source='historical'` and let the user curate.

## The intake flow (screen by screen)

### Screen 1: Foundation introduction

```
┌──────────────────────────────────────────────────────────┐
│                                                          │
│  Pour your foundation                                    │
│                                                          │
│  Brick learns your voice from real you — not from        │
│  forms or summaries. Pick any path below. More           │
│  material means a stronger foundation.                   │
│                                                          │
│  Most users hit "solid" foundation in 15 minutes.        │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

Don't ask the user to commit to all paths up front. Show all four, let them start anywhere.

### Screen 2: Four intake cards

Display four cards, ranked Tier 1 first:

**Card 1 — Talk to Brick (recommended)**
> *5-10 minutes · Guided voice interview*
> *"Best signal. Just answer my questions out loud."*
> Button: **Start interview →**

**Card 2 — Upload audio or video**
> *Bring podcasts, talks, Loom recordings*
> *"I'll transcribe and learn from how you speak."*
> Button: **Upload files →**

**Card 3 — Paste your writing**
> *Blog posts, articles, LinkedIn — your words*
> *"Best for writers. Paste 3-5 pieces and I've got plenty to work with."*
> Button: **Paste content →**

**Card 4 — Point me at your channels**
> *YouTube · podcast RSS · blog URL*
> *"I'll pull your recent material automatically."*
> Button: **Connect channels →**

### Screen 3: Live progress (the magic moment)

As content ingests, show Foundation building in real time:

```
┌──────────────────────────────────────────────────────────┐
│  Pouring foundation...                                   │
│                                                          │
│  ▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░  thin → solid           │
│                                                          │
│  Recent samples ingested:                                │
│  ✓  Voice interview answer #3 · 47 sec                   │
│  ✓  EP 42 transcript chunk · 312 words                   │
│  ✓  Blog post: "Why Springfield is heating up"           │
│  ✓  LinkedIn post · May 14                               │
│                                                          │
│  Brick: "Solid material. You sound clearest when you     │
│  have an opinion. Keep going — 5 more minutes of audio   │
│  and I'll be set."                                       │
│                                                          │
│  [ Add more ↗ ]            [ I'm done for now → ]        │
└──────────────────────────────────────────────────────────┘
```

This screen is the conversion moment. The user sees their foundation materialize, hears Brick comment on it, and *wants* to add more. Don't rush them to the next step.

### Screen 4: Blueprint draft (auto-generated from Foundation)

Once Foundation has minimum 5 samples, run an analysis pass that auto-populates the Blueprint:

```
┌──────────────────────────────────────────────────────────┐
│  Here's what your foundation tells me about you.         │
│  Edit anything that's off.                               │
│                                                          │
│  TONE:                                                   │
│  [ Direct ] [ Warm ] [ No-fluff ] [ Operator energy ]    │
│                                                          │
│  WORDS YOU USE:                                          │
│  "the data shows" · "here's what I'd do" · "y'all"       │
│  "straight up" · "deal" · "worth a look"                 │
│                                                          │
│  WORDS YOU NEVER USE:                                    │
│  (none detected yet — Brick will learn over time)        │
│                                                          │
│  AUDIENCE I HEARD YOU TALKING TO:                        │
│  Relocation buyers · First-time homebuyers · Investors   │
│                                                          │
│  TOPICS IN YOUR MATERIAL:                                │
│  ▓ Market intelligence (32%)                             │
│  ▓ Neighborhood deep dives (24%)                         │
│  ▓ AI & real estate tooling (19%)                        │
│  ▓ Behind-the-deal stories (15%)                         │
│  ▓ Personal · family · faith (10%)                       │
│                                                          │
│  [ Looks right ✓ ]              [ Let me adjust → ]      │
└──────────────────────────────────────────────────────────┘
```

This is dramatically better than asking the user to *describe* their tone in abstract terms. They don't have to think "am I direct? casual?" — the system tells them what their actual material reveals. They just confirm or correct.

## The voice interview script

This is the single highest-leverage intake feature. The script:

### Setup
*"I'm going to ask you 8 questions. Answer each one out loud like you would to a friend. 30-60 seconds each is plenty. There are no right answers — I just need to hear how you talk."*

### The questions (in order)

1. **Introduce yourself like you would at a networking event.**
   *"Who are you, what do you do, and where do you work?"*
   → Surfaces: identity, positioning, professional register

2. **Tell me about the last deal or project you're proud of.**
   *"What made it special?"*
   → Surfaces: storytelling cadence, what they care about

3. **What's something most people in your industry get wrong?**
   *"I want a real opinion, not a polite one."*
   → Surfaces: opinions, edge, where they have conviction

4. **Describe your ideal client.**
   *"What do they look like, what do they need from you?"*
   → Surfaces: audience awareness, empathy language

5. **Walk me through a typical Tuesday.**
   *"What's a normal day in your business?"*
   → Surfaces: rhythms, terminology, what they actually do

6. **What's the most valuable thing you've learned in this business?**
   *"The thing you wish someone told you 5 years ago."*
   → Surfaces: wisdom register, reflective tone

7. **If you had 60 seconds to convince someone to work with you, what would you say?**
   *"This is your elevator pitch — say it like you mean it."*
   → Surfaces: sales register, conviction

8. **What do you do for fun outside of work?**
   *"Hobbies, family, things you care about."*
   → Surfaces: personal register, warmth, humanness

### Optional questions (Brick can add these for longer interviews)

9. *"What's a misconception about you or your business?"*
10. *"If you had to teach a beginner one thing, what would it be?"*

### Interview UI

Dead simple. One question on screen. Big red record button. Question stays visible while recording. Stop button when done. System auto-transcribes in background, ingests, advances to next question.

Brick comments lightly between questions on the side panel: *"Good answer on #3. You sound more like yourself when you have an opinion."*

After all questions: *"That's the interview. I've got 4 minutes 12 seconds of you talking. Foundation jumped to 34%. Want to add anything else now or come back to it?"*

## Audio/video upload pipeline

When a user uploads audio or video:

```
1. File hits upload endpoint, lands in S3
2. Worker picks up the file
3. If video: extract audio via FFmpeg
4. If duration > 5 min: chunk into 5-min segments for parallel transcription
5. Transcribe via Whisper API (or local Whisper if cost demands)
6. Diarize if multiple speakers detected
7. Filter to host-only segments (skip guest speech)
8. Chunk transcript into 300-token segments with 50-token overlap
9. For each chunk:
   - Skip if word count < 30 (too short to be useful)
   - Skip if all caps or all numbers (likely a label)
   - Embed via OpenAI
   - Insert into voice_samples with source='podcast' (audio) or 'historical' (older uploads)
10. Notify user via WebSocket as chunks ingest
11. Update Foundation score in background
```

### Diarization caveat

If the upload is a podcast with the user + a guest, the user only wants *their* speech ingested. Use a diarization library (pyannote, AssemblyAI's diarization, or AWS Transcribe's speaker labels) to identify speakers. Ask the user once: "Which speaker are you?" Then ingest only their segments.

If diarization fails or is unclear, default to skipping audio with multiple speakers and showing the user: *"This sounds like a conversation. Tell me which voice is yours so I only learn from your side."*

## Text ingestion pipeline

When a user pastes or uploads text:

```
1. Detect format (markdown, HTML, plain text, .docx, .pdf)
2. Extract clean text (strip HTML tags, parse docx, etc.)
3. Chunk into 300-token segments with 50-token overlap
4. For each chunk:
   - Skip if word count < 30
   - Skip if it looks like a heading/title (line break density high)
   - Embed
   - Insert with source='social_approved' if from social, 'historical' if older
5. Update foundation score
```

For LinkedIn posts specifically, prompt the user to copy from their LinkedIn profile and paste — LinkedIn doesn't allow scraping, but copy/paste works fine.

## Channel auto-pull pipeline

### YouTube channel
```
1. User pastes channel URL or handle
2. Resolve to channel ID via YouTube Data API
3. Fetch most recent 20 videos
4. For each video:
   - Fetch captions if available
   - If captions exist: parse, treat as text, ingest with source='historical', platform='youtube'
   - If no captions: queue audio download + Whisper transcription
5. Limit to 20 videos initially to avoid massive ingestion job
6. Let user opt in to "keep syncing" for future videos
```

### Podcast RSS
```
1. User pastes RSS URL
2. Fetch and parse RSS feed
3. For most recent 10 episodes:
   - Download MP3
   - Run through audio pipeline (Whisper, diarization, ingest)
4. Heavy operation — show clear progress, est. 30-60 min
5. Offer "keep syncing future episodes" option
```

### Blog URL
```
1. User pastes blog URL
2. Detect if it's RSS-discoverable, sitemap-discoverable, or needs scraping
3. Fetch most recent 10-15 posts
4. Extract main text (using readability library, not raw HTML)
5. Ingest each post as a sample with source='historical'
```

## The Foundation-readiness thresholds

Encode these in code as constants, not magic numbers:

```typescript
const FOUNDATION_THRESHOLDS = {
  NOT_READY: { maxSamples: 4, label: 'Pouring foundation...' },
  THIN: { maxSamples: 14, label: 'Foundation thin — keep adding' },
  SOLID: { maxSamples: 49, label: 'Foundation set' },
  DEEP: { maxSamples: Infinity, label: 'Foundation deep' }
};

function getFoundationStatus(sampleCount: number): FoundationStatus {
  if (sampleCount <= FOUNDATION_THRESHOLDS.NOT_READY.maxSamples) return 'not_ready';
  if (sampleCount <= FOUNDATION_THRESHOLDS.THIN.maxSamples) return 'thin';
  if (sampleCount <= FOUNDATION_THRESHOLDS.SOLID.maxSamples) return 'solid';
  return 'deep';
}
```

### Generator behavior at each tier

| Tier | Generators behavior |
|---|---|
| `not_ready` | Block generation. Show: "Foundation needs at least 5 samples. Pour more first." |
| `thin` | Allow generation with warning: "Foundation still thin — output quality will improve as you add more." |
| `solid` | Normal operation. No warnings. |
| `deep` | Normal operation. Foundation match score should be 70%+ at this point. |

### UI badges

Show the tier prominently in Blueprint and on the Walk-through:

- 🟡 *"Foundation thin"* — when below SOLID
- 🟢 *"Foundation set"* — when at SOLID  
- 🟢 *"Foundation deep"* — when at DEEP

Don't badge `not_ready` cheerfully — it's blocking. Show the action: *"Pour foundation to unlock Brick."*

## Blueprint auto-generation from Foundation

After at least 5 samples are ingested, run a one-time auto-population of the Blueprint:

```typescript
async function autoGenerateBlueprint(locationId: string) {
  const samples = await db.voice_samples.findAll({
    where: { location_id: locationId, excluded: false },
    limit: 30,
    orderBy: 'created_at DESC'
  });
  
  if (samples.length < 5) throw new Error('Not enough samples');
  
  const allText = samples.map(s => s.text).join('\n\n---\n\n');
  
  // Single LLM call to extract structured profile
  const result = await anthropic.messages.create({
    model: 'claude-sonnet-4-6',
    max_tokens: 2000,
    messages: [{
      role: 'user',
      content: `Analyze the following voice samples from one person and extract their brand profile. Return strict JSON.

Samples:
${allText}

Return JSON with these fields:
- tone: array of 3-5 adjectives describing tone (e.g., ["direct", "warm", "no-fluff"])
- cadence: one-sentence description of their speaking/writing rhythm
- pov: "first-person" or "second-person" or "third-person"
- humor_level: "none" or "dry, sparingly" or "frequent" or "playful"
- vocabulary_yes: array of 5-10 phrases or words they use naturally (verbatim from samples)
- vocabulary_no: empty array (Brick will learn this over time from edits)
- audience_primary: their main audience as described in samples
- audience_pain_points: array of pain points they reference
- pillars: array of 3-5 content pillars detected with {name, weight (0-1), examples} — weights sum to 1.0

Do not invent details not in the samples. If something can't be inferred, leave it null or empty.`
    }]
  });
  
  const parsed = JSON.parse(extractJSON(result.content[0].text));
  
  await db.blueprints.upsert({
    location_id: locationId,
    ...parsed,
    auto_generated_at: new Date()
  });
  
  return parsed;
}
```

The Blueprint screen then shows these draft values to the user with edit affordances. They confirm or tweak. The system never silently uses auto-generated values without user review.

## Sample weighting at intake

When samples are ingested during intake, apply these weights:

| Source during intake | Weight |
|---|---|
| Voice interview | 1.4 (purpose-built, fresh, on-message) |
| Audio/video upload (recent, <12mo) | 1.2 |
| Audio/video upload (older) | 0.9 |
| Text paste (recent, written by user) | 1.2 |
| Text upload (blog posts, articles) | 1.0 |
| Channel auto-pull (YouTube/RSS) | 0.8 (mixed quality, may include guest voices) |
| Blog scrape | 0.8 (may include footers, CTAs, boilerplate) |

These weights influence retrieval at generation time — higher-weight samples surface more often.

## Curation after intake

After intake, the Blueprint > Foundation panel lets the user manage samples:

- **Browse:** scrollable list of all samples with source, date, preview
- **Promote:** mark a sample as "extra you" → weight × 2
- **Exclude:** keep the sample but never retrieve it (user filtering)
- **Delete:** permanently remove

This is where users with mature foundations spend time. They notice "this sample is from a guest interview where my guest dominated — exclude it." Or "this post really nailed my voice — promote it."

Brick can suggest curation proactively: *"I noticed this sample is mostly your guest's words. Want to exclude it?"*

## The SaaS upgrade trigger

Free tier limits to encode:

```typescript
const FREE_TIER = {
  maxSamples: 25,
  maxAudioMinutes: 30,
  voiceInterviews: 1,
  channelSyncs: 0  // upgrade required for auto-sync
};

const PRO_TIER = {
  maxSamples: Infinity,
  maxAudioMinutes: Infinity,
  voiceInterviews: Infinity,
  channelSyncs: Infinity
};
```

When a free user hits the cap, show:

> *"Foundation capped at 25 samples on the free plan. Right now you're at 25 — Brick can't get any smarter unless you upgrade."*
> *Button: "Upgrade to keep building →"*

That's a more honest upgrade prompt than feature-gating arbitrary stuff. Users who actually want their AI to sound like them will pay. Users who don't won't — and that's fine.

## Empty-state handling for generators

When a user hits any generator (Post Forge, Script Lab, Cover Forge, etc.) before Foundation is ready (`not_ready` tier), the generator must NOT produce generic output. Instead:

```
┌──────────────────────────────────────────────────────────┐
│                                                          │
│  Foundation not ready                                    │
│                                                          │
│  I can't write in your voice until I know your voice.    │
│  Takes 10 minutes — record a voice interview or upload   │
│  a podcast you've done.                                  │
│                                                          │
│  [ Pour the foundation → ]                               │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

This converts a friction moment into a value moment. The user understands *why* Foundation matters because they tried to skip it and PodClick stopped them with a concrete reason.

## Implementation order for Phase 1

Given everything above, the engineering order:

1. **Database:** `voice_samples`, `foundation_scores`, `blueprints` tables with pgvector setup
2. **Foundation Service core:** `getBrandContext()`, `ingestSample()`, `getFoundationStatus()`
3. **Text ingestion:** simplest path — paste text in UI, runs through chunking + embedding pipeline, samples appear
4. **Audio ingestion:** S3 upload + Whisper transcription + chunking + embedding
5. **Voice interview UI:** 8 questions, browser audio recording, submit each question as audio file → audio pipeline
6. **Blueprint auto-generation:** the analysis pass that turns 5+ samples into a Blueprint draft
7. **Blueprint UI:** Foundation panel showing samples + score + curation actions
8. **Channel auto-pull (last):** YouTube API integration, RSS parsing, blog scraping
9. **Generator empty-state:** error pattern when generators hit `not_ready` Foundation
10. **Post Forge refactor:** first generator to call `getBrandContext` end-to-end

Each step can be demoed independently. After step 5 (voice interview), you have a complete user-facing onboarding flow worth showing beta users.

## The 60-second test for any intake design choice

When designing an intake decision, ask:
1. Does this collect *real user content*, not summaries?
2. Will the user see Foundation building in real time?
3. Can they exit and resume without losing progress?
4. Does it feel like they're contributing to something valuable, not filling out a form?
5. Does Brick talk to them during the process so it feels guided?

Five yeses, ship. Any no, reconsider.

## Summary: the one-sentence contract

**Foundation intake collects real user-produced content, ingests it transparently in real time, auto-populates the Blueprint from the material itself, and never produces a Brand Brief that wasn't grounded in the user's actual voice.**
