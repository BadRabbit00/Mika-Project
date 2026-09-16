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
   and prepare a new main PR. Deployment requires a graceful stop, receipt review
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
- The complete financial table and availability rules are owner-approved and
  recorded in [LIFE_CALIBRATION.md](LIFE_CALIBRATION.md). The financial engine
  still needs to consume these values; approval does not initialize live data.

The runtime itinerary, causal scenario engine, activity-dependent publication
cadence, durable overnight inbox, shared life/chat context, and home-study commit
guards remain implementation work. The storage schema is not evidence that
these behaviors are already connected to LiveApplication. No multi-day causal
simulation or deployment is claimed at this checkpoint.

## Remaining implementation and acceptance work

1. Persist complete daily itineraries, including activities, actual class subjects,
   sleep, travel intervals and deterministic choices. Both `where(at)` and all
   model contexts must read the saved itinerary. Preserve completed activities
   when illness, weather, an obligation or a resource shortage changes the plan.
2. Implement resource state and dependent tasks. Load approved financial values
   from configuration; apply income, purchases, transfers and repayments once.
   Persist pantry, belongings, health, relationships, NPC availability, deadlines,
   promises and story stages. Changes must create or revise actual tasks and
   affect mood through `record_event` or `fire_trigger`.
3. Author and connect the requested causal chains: money assistance and repayment,
   shopping and cooking, repair and replacement, relationship and roommate
   conflicts, illness and recovery, coursework, leisure, cat care, weather and
   family help. Preserve selected outcomes independently of model calls and post
   delivery. Outstanding authoring detail is listed in LIFE_GAPS.md.
4. Add home-study admission before enqueue, execution and result persistence for
   every learning entry point, including library uploads and curator exam
   answers. Leaving home must defer the action and preserve the last valid
   checkpoint. Uploading a file must not mark it as read or immediately extract it.
5. Build truthful writing contexts from recorded events and current activities.
   Connect offtop, daily, situation and continuations through existing writing
   components; retain found, impression, struggle and summary. Generate occasional
   evening retrospectives from actual events and linked mood changes. Recheck
   applicability before delivery; cancel or rewrite stale text without repeating
   the event or applying its effects twice.
6. Configure activity-dependent cadence and event-level deduplication. Retire the
   two-offtopic-per-week limit, broad category bans and exam-day life exclusion.
   Busy awake periods allow shorter, less frequent life posts and ordinary chat;
   rest allows longer messages. Sleep forbids persona sends. The exact daily
   target and life/study distribution still require separate owner agreement.
7. Connect chat to the same activity, class subject, resources, health and mood
   snapshot. Persist incoming messages before background processing; keep night
   messages unread until waking and supply received/seen times to the model.
   Reconcile overnight waiting with the six-hour session timeout and delivery
   freshness. Existing delivery-confirmed history must remain intact.
8. Assemble an independent life loop in LiveApplication. An empty or absent
   article catalogue, waiting exam, slow curator or curator failure must not
   block life state, life publications or ordinary conversation. Separate durable
   work and scheduling boundaries and recover pending work without duplicates.
9. Test all new invariants, then run a multi-day offline simulation with a table
   of time, location, activity, event and publication/silence reason. Force calm
   help, delayed help and refusal branches and verify their distinct effects on
   balances, mood, plans and posts. Exercise departures during generation,
   overnight chat, stale drafts, restart recovery and compatible upgrades.
10. Run the complete Nix checks and refresh Graphify, integrate into develop and
    prepare a new PR into main. Deploy with a verified SQLite backup, graceful
    shutdown, review of unfinished/uncertain actions and post-upgrade checks.
    The production checkout is not an implementation or simulation workspace.

The existing 472-test result validates the current checkpoint. It is not evidence
that these remaining behaviors have been implemented or acceptance-tested.

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
