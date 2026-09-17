# Autonomous life runtime

Implementation uses the isolated `BlogAI-life` worktree and temporary databases.
The running process and production database are unchanged. The owner-requested
state topic is stored separately in the ignored deployment layout.
The ordered checklist is in [LIFE_IMPLEMENTATION.md](LIFE_IMPLEMENTATION.md).

## Connected components

- `RuntimeProviders` persists complete daily itineraries. World queries, writing,
  chat and study admission read the same activity identity, subject and interval.
  Revisions preserve the past and include actual travel when returning home.
- `LifeEngine` stores resources, NPC availability, dependent tasks and causal
  events independently of publications. Integer money entries and effect receipts
  prevent repeated purchases, transfers, repayments and mood consequences.
- `LifeRuntime` runs alongside the learner. Empty libraries, waiting exams and
  slow curator jobs do not prevent everyday events or conversation. Household
  tasks reserve time; effects commit when the reserved activity finishes.
- Recorded events reach the existing OfftopPlanner, OfftopGenerator and Writer.
  Daily, situation and continuation posts use a new file-backed factual contract.
  Evening reflections receive actual events and verbal mood transitions.
- `StudyGate` checks admission before execution and at transaction commit. The
  live scheduler also checks before queueing. Study requires an awake home-study
  interval without a competing household task; departure defers the action.
  Library uploads register sources without reading or extracting them immediately.
- The durable chat inbox preserves unread night messages, received/seen times and
  the reason for delay. It shares activity, subject, resources and mood with the
  blog. Only validated replies with delivery receipts enter subsequent dialogue.
- Outbox admission rechecks activity and expiry before sending. Stale drafts are
  cancelled without becoming uncertain deliveries or rerolling their event.

## Configuration and authoring

The owner-approved financial table is implemented in
`config/life_simulation.yaml`. `config/life_chains.yaml` contains the newly
authorized fictional scenario rules, including mixed, delayed and negative
outcomes. Existing PAD and cycle equations remain unchanged; consequences use
the mood service and retain cause, before/after state and an application receipt.

Life posts and ordinary chat are allowed while awake. Busy activities use shorter
posts and longer gaps; rest permits longer posts. The owner selected 10–30 minute
busy gaps, 5–20 minute rest gaps and no daily or burst quota. Legacy weekly
off-topic quotas, category bans and exam-day exclusions do not govern this
runtime. The default admission rule keeps published life posts in a strict
majority; a numeric share can be configured later. No events are created to fill
a publication quota.

Food, financial pressure, illness, relationship outcomes and commitments affect
tasks and future choices. Existing recognized appliance, series, gym, coursework
and cat state is adopted without retroactive purchases. Legacy records remain
stored. [LIFE_GAPS.md](LIFE_GAPS.md) describes the model's boundaries.
[DETAILED_WORLD.md](DETAILED_WORLD.md) describes the
new event trees, NPC calendars, inventory, productivity and pinned state view.

## Verification

Run everything through Nix:

```sh
nix develop --command pytest -q
nix develop --command python -m src.cli run --dry-run
nix develop --command python -m scripts.simulate_life --days 5 --output docs/LIFE_SIMULATION.md
nix flake check --no-update-lock-file --print-build-logs
nix develop .#audit --command python scripts/check_repository.py
nix develop --command graphify update .
```

[LIFE_SIMULATION.md](LIFE_SIMULATION.md) reports time, location, activity, event
and publication/silence, plus forced assistance branches with distinct financial
and mood effects. Its factual renderer and transport are offline test adapters;
the report does not claim live model prose or Telegram delivery validation.

## Deployment boundary

Code verification does not restart the running application. Deployment must
stop it gracefully, back up SQLite consistently, review pending/uncertain outbox
and learning actions, apply additive migrations, and restart from saved state.
Do not copy a development database over the live installation. Private library,
Telegram layout, world-state overrides and bot tokens remain outside Git.
The concrete stop, backup, migration and restart sequence is in
[LIFE_UPGRADE.md](LIFE_UPGRADE.md).
