# REOS SuperApp Concept

A standalone, non-production prototype for a more proactive, resilient REOS. It keeps the operator’s work and ATLAS visible at the same time, then turns the boundary between them into a live continuity rail: evidence enters, ATLAS acts, blocked paths recover, and every material result produces a receipt.

## Run it

```bash
cd reos-superapp-concept
bun run serve
```

Open `http://localhost:4173`.

## Verify it

```bash
cd reos-superapp-concept
bun run check
```

## Prototype interactions

- Switch among deals in the left rail and watch the focused truth state update.
- Complete queue items in the center operations canvas.
- Open **Review 3 moves**, approve a proposed action, and close the drawer.
- Undo the recovered ATLAS receipt.
- Ask ATLAS a question; the local demo responds with evidence-aware copy.
- Open **Signals** to preview daily, weekly, product-update, and event-risk email classes.

## Boundaries

This package does not call production APIs, send email, modify real transactions, or deploy to myrealestateos.com. It exists to make the product direction tangible enough to critique before production integration.
