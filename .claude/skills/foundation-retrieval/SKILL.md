---
name: foundation-retrieval
description: Use this skill any time PodClick generates user-facing content via an LLM — social posts, captions, show notes, episode descriptions, guest outreach emails, sponsor pitches, thumbnail text, hashtag suggestions, clip captions, script outlines, scout remixes, or any other text intended to sound like the user. Apply whenever building or refactoring a generator service, when adding a new AI-powered feature, when writing prompt templates that produce content attributed to the user (not Brick), and when reviewing existing code to ensure no LLM calls bypass the Foundation. Do NOT use this skill for Brick's own speech (status reports, walk-throughs, chat) — that's the brick-voice skill.
---

# Foundation retrieval — the contract every generator must follow

PodClick has one rule that's stronger than any other architectural rule: **every piece of AI-generated content that's attributed to the user must pass through `getBrandContext()`.** No exceptions. No "quick generators" that skip it. No "temporary" direct LLM calls.

Why this is absolute: the voice fingerprint is PodClick's moat. The longer a user uses PodClick, the more "them" their AI output gets, and the more switching costs accumulate against any competitor. If a single generator bypasses Foundation, the moat has a leak — that generator produces generic content, the user notices, trust erodes. One leak kills the brand promise.

This skill encodes the contract.

## The function signature

```typescript
async function getBrandContext(params: {
  locationId: string;
  taskType: BrandContextTaskType;
  topic?: string;
  platform?: SocialPlatform;
  audience?: string;
  additionalContext?: string;
}): Promise<BrandContext>

type BrandContextTaskType =
  | 'linkedin_post'
  | 'facebook_post'
  | 'instagram_caption'
  | 'instagram_first_comment'
  | 'tiktok_caption'
  | 'youtube_short_caption'
  | 'x_post'
  | 'episode_title'
  | 'episode_description'
  | 'show_notes'
  | 'clip_caption'
  | 'thumbnail_text'
  | 'hashtag_set'
  | 'guest_outreach_email'
  | 'guest_asset_email'
  | 'sponsor_pitch'
  | 'podcast_script_outline'
  | 'podcast_intro_script'
  | 'podcast_outro_script'
  | 'scout_remix_script'
  | 'brick_chat_response';  // Brick uses this when answering "what should I post"

interface BrandContext {
  brandProfile: {
    fullName: string;
    marketCity: string;
    nicheePrimary: string;
    audiencePrimary: string;
    onePager: string;
    differentiators: string[];
    pillars: ContentPillar[];
  };
  voiceProfile: {
    tone: string[];
    cadence: string;
    pov: string;
    humorLevel: string;
  };
  vocabulary: {
    use: string[];      // vocabulary_yes from blueprint
    avoid: string[];    // vocabulary_no from blueprint
  };
  voiceSamples: VoiceSample[];   // 3-5 retrieved samples
  foundationScore: number;        // 0-1
  metadata: {
    retrievalQuery: string;       // for debugging
    sampleCount: number;          // total samples in user's foundation
    retrievedAt: Date;
  };
}

interface VoiceSample {
  text: string;
  source: 'podcast' | 'social_approved' | 'social_edited' | 'written_from_scratch' | 'brand_studio' | 'historical';
  weight: number;
  platform?: string;
  similarity: number;  // 0-1, cosine similarity to query
}
```

## How the function works under the hood

```typescript
async function getBrandContext({ locationId, taskType, topic, platform, audience, additionalContext }) {
  // 1. Load structured blueprint
  const blueprint = await db.blueprints.findOne({ location_id: locationId });
  if (!blueprint) throw new BrandContextError('Blueprint not found — user must complete intake first');

  // 2. Build retrieval query
  const queryText = buildRetrievalQuery({ taskType, topic, platform, audience, additionalContext });
  
  // 3. Embed the query
  const queryEmbedding = await embeddings.embed(queryText);
  
  // 4. Query vector store
  const platformFilter = platform || inferPlatformFromTaskType(taskType);
  const samples = await db.query(`
    SELECT text, source, weight, platform, 
           1 - (embedding <=> $1) AS similarity
    FROM voice_samples
    WHERE location_id = $2
      AND excluded = false
      AND (platform IS NULL OR platform = $3 OR $3 IS NULL)
    ORDER BY (embedding <=> $1) / weight ASC
    LIMIT 5
  `, [queryEmbedding, locationId, platformFilter]);
  
  // 5. Load latest foundation score
  const score = await db.foundation_scores.findOne({ 
    location_id: locationId 
  }, { order: 'computed_at DESC' });
  
  // 6. Count total samples (for metadata)
  const sampleCount = await db.voice_samples.count({ 
    location_id: locationId, 
    excluded: false 
  });
  
  return {
    brandProfile: extractBrandProfile(blueprint),
    voiceProfile: extractVoiceProfile(blueprint),
    vocabulary: { use: blueprint.vocabulary_yes, avoid: blueprint.vocabulary_no },
    voiceSamples: samples,
    foundationScore: score?.score ?? 0,
    metadata: { retrievalQuery: queryText, sampleCount, retrievedAt: new Date() }
  };
}
```

## The prompt construction pattern

After retrieving context, every generator builds prompts using this exact pattern:

```
SYSTEM:
You are writing {taskType} in the voice of {brandProfile.fullName}, a {brandProfile.niche_primary} in {brandProfile.marketCity}.

BRAND CONTEXT:
- Audience: {brandProfile.audiencePrimary}
- Positioning: {brandProfile.onePager}
- Differentiators: {differentiators joined}

VOICE PROFILE:
- Tone: {voiceProfile.tone joined}
- Cadence: {voiceProfile.cadence}
- Point of view: {voiceProfile.pov}
- Humor: {voiceProfile.humorLevel}

VOCABULARY:
- Use naturally when it fits: {vocabulary.use}
- Never use: {vocabulary.avoid}

EXAMPLES OF HOW {fullName} WRITES SIMILAR CONTENT:
{For each voiceSample:}
{index + 1}. ({sample.source}) {sample.text}

STYLE NOTES:
- Match the voice in the examples above.
- Do not copy phrasing directly — write fresh content in the same voice.
- Length and structure should match what's typical for {taskType}.

USER:
{the specific task prompt}
```

## The five things that go wrong if you skip this skill

I'm naming these because each one is a real failure mode that breaks the product:

**1. The generic-output failure.** Generator writes "Are you looking to buy a home in Springfield? Contact us today!" — corporate-real-estate boilerplate. Foundation prevents this by injecting the user's actual style.

**2. The voice-drift failure.** Different modules produce content in different "voices" because each prompt was tuned independently. User feels the inconsistency, trust erodes. Foundation ensures every module pulls from one source of truth.

**3. The hallucinated-fact failure.** Generator invents details about the user's market, audience, or business. Foundation grounds output in real Blueprint data the user verified.

**4. The banned-phrase failure.** AI output contains "leverage" or "unlock" or "in today's market" — words the user explicitly hates. Foundation injects the user's `vocabulary_no` list as a hard constraint.

**5. The fingerprint-stagnation failure.** User's voice fingerprint never improves because new samples aren't being collected from their approved content. Foundation includes the sample-ingestion side, which must run on every approval/edit event.

## Mandatory checkpoints in every generator

When building or refactoring a generator that produces user-attributed content, verify these checkpoints exist:

### Checkpoint 1: Single entry point
The generator has exactly one function that takes a task spec and returns generated content. That function is the only place LLM is called.

```typescript
// ✅ Correct
async function generateLinkedInPost(spec: LinkedInPostSpec): Promise<string> {
  const context = await getBrandContext({ ... });
  return await generation.generate({ context, spec });
}

// ❌ Wrong — multiple LLM call sites
async function generateLinkedInPost(spec) {
  return await anthropic.messages.create({ ... });
}
async function generateLinkedInPostV2(spec) {
  return await openai.chat.completions.create({ ... });
}
```

### Checkpoint 2: getBrandContext called before LLM
The first line of the generator (after validation) is a `getBrandContext()` call. Never skip it, even for "simple" generations.

```typescript
// ✅ Correct
async function generateClipCaption(spec) {
  validate(spec);
  const context = await getBrandContext({ locationId: spec.locationId, taskType: 'clip_caption', topic: spec.topic, platform: spec.platform });
  return await generation.generate({ context, spec });
}

// ❌ Wrong — direct call, skipping foundation
async function generateClipCaption(spec) {
  return await anthropic.messages.create({ messages: [{ role: 'user', content: `Write a caption about ${spec.topic}` }] });
}
```

### Checkpoint 3: Sample ingestion on approve/edit
When the user approves, edits, or writes from scratch, the result feeds back into the foundation.

```typescript
// ✅ Correct
async function approvePost(postId, userId, finalText) {
  await db.posts.update(postId, { status: 'approved', final_text: finalText });
  
  // Ingest back into foundation
  await foundation.ingest({
    locationId: post.location_id,
    text: finalText,
    source: post.was_edited ? 'social_edited' : 'social_approved',
    platform: post.platform,
    bucket: post.bucket,
    editDistance: post.was_edited ? computeEditDistance(post.ai_draft, finalText) : null
  });
}

// ❌ Wrong — approval without ingestion
async function approvePost(postId, userId, finalText) {
  await db.posts.update(postId, { status: 'approved' });
  // Foundation never grows from this user's approved work
}
```

### Checkpoint 4: Logging
Every generation logs to `audit_log` with the taskType, model used, foundation samples retrieved (IDs, not content for PII), and result length. This is non-negotiable for debugging and future training data analysis.

### Checkpoint 5: Error handling for missing blueprint
If a user hits a generator before completing Blueprint intake, the generator must fail gracefully with a clear message routing them back to Blueprint, not produce generic output.

```typescript
try {
  const context = await getBrandContext({ ... });
} catch (e) {
  if (e instanceof BrandContextError) {
    throw new GeneratorError('Foundation not ready', {
      userMessage: "Brick needs your blueprint before he can write in your voice. Complete Blueprint setup first.",
      redirectTo: '/blueprint'
    });
  }
  throw e;
}
```

## The retrieval query construction

The query text used for similarity search matters. A bad query retrieves irrelevant samples and the voice match suffers. The pattern:

```typescript
function buildRetrievalQuery({ taskType, topic, platform, audience }): string {
  const parts = [];
  
  // Task framing
  if (platform) parts.push(`${platform} ${taskType.replace('_', ' ')}`);
  else parts.push(taskType.replace('_', ' '));
  
  // Topic (most important signal)
  if (topic) parts.push(`about ${topic}`);
  
  // Audience signal
  if (audience) parts.push(`for ${audience}`);
  
  return parts.join(' ');
}
```

Examples:
- LinkedIn post about FHA loan limits → `"linkedin linkedin post about FHA loan limits"`
- Instagram caption for Rountree home tour → `"instagram instagram caption about Rountree home tour"`
- Sponsor pitch for DealCheck → `"sponsor pitch about DealCheck"`

The query is logged in `metadata.retrievalQuery` for debugging. If a generation feels off, check the query first.

## Platform-aware retrieval

When a `platform` is specified, the SQL filter prefers samples from that platform but doesn't *require* them. The fallback to cross-platform samples matters because:

- A new user has few samples per platform; cross-platform fallback prevents thin retrieval
- Some content topics span platforms naturally
- The user's overall voice is consistent across platforms even if cadence varies

The exact filter:
```sql
WHERE (platform IS NULL OR platform = $platform OR $platform IS NULL)
```

This returns: samples explicitly for the target platform + samples not tagged to any platform + all samples if no platform filter. The weight multiplier in `ORDER BY` ensures same-platform samples rank higher when both are available.

## Weighting

Samples have a `weight` field that biases retrieval. Higher weight = preferred sample even when slightly less topically similar. The standard weights at ingestion:

| Source | Weight | Reasoning |
|---|---|---|
| `written_from_scratch` | 1.5 | Strongest voice signal — user typed it themselves |
| `social_edited` (high edit distance) | 1.3 | User corrected the AI — gold for voice training |
| `podcast` | 1.2 | Real speaking voice |
| `social_approved` | 1.0 | Baseline approved content |
| `brand_studio` | 0.8 | More formal, less natural |
| `historical` (>12 months) | linearly decays | Old voice may not match current voice |

Users can manually `promote` a sample (weight × 2) or `exclude` it (weight = 0) via Blueprint's Foundation panel.

## The score calculation (for the Foundation panel)

The "78% match" score in the UI isn't decorative. Calculate it weekly:

```typescript
async function calculateFoundationScore(locationId) {
  // Sample 20 recent AI outputs the user approved
  const aiOutputs = await db.query(`
    SELECT final_text 
    FROM posts 
    WHERE location_id = $1 AND status='published' 
      AND source='post_forge'
    ORDER BY published_at DESC LIMIT 20
  `, [locationId]);
  
  if (aiOutputs.length < 5) return null; // not enough data
  
  // Sample 20 user-written samples
  const userWritten = await db.query(`
    SELECT text FROM voice_samples
    WHERE location_id = $1 
      AND source IN ('written_from_scratch', 'social_edited')
    ORDER BY created_at DESC LIMIT 20
  `, [locationId]);
  
  if (userWritten.length < 5) return null;
  
  // For each AI output, find nearest neighbor in user-written
  const similarities = [];
  for (const ai of aiOutputs) {
    const aiEmbed = await embeddings.embed(ai.final_text);
    let bestSim = 0;
    for (const user of userWritten) {
      const userEmbed = await embeddings.embed(user.text); // can cache
      const sim = cosineSimilarity(aiEmbed, userEmbed);
      if (sim > bestSim) bestSim = sim;
    }
    similarities.push(bestSim);
  }
  
  // Average
  const score = similarities.reduce((a, b) => a + b, 0) / similarities.length;
  
  await db.foundation_scores.insert({
    location_id: locationId,
    score,
    sample_count: userWritten.length,
    computed_at: new Date()
  });
  
  return score;
}
```

## Anti-patterns to grep for

After implementing any generator, run these to verify Foundation compliance:

```bash
# Direct LLM client imports outside Foundation/Generation service
grep -r "import.*anthropic" src/ | grep -v "src/foundation\|src/generation"
grep -r "import.*openai" src/ | grep -v "src/foundation\|src/generation\|embeddings"

# Direct messages.create calls outside Generation service
grep -r "messages.create\|chat.completions" src/ | grep -v "src/generation"

# Missing getBrandContext in generator functions
grep -r "async function generate" src/ -A 10 | grep -B 1 -A 10 "anthropic\|openai" | grep -v "getBrandContext"
```

Any hit outside the allowed paths is a violation that needs to be refactored before shipping.

## The "Foundation not ready" experience

A new user who hasn't completed Blueprint intake hits a generator. What should happen?

❌ **Wrong:** Generator produces generic output anyway. User thinks PodClick is mediocre.

✅ **Right:** Generator throws `BrandContextError`. UI shows a Brick message: *"Need your blueprint first — I can't write in your voice until I know your voice. Takes 10 minutes."* Button: "Pour the foundation."

This converts a friction moment into a value moment. The user understands *why* Blueprint matters because they tried to skip it and PodClick stopped them with a concrete reason.

## Summary: the one-sentence contract

**Every LLM call that produces user-attributed content passes through `getBrandContext()` first, retrieves 3-5 voice samples, injects them as few-shot examples, and logs the generation for foundation feedback loops.**

If you can read a generator's code and find an LLM call without `getBrandContext()` two lines above it, that generator is broken. Fix it.
