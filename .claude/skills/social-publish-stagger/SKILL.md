---
name: social-publish-stagger
description: Use this skill whenever PodClick is about to publish or schedule social posts across multiple platforms or multiple users at the same scheduled time. Covers the queue-based stagger that prevents GHL rate-limit throttling (100 requests per 10 seconds per app per location), platform-specific timing offsets, and the retry-on-failure pattern. Apply when implementing the SocialService.publish or SocialService.schedule methods, when designing the background job runner for scheduled posts, or when fanning out a single content piece (an episode clip, a Post Forge output, a calendar batch) to multiple platforms or accounts.
---

# Social publish stagger

The core problem: GHL's rate limit is 100 requests per 10 seconds per app per location. A naive "publish 10 platforms × 50 users at 8:00am" creates a 500-request burst that gets throttled, posts fail silently, users blame PodClick. This skill encodes the fix.

## The stagger pattern

Every publish call goes through a job queue (Bull on Node, Sidekiq on Ruby, Celery on Python — pick the one matching the stack). The queue applies three layers of stagger:

1. **Per-user platform stagger** — within one user's publish, space platforms 5-10 seconds apart. Don't fire all platforms simultaneously.
2. **Global concurrency cap** — no more than N concurrent publish jobs across all users at any moment. Start with N=8.
3. **Jitter on shared schedule times** — when many users schedule "8:00am Tuesday," spread actual execution across a ±90 second window so the load smooths.

## Per-platform default offsets

These are the within-user offsets from the user's requested schedule time. They are deliberate — different platforms reward different posting moments, and clustering activity across platforms boosts algorithm reach.

```
linkedin   +0s     (lead with LinkedIn — most professional)
x          +60s    (snappy second hit)
facebook   +120s
instagram  +180s   (Reels/feed)
tiktok     +240s
youtube    +300s   (Shorts last — algorithm rewards being the "final stop")
gmb        +360s
```

Total per-user spread: 6 minutes for a full 7-platform fanout. Calendar shows the user "8:00am" as the schedule time; the system fans out 8:00, 8:01, 8:02, etc. The user doesn't see this complexity — they see one publish event.

## Global concurrency cap

The job queue is configured with `concurrency: 8` by default. Tune up to 12 if rate limits show headroom, down to 4 if you start seeing 429s. The cap applies across all users — not per user.

```typescript
const publishQueue = new Queue('social-publish', {
  defaultJobOptions: {
    attempts: 4,
    backoff: { type: 'exponential', delay: 30000 }
  },
  limiter: {
    max: 8,
    duration: 10000
  }
})
```

## Jitter on shared schedule times

When `scheduleAt` falls within a popular minute (any hour:00, hour:30), add deterministic jitter based on user ID hash so the same user always gets the same offset but the global load smooths:

```typescript
function jitterFor(userId: string, scheduleAt: Date): number {
  const popularMinutes = [0, 15, 30, 45]
  const min = scheduleAt.getMinutes()
  if (!popularMinutes.includes(min)) return 0
  const hash = hashString(userId)
  return (hash % 180) - 90
}
```

±90 seconds of jitter around popular times. Deterministic per user (debugging-friendly), random-looking in aggregate.

## Retry-on-failure pattern

GHL calls fail for reasons including: token expired (401), rate-limited (429), platform-side upstream failure (502/503), payload validation error (400). Each gets different handling:

| Status | Treatment | Retries |
|--------|-----------|---------|
| 401 | Refresh token, retry once. If refresh fails, mark connection broken and notify user via Brick | 1 |
| 429 | Exponential backoff starting at 30s | 4 |
| 5xx | Exponential backoff starting at 30s | 4 |
| 400 | No retry — payload is wrong, log and surface to user. Brick says: "your LinkedIn post had an unsupported character — want me to clean it?" | 0 |
| network timeout | Exponential backoff starting at 10s | 4 |

Never retry 400s. Never silently retry forever. After max attempts, write the failure to a `publish_failures` table and trigger Brick notification.

## Job lifecycle hooks

Three observability hooks every publish job emits:

- `publish.requested` — when job enters queue (Calendar fires this when scheduling)
- `publish.attempted` — when job picks up off queue and calls GHL
- `publish.completed` — terminal state (success or final failure)

Hook into these for analytics dashboards, Brick context, and the calendar's status badges. Don't poll GHL constantly to find out what happened — your own queue events are the source of truth for PodClick.

## Implementation order

When building or refactoring publish:

1. Wire the queue with concurrency cap. Verify a single publish through it works end-to-end.
2. Add per-platform offsets. Verify a 7-platform fanout staggers correctly in logs.
3. Add jitter for popular minutes. Test by scheduling 20 fake users for the same 8:00am and watching actual fire times spread.
4. Add retry handling per status code.
5. Wire the lifecycle hooks.

## Anti-patterns to avoid

- **Don't `Promise.all` the platform fan-out.** That parallelizes the burst — exactly what stagger prevents.
- **Don't skip the queue for "publish now" requests.** Even immediate publishes route through the queue; concurrency cap protects you.
- **Don't show the user the stagger.** The calendar shows one schedule time. The complexity is invisible.
- **Don't retry 400 errors.** They never succeed on retry. Surface them to Brick instead.
- **Don't lose jobs on deploy.** Use a queue with persistent storage (Redis-backed Bull, etc) so in-flight publishes survive restarts.

## When to revisit

Revisit the stagger constants when: (a) you start hitting 429s in production, (b) GHL changes rate limits, (c) you add a new platform, (d) user volume grows past ~500 active publishers — at that scale you may need multiple registered apps to multiply rate-limit headroom.
