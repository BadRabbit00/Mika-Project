# Autonomous-life implementation work

Development uses the isolated `BlogAI-life` worktree from `ffd9c5b`. The running
checkout and production database must remain untouched. All execution and tests
use the Nix devShell and temporary databases. Graphify is refreshed after edits.

## Acceptance gates

1. Add storage and migration invariants before implementation: durable plans,
   non-overlapping activities, event/effect identities, pending chat deliveries.
2. Save itinerary and causal state independently of text generation. Reuse the
   existing sleep planner, mood formulas, life data and time helpers.
3. Enforce home/awake study admission before queueing, execution and commits;
   leaving home is a retryable deferral, with no invalid progress commit.
4. Validate short chat separately from posts. Keep errors and unsent drafts out
   of dialogue history, summaries and facts. Confirm turns with outbox receipts.
5. Connect shared activity/state to chat and writing, defer unread messages during
   unavailability, and reject stale publication descriptions before delivery.
6. Run life planning/publication independently of library and curator availability.
   Preserve the learner and use the existing Writer and off-topic components.
7. Provide a multi-day offline simulation with time, location, activity, event,
   publication or silence reason, including divergent money-request outcomes.
8. Run all tests, Nix checks, privacy checks and Graphify; integrate into develop
   and update the main PR. Deployment requires a graceful stop, receipt review
   and a SQLite backup; do not restart the live process during development.

The unresolved authoring/calibration questions are collected in
[LIFE_GAPS.md](LIFE_GAPS.md). No provisional financial or character values may be
presented as approved deployment facts.

## Current checkpoint

- The additive version-14 migration introduces persisted itinerary, causal
  events, effect receipts, integer money entries, and pending chat storage.
  Migration tests preserve existing graph, mood, journal, dialogue, posts and
  outbox rows byte-for-byte at the SQL value level.
- Chat accepts short substantive replies and withholds internal errors.
  Validated drafts become visible dialogue only when the outbox receipt commits.
  Drafts are bound to their text, channel, trace and unique delivery. A failed or
  uncertain send cannot enter subsequent prompts, compression or memory.
- Legacy dialogue rows remain stored. Their visibility requires a matching
  Telegram receipt; old compression is ignored if it included an unconfirmed
  reply. This prevents an upgrade from reintroducing unsent text into context.
- The financial proposal and confirmed availability rules are recorded in
  [LIFE_CALIBRATION.md](LIFE_CALIBRATION.md). Only the scholarship amount is
  approved; proposed balances and prices are not live configuration.

The runtime itinerary, causal scenario engine, activity-dependent publication
cadence, durable overnight inbox, shared life/chat context, and home-study commit
guards remain implementation work. The storage schema is not evidence that
these behaviors are already connected to LiveApplication. No multi-day causal
simulation or deployment is claimed at this checkpoint.

## Verification of this checkpoint

- `nix develop --command pytest -q`: 472 passed.
- `nix develop --command python -m src.cli run --dry-run`: reached EXAM,
  five answers, no killed posts, no external deliveries, temporary SQLite file.
- Ruff checks and formatting pass for source, tests and scripts.
- `nix develop .#audit --command python scripts/check_repository.py`: passed.
- Graphify rebuilt the code graph. The existing missing optional SQL parser
  still excludes two SQL fixtures; no package was installed outside Nix.
- The original running checkout remains clean and its database was not opened
  by this worktree. Only temporary test databases received the migration.
