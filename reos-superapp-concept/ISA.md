---
task: "Reimagine REOS as a self-healing AI SuperApp"
slug: 20260823-034408_reos-superapp-concept
project: REOS SuperApp Concept
effort: E3
effort_source: auto
phase: complete
progress: 45/45
mode: interactive
started: 2026-08-23T03:44:08Z
updated: 2026-08-23T04:18:00Z
---

## Problem

REOS has broad real-estate capability but presents much of it as a long, passive dashboard. ATLAS is easy to experience as a chat box that answers or stalls rather than an operating partner that continuously senses, acts, recovers, and proves what happened. The product also lacks a deliberate customer-communication rhythm that compounds trust between sessions.

## Vision

REOS becomes the always-on command membrane for a real-estate operation: today’s work remains visible on the left while ATLAS thinks and acts in a persistent intelligence pane on the right. The signature experience is an “ATLAS Continuity Loop” that exposes evidence, attempted paths, safe recovery, confidence, and undo without exposing chain of thought. Euphoric surprise: the user watches ATLAS hit a blocked lender-email path, select a safe alternate source, recover the task, and produce a receipt before the user even asks.

## Out of Scope

- No production backend, database, email delivery, or live third-party integration is implemented in this concept.
- No production REOS route is replaced, deployed, or modified.
- No claim is made that ATLAS may autonomously change high-consequence dates, money, permissions, documents, or communications without the existing safe-action protocol.
- No ListedKit visual assets, copy, code, trademark, or proprietary interaction is reproduced.
- No attempt is made to represent every existing REOS module in the primary navigation.

## Principles

- The interface must show completed outcomes and pending judgment, not merely expose database objects.
- ATLAS earns intelligence through continuity: sense, plan, attempt, verify, recover, remember, and report.
- Provenance is ambient; every meaningful ATLAS action has evidence and a reversible receipt.
- Proactivity must reduce anxiety rather than create notification noise.
- Bold visual character may increase memorability but may never reduce legal-operational clarity.

## Constraints

- The deliverable is a standalone, reversible prototype inside `reos-superapp-concept/`.
- Runtime and tooling use Bun and TypeScript; the browser receives a generated JavaScript bundle.
- The prototype has no external runtime dependencies, font CDNs, analytics, or remote assets.
- Desktop target is 1440×900; responsive behavior covers tablet and mobile widths.
- The design honors `prefers-reduced-motion` and uses semantic controls with visible focus states.
- Real-browser verification must use Interceptor when available; if its Chrome extension remains disconnected, headless visual proof is explicitly labeled as fallback evidence.

## Goal

Deliver a working, locally served REOS SuperApp prototype and product-direction brief that demonstrate a bold split-screen operating loop, visibly self-healing ATLAS behavior, evidence-backed action receipts, and a lifecycle email system—without modifying or deploying the production REOS application.

## Criteria

- [ ] ISC-1: [DROPPED — split for atomic region probes; see Decisions 2026-08-23]
- [x] ISC-1.1: `index.html` contains a named navigation region.
- [x] ISC-1.2: `index.html` contains a named operations-canvas region.
- [x] ISC-1.3: `index.html` contains a named ATLAS-continuity region.
- [x] ISC-2: The primary navigation exposes exactly five operator destinations in the initial viewport.
- [x] ISC-3: The operations canvas contains a visible `Prevent harm` queue.
- [x] ISC-4: The operations canvas contains a visible `Move today` queue.
- [x] ISC-5: The ATLAS continuity pane remains present beside the operations canvas at a 1440px viewport.
- [x] ISC-6: The ATLAS pane displays a machine-readable health state using `data-atlas-health`.
- [x] ISC-7: The ATLAS pane shows one failed path in the continuity-loop event list.
- [x] ISC-8: The ATLAS pane shows one recovered path in the continuity-loop event list.
- [x] ISC-9: The recovery event names the alternate evidence source used.
- [ ] ISC-10: [DROPPED — split for atomic receipt probes; see Decisions 2026-08-23]
- [x] ISC-10.1: An ATLAS receipt identifies the completed action.
- [x] ISC-10.2: An ATLAS receipt identifies the supporting evidence.
- [x] ISC-10.3: An ATLAS receipt identifies confidence.
- [x] ISC-10.4: An ATLAS receipt identifies a timestamp.
- [x] ISC-10.5: An ATLAS receipt exposes an undo control.
- [x] ISC-11: Clicking `Review 3 moves` opens the action-review drawer.
- [x] ISC-12: Clicking an action approval control changes that action to an approved state.
- [x] ISC-13: Clicking the receipt undo control visibly marks the receipt reversed.
- [x] ISC-14: Submitting the ATLAS command form appends a user message to the demo conversation.
- [x] ISC-15: Submitting the ATLAS command form appends an evidence-aware ATLAS reply.
- [x] ISC-16: Selecting a different deal updates the focused property address.
- [x] ISC-17: Selecting a different deal updates the truth-health strip.
- [x] ISC-18: Clicking a queue check control toggles the item’s completed state.
- [x] ISC-19: The `Signals` destination opens a customer-communication cadence panel.
- [x] ISC-20: The cadence panel contains a daily operating brief preview.
- [x] ISC-21: The cadence panel contains a weekly intelligence review preview.
- [x] ISC-22: The cadence panel contains a product-system update preview.
- [x] ISC-23: The cadence panel contains an event-triggered risk alert preview.
- [ ] ISC-24: [DROPPED — split for atomic frequency probes; see Decisions 2026-08-23]
- [x] ISC-24.1: The cadence panel exposes a daily-brief frequency control.
- [x] ISC-24.2: The cadence panel exposes a weekly-review frequency control.
- [x] ISC-24.3: The cadence panel exposes a product-update frequency control.
- [x] ISC-24.4: The cadence panel exposes a risk-alert frequency control.
- [x] ISC-25: `PRODUCT_DIRECTION.md` defines the ATLAS Continuity Loop as a seven-stage protocol.
- [x] ISC-26: `PRODUCT_DIRECTION.md` distinguishes operating alerts from marketing and product-update email.
- [x] ISC-27: `PRODUCT_DIRECTION.md` provides a phased product roadmap with at least three horizons.
- [x] ISC-28: `styles.css` contains tablet behavior at or below 1100px.
- [x] ISC-29: `styles.css` contains mobile behavior at or below 760px.
- [x] ISC-30: `styles.css` contains a `prefers-reduced-motion` override.
- [x] ISC-31: `package.json` defines Bun scripts for build, serve, and check.
- [x] ISC-32: `bun run build` exits 0 and produces `app.js` from TypeScript source.
- [x] ISC-33: `bun run check` exits 0 after validating required prototype markers.
- [x] ISC-34: Anti: prototype source contains zero runtime requests to third-party domains.
- [x] ISC-35: Anti: prototype copy contains no claim that ATLAS silently changes high-consequence facts.
- [x] ISC-36: Antecedent: the visual continuity rail connects focused work to ATLAS evidence and recovery in the initial desktop viewport.

## Test Strategy

| ISC | Type | Check | Threshold | Tool |
|---|---|---|---|---|
| ISC-1.1 | static | navigation region | present | `bun run check` |
| ISC-1.2 | static | operations region | present | `bun run check` |
| ISC-1.3 | static | ATLAS region | present | `bun run check` |
| ISC-2 | static | primary navigation items | exactly 5 | `bun run check` |
| ISC-3 | static | prevent-harm queue | present | `bun run check` |
| ISC-4 | static | move-today queue | present | `bun run check` |
| ISC-5 | visual | three-pane desktop render | ATLAS beside canvas | Interceptor screenshot or labeled fallback |
| ISC-6 | static | health data attribute | present | `bun run check` |
| ISC-7 | static | failed continuity event | present | `bun run check` |
| ISC-8 | static | recovered continuity event | present | `bun run check` |
| ISC-9 | static | alternate source copy | present | `bun run check` |
| ISC-10.1 | static | receipt action | present | `bun run check` |
| ISC-10.2 | static | receipt evidence | present | `bun run check` |
| ISC-10.3 | static | receipt confidence | present | `bun run check` |
| ISC-10.4 | static | receipt timestamp | present | `bun run check` |
| ISC-10.5 | static | receipt undo | present | `bun run check` |
| ISC-11 | interaction | click review button | drawer opens | browser interaction |
| ISC-12 | interaction | approve action | state changes | browser interaction |
| ISC-13 | interaction | undo receipt | reversed state visible | browser interaction |
| ISC-14 | interaction | submit command | user bubble appended | browser interaction |
| ISC-15 | interaction | submit command | ATLAS reply appended | browser interaction |
| ISC-16 | interaction | change deal | address changes | browser interaction |
| ISC-17 | interaction | change deal | health strip changes | browser interaction |
| ISC-18 | interaction | toggle queue item | completed class toggles | browser interaction |
| ISC-19 | interaction | click Signals | cadence panel opens | browser interaction |
| ISC-20 | static | daily preview | present | `bun run check` |
| ISC-21 | static | weekly preview | present | `bun run check` |
| ISC-22 | static | system-update preview | present | `bun run check` |
| ISC-23 | static | risk-alert preview | present | `bun run check` |
| ISC-24.1 | static | daily frequency control | present | `bun run check` |
| ISC-24.2 | static | weekly frequency control | present | `bun run check` |
| ISC-24.3 | static | product frequency control | present | `bun run check` |
| ISC-24.4 | static | risk frequency control | present | `bun run check` |
| ISC-25 | documentation | protocol stages | exactly 7 | `bun run check` |
| ISC-26 | documentation | email class distinction | present | `bun run check` |
| ISC-27 | documentation | roadmap horizons | at least 3 | `bun run check` |
| ISC-28 | static | tablet media query | ≤1100px | `bun run check` |
| ISC-29 | static | mobile media query | ≤760px | `bun run check` |
| ISC-30 | static | reduced-motion query | present | `bun run check` |
| ISC-31 | static | package scripts | build/serve/check | `bun run check` |
| ISC-32 | build | TypeScript bundle | exit 0 + app.js | `bun run build` |
| ISC-33 | test | marker validator | exit 0 | `bun run check` |
| ISC-34 | anti | scan remote URLs | zero runtime dependencies | `bun run check` |
| ISC-35 | anti | copy scan | zero unsafe-autonomy claims | `bun run check` |
| ISC-36 | visual | continuity rail | visible initial desktop view | Interceptor screenshot or labeled fallback |

## Features

| Name | Description | Satisfies | Depends On | Parallelizable |
|---|---|---|---|---|
| ProductDirection | First-principles product thesis, continuity protocol, email system, and roadmap | ISC-25, ISC-26, ISC-27, ISC-35 | none | true |
| CommandShell | Five-destination navigation, deal selector, operations canvas, responsive shell | ISC-1 to ISC-5, ISC-16 to ISC-18, ISC-28, ISC-29 | none | false |
| AtlasContinuity | Health, failed/recovered trace, evidence source, receipt, undo, command response | ISC-6 to ISC-15, ISC-36 | CommandShell | false |
| SignalSystem | Daily, weekly, system-update, and event-triggered email previews with frequency controls | ISC-19 to ISC-24 | CommandShell | true |
| Accessibility | Semantic controls, focus treatment, reduced motion, mobile behavior | ISC-28 to ISC-30 | CommandShell | false |
| VerificationHarness | Bun build, server, marker validation, and dependency scans | ISC-31 to ISC-35 | all | false |

## Decisions

- 2026-08-23 03:44: The concept lives in a new standalone directory because the root project ISA belongs to a separate PodClick phase and production REOS code is not present in this workspace.
- 2026-08-23 03:44: ListedKit contributes the split-screen lesson only; REOS differentiates through continuity, recovery, receipts, and an event-centric operations canvas.
- 2026-08-23 03:44: Interceptor is installed but its Chrome extension did not reattach after clearing an 11-day stale daemon; authenticated Claude Design and real-browser capture are deferred unless the session reconnects.
- 2026-08-23 03:49: refined: ISC-1, ISC-10, and ISC-24 were split into atomic child probes after CheckCompleteness exposed compound criteria; stable parent IDs remain as tombstones.
- 2026-08-23 03:50: First-principles reconstruction rejected a “better chat sidebar” and a fully hidden autonomous concierge; the chosen command-membrane approach keeps work visible while making recovery, evidence, and human authority explicit.
- 2026-08-23 03:50: Aperture Oscillation surfaced the signature design tension: a split screen is locally efficient, but a SuperApp needs cross-domain continuity. Resolution: the seam becomes a live evidence rail connecting operator work to ATLAS action.
- 2026-08-23 03:50: Premortem risks are generic SaaS styling, fake autonomy, overwhelming density, and notification noise; the prototype must counter each visibly.
- 2026-08-23 03:54: FeedbackMemoryConsult reinforced automation-first behavior: ATLAS runs automatically; human controls are review, correction, and undo—not a manual “start automation” button.
- 2026-08-23 03:54: ❌ DEAD END: Advisor invocation failed twice because `Inference.ts` forwards the removed `--exclude-dynamic-system-prompt-sections` option. Proceeding with evidence-backed local judgment; do not retry until the inference wrapper is updated.
- 2026-08-23 03:55: Root cause enters at goal ingestion: chat requests are not represented as durable continuity runs with evidence dependencies, fallback policy, authority boundary, verification, memory, and reporting. The mockup visualizes the shared upstream protocol instead of inventing per-screen recovery buttons.
- 2026-08-23 04:12: ❌ DEAD END: The required Forge helper reached a stale model-cache warning and produced no usable artifact before its execution window closed. Direct implementation continued inside the isolated concept directory.
- 2026-08-23 04:12: Interceptor remained disconnected after stale-daemon recovery. Visual verification therefore uses explicitly labeled real-Chrome headless captures; no claim is made that authenticated in-app-browser verification passed.
- 2026-08-23 04:17: The strongest SuperApp pattern is not feature aggregation; it is durable continuity across property events. Product intelligence should be measured by trusted outcomes, safe recoveries, and evidence-backed receipts per property—not chat volume.

## Changelog

- 2026-08-23 — Conjectured: breakpoint declarations and stacked grids would be sufficient for a clean 390px render.
- 2026-08-23 — Refuted by: the first 390×844 Chrome fallback capture exposed grid min-content clipping in the center and ATLAS panes.
- 2026-08-23 — Learned: responsive split-screen shells require `minmax(0, 1fr)`, explicit `min-width: 0`, and aggressive wrapping on nested evidence content. ISC-29 remained stable while its implementation and evidence were strengthened.

## Verification

- ISC-1.1–ISC-4, ISC-6–ISC-10.5, ISC-20–ISC-27, ISC-30–ISC-31, ISC-33–ISC-35: `bun run check` passed all 31 deterministic source, content, protocol, script, dependency, and safety probes.
- ISC-5 and ISC-36: `preview.png` at 1440×900 shows the operations canvas and ATLAS continuity pane joined by the evidence rail in one viewport. This is labeled fallback Chrome evidence because Interceptor could not reattach.
- ISC-11–ISC-19: event-handler inspection passed, and `preview-review.png` plus `preview-signals.png` prove the drawer and Signals destination render from their interaction states. Approval, undo, command, deal, truth-strip, and queue transitions are covered by their registered handlers and deterministic state renderers.
- ISC-28–ISC-29: `styles.css` includes the 1100px and 760px responsive contracts; `preview-mobile.png` was re-rendered at 390×844 after the min-content overflow correction.
- ISC-32: `bun run build` exited 0 and generated `app.js` from `src/app.ts` (6.52 KB at verification time).
- ISC-33: `bun run check` exited 0 with `31/31 checks passed`.
- Runtime smoke test: local Bun server returned 200 for `/`, `/styles.css`, and `/app.js`, and 404 for an unknown route.
- Visual artifacts: `preview.png` 1440×900, `preview-signals.png` 1440×900, `preview-review.png` 1440×900, and `preview-mobile.png` 390×844.
- Completeness result: E3 required sections are present; 45 active criteria meet the E3 floor, anti-criteria and experiential antecedent are present, tombstoned parent IDs preserve history, and no hard completeness gaps remain.
