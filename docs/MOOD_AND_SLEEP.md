# Mood and biological time

Mood uses the literal inertia and lazy-decay formulas from architecture sections
28 and 35. Mood values are immutable; only record_event and fire_trigger append
state. Reads never mutate PAD. Stored PAD and sleep debt use round(value, 4).
Model context receives configured band/octant descriptions, never coordinates.

## Cycle and baseline

The nominal cycle is 28 days with deterministic length jitter from -2 through +2,
seeded by the cycle epoch and cycle number. Menstrual, ovulatory, and luteal phases
last 5, 3, and 12 days. Follicular length is L-20; it absorbs all variation. Late
luteal means the final five days. The nominal table still matches days 6–13.
The epoch and one-based starting day come from the approved configuration.

Baseline history uses the configured 72-hour exam window, 48-hour correction
window, three successful quiz rounds, and waiting duration. Runtime providers
assemble these facts outside MoodModel. Semester pressure and sleep debt modify
the baseline before its configured clamp. Trigger limits use rolling seven days;
resolutions are consumed transactionally in chronological order.

Disabling mood yields the configured neutral state. The live settings callback
records that state and clears queued resolutions through record_event.

## Sleep and observations

Schedule.plan_bedtime implements section 25.1; Schedule.resolve_wake implements
section 29.1. They answer separate questions. Impossible intervals are rejected.
Debt recovery comes only from surplus sleep, with no extra good-night bonus.
SleepHistory records explicit intervals and charges debt exactly once, including
after process restart. It finishes older pending nights before reading a later one.

Classes include five minutes after their end and no grace inside the lesson.
Commute starts 45 minutes before the first class and lasts 40 minutes. Sleep,
class, and commute blackout checks also run immediately before live publication.
Current location and sleep observations are explicit caller inputs; their live
format is documented in [LIVE_RUNTIME.md](LIVE_RUNTIME.md).

## Simulation

```sh
nix develop --command python scripts/simulate_mood.py \
  --start 2026-09-21 --days 14 --seed 42 --with-schedule \
  --output data/simulations/example
```

Use a fresh output directory. The simulation produces console tables, CSVs,
SQLite snapshots, and structured logs without model calls or publication.
Its scenario is a test fixture. Results and corrected decay assertions are in
[VALIDATION.md](VALIDATION.md).
