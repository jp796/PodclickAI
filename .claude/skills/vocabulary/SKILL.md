---
name: vocabulary
description: Use this skill any time PodClick generates, writes, or refactors user-facing strings — UI labels, button text, screen titles, navigation items, error messages, email subjects and bodies, push notifications, onboarding copy, empty states, success confirmations, and any other text the user reads in the app or in PodClick-sent communications. Apply whenever building new screens, refactoring existing screens, writing prompt templates that produce UI copy, generating onboarding content, drafting marketing copy for the GHL Marketplace listing, or reviewing legacy strings during phase refactors. Do NOT use this skill for the user's brand voice (handled by foundation-retrieval) or for Brick's character voice (handled by brick-voice).
---

# Vocabulary — construction language enforcement

PodClick speaks one language: construction. This is the most important brand decision in the entire product. Every screen, every label, every button, every email must use the canonical vocabulary in this skill. Drift kills the metaphor; consistency makes it ownable.

The reason this matters more than typical product copy guidelines: PodClick's audience is real estate agents who already think in construction terms (blueprint, foundation, closing, walk-through, punch list, comps). Speaking their language makes the product feel *built for them*. Speaking generic SaaS English makes PodClick feel like every other AI tool. The vocabulary is positioning, not decoration.

## The canonical term table

This is the source of truth. When writing any user-facing string, use the right column, never the left.

### Concepts

| Generic term | PodClick term |
|---|---|
| AI assistant / AI agent / bot / chatbot | **Brick** |
| Brand profile / brand kit / brand settings | **Blueprint** |
| Voice fingerprint / AI training / voice corpus | **Foundation** |
| Voice match score / AI similarity | **Foundation match** |
| Dashboard / home / overview | **Walk-through** (daily report) or **Job Site** (project overview) |
| Approval queue / pending tasks / review items | **Punch list** |
| Autonomy level / AI permissions / automation settings | **Permit** |
| Content campaign / project / initiative | **Project** |
| Episode launch / publishing | **Closing** |
| Competitor analysis / trending content / market research | **Comps** |
| AI specialists / agents | **The Crew** |
| Tasks completed / activity log | **Built** (as in "built overnight") |

### Crew members

| Function | Crew name |
|---|---|
| YouTube market analyzer | **Scout** |
| Social post writer | **Draftsman** |
| Script/outline builder | **Framer** |
| Thumbnail / cover generator | **Painter** |
| Publishing coordinator | **Dispatcher** |
| Analytics / performance tracker | **Inspector** |

### Permit tiers (autonomy levels)

| Level | Tier name |
|---|---|
| 0 — Off | **Owner-Builder** |
| 1 — Suggest only | **Draftsman** |
| 2 — Draft and queue | **Bricklayer** |
| 3 — Auto-execute, notify | **Foreman** |
| 4 — Full autonomy | **General Contractor** (or **GC** in casual) |

### Status states

| Generic | PodClick |
|---|---|
| Draft | Draft |
| Scheduled | Scheduled (or "lined up") |
| Publishing | Closing (in progress) |
| Published | Live (or "closed") |
| Failed | Stalled |
| Cancelled | Pulled |

### Action verbs

| Generic verb | PodClick verb |
|---|---|
| Publish | Close (formal) / Ship it (informal) |
| Schedule | Line up / Set the closing |
| Generate | Draft / Lay |
| Approve | Sign off / Approve (acceptable) |
| Reject | Send back |
| Edit | Adjust / Rework |
| Delete | Tear down (for projects) / Remove (for items) |
| Reset | Re-lay |

## Banned terms

These never appear in user-facing PodClick strings. Use grep to enforce.

### Hard bans (zero tolerance)

- "AI-powered" / "AI-driven" / "powered by AI"
- "Leverage" / "leveraged"
- "Synergy" / "synergistic"
- "Unlock" / "unlocking"
- "Empower" / "empowering"
- "Cutting-edge" / "state-of-the-art" / "best-in-class" / "world-class"
- "Game-changer" / "game-changing"
- "Disrupt" / "disruptive"
- "Revolutionize" / "revolutionary"
- "Seamless" / "seamlessly"
- "Frictionless"
- "Robust"
- "Holistic"
- "End-to-end" (as marketing-speak, not technical)
- "At your fingertips"
- "Take your X to the next level"
- "Supercharge"
- "Effortless" / "effortlessly"

### Soft bans (avoid unless context forces)

- "Settings" → prefer specific page name ("Permit," "Blueprint")
- "Dashboard" → prefer "Walk-through" or "Job Site"
- "Notification" → prefer "Brick says..." or "Update from the site"
- "Configure" → prefer "Set up" or specific verb
- "Manage" → prefer specific verb (review, adjust, approve)
- "Workflow" → prefer "Project" or "Build"
- "Onboarding" → prefer "Setup" or "Pour the foundation"
- "Account" → user-facing should usually be "Site" or specific
- "Login" / "Sign in" → acceptable but plain English preferred
- "Submit" → prefer specific verb (Ship it, Send, Close)
- "Click here" → never; always describe the destination

### Banned in error/empty states

- "Oops!" / "Whoops!" / "Uh-oh"
- "Something went wrong"
- "Please try again later"
- "We're experiencing technical difficulties"
- Any apology longer than 5 words
- Emoji in error states (single 🔨 is the exception for Brick error voice)

## Approved vocabulary (preferred phrasing)

These read as natural construction-language and should be used when natural:

### Site-flavored phrases

- "On the site" (= in PodClick / active)
- "Off the site" (= paused / inactive)
- "Site plan" (= today's schedule)
- "Site report" (= analytics)
- "Pour the foundation" (= initial setup)
- "Lay the brick" (= produce/create content)
- "Frame it up" (= build the structure of)
- "Roughed in" (= drafted but not finalized)
- "Punch out" (= clear the punch list)
- "Walk the site" (= review progress)
- "Solid build" / "clean build" (= compliment on output)
- "The foreman called it" (= Brick made the decision)

### Project lifecycle phrases

- "Breaking ground" (= starting a project)
- "Framed" (= structure done, content drafted)
- "Almost closed" (= ready to publish)
- "Closing day" (= publish day)
- "Closed clean" (= published without issues)
- "Punch list cleared" (= all approvals done)

### Time/schedule phrases

- "Lined up for Tuesday" (= scheduled)
- "Pour starts at 8am" (= publication begins)
- "End of shift" (= end of day)
- "Tomorrow's site plan"
- "This week's build"

## Application by surface

Different surfaces need slightly different register. Use the appropriate tone.

### Navigation labels (terse)

```
Walk-through
Job Site
Studio
Blueprint
Foundation
Crew
Calendar
Permit
```

Not:

```
Dashboard
My Projects
Recording
Brand Settings
AI Training
Tools
Schedule
Settings
```

### Button text (action-first, short)

```
Ship it
Close it
Sign off
Send back
Line it up
Tear it down
Pour the foundation
Promote Brick
```

Not:

```
Submit
Publish
Approve
Reject
Schedule
Delete
Get Started
Upgrade Permissions
```

### Empty states

```
No projects on the site yet. Hit "Break ground" to start one.
```

Not:

```
You don't have any projects yet! Click here to create your first project.
```

### Success confirmations

```
Closed clean. Live across 5 platforms.
```

Not:

```
🎉 Success! Your post has been published successfully across all platforms!
```

### Error states

```
LinkedIn token expired. 30 seconds to reconnect — here's the button.
```

Not:

```
We're sorry! It looks like there was an issue with your LinkedIn connection. Please try reconnecting your account.
```

### Onboarding step titles

```
Step 1 — Lay the foundation
Step 2 — Draft the blueprint
Step 3 — Meet Brick
Step 4 — Break ground on your first project
```

Not:

```
Step 1 — Set up your account
Step 2 — Configure your brand
Step 3 — Introduction to AI
Step 4 — Create your first content
```

### Email subject lines

For walk-through digests:
```
Walk-through · {date} — {count} on the punch list
```

For closing notifications:
```
EP 101 closed clean
```

For permit ceremony:
```
You're handing me the keys.
```

Not:

```
Your daily AI content briefing
Your post has been published!
Upgrade your AI permissions
```

## Application by audience tier

### Solo realtor (default)

Use full construction vocabulary unapologetically. They get the references because they live in the industry every day.

### Agency owner (white-label admin)

Slightly more formal register but keep the metaphor. Agency owners manage multiple "sites" (their clients), so construction language scales naturally:

```
Sites under your watch (5)
Active builds across your portfolio
Punch list rollup
```

### End user of a white-labeled instance

If an agency white-labels PodClick, the agency might want to soften the metaphor for their clients. The white-label config should allow agencies to *choose vocabulary intensity* (full construction / moderate / minimal). Default to full; let agencies tone down for non-real-estate audiences.

## When generating new copy via AI

When Claude Code uses an LLM to generate user-facing strings (onboarding flows, error messages, etc.), the prompt must include this vocabulary skill as a constraint. The pattern:

```
SYSTEM:
You are writing UI copy for PodClick. Apply the vocabulary skill strictly.

REQUIRED VOCABULARY:
- Use "Blueprint" not "Brand Profile"
- Use "Foundation" not "Voice Training"
- Use "Walk-through" not "Dashboard"
- Use "Punch list" not "Approvals"
- Use "Permit" not "Settings"
- Use "Closing" not "Publish"
- Use "Project" not "Campaign"

BANNED TERMS (never use):
{full banned list}

REGISTER:
Direct, plain English. No corporate-speak. No exclamation marks unless one per multi-sentence message max. No emoji in operational copy.

TASK: {specific copy generation task}
```

## Grep-based enforcement

Run these greps after any phase or major refactor. Each hit is a violation to fix:

```bash
# Hard bans
grep -ri "AI-powered\|leverage\|synergy\|unlock\|empower" src/ \
  --include="*.tsx" --include="*.ts" --include="*.jsx" --include="*.js" \
  | grep -v "node_modules\|test\|spec"

# Soft bans in user-facing files
grep -ri "dashboard\|settings" src/app/ src/components/ \
  --include="*.tsx" --include="*.ts" \
  | grep -v "test\|spec\|comment"

# Generic SaaS words
grep -ri "cutting-edge\|state-of-the-art\|game-changer\|revolutionary" src/ \
  --include="*.tsx" --include="*.ts"

# Banned in error states
grep -ri "Oops\|Whoops\|Uh-oh\|Something went wrong" src/ \
  --include="*.tsx" --include="*.ts"

# Banned button text patterns
grep -ri ">Submit<\|>Click here<" src/ \
  --include="*.tsx" --include="*.ts"
```

Each violation requires either rewriting the string or, if the original is technically correct in context (e.g., HTML `<button type="submit">` is fine — only the *display text* matters), confirming the visible string is compliant.

## When the metaphor doesn't fit

Editorial discipline: if a construction term doesn't have a natural analog for a concept, don't force it. Forced metaphors are worse than no metaphor. Examples:

✅ "Foundation" for voice fingerprint — natural mapping, the substrate everything is built on
✅ "Punch list" for approval queue — already real construction language for "items needing owner attention"
✅ "Closing" for publishing — perfect, since realtors literally do closings

❌ "Toolbelt" for user preferences — forced, no natural mapping
❌ "Hard hat" for safety/security settings — cute but doesn't add clarity
❌ "Cement mixer" for batch operations — adorable, but users will be confused

If a feature needs a name and construction language doesn't provide one cleanly, use plain English. The vocabulary is a system, not a costume.

## Voice and tone alongside vocabulary

Vocabulary is *what words* you use. Voice and tone are *how* you use them. PodClick's overall register, separate from Brick's specific character:

- **Direct, not breezy.** "Foundation match 78%." Not "Your AI is learning your voice so well! 🎉"
- **Specific, not vague.** "Lined up Tuesday 8am." Not "Coming soon."
- **Confident, not hedging.** "I'd lead with the FHA angle." Not "You might want to consider possibly..."
- **Concrete, not abstract.** "6 posts. 3 clips. EP 101 ready." Not "Productive overnight session!"
- **Respectful, not subservient.** "Your call." Not "Whatever you prefer!"

## The 30-second test

For every user-facing string, ask:
1. Does this use construction vocabulary where natural?
2. Does this avoid banned terms?
3. Would a realtor recognize the metaphor instantly?
4. Would a foreman say it this way?
5. Is it as short as it could be while staying clear?

If five yeses, ship. If any no, rewrite.

## Summary: the one-sentence contract

**Every user-facing string in PodClick uses construction vocabulary where natural, avoids banned generic-SaaS terms, and reads as something a working professional would say — not a marketing department.**

The metaphor is the brand. Hold the line.
