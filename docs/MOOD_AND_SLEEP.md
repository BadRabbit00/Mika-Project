# Mood and biological time

All Python commands run inside the Nix devShell. Configuration and prompt files
remain read-only.

## Mood

`src/core/mood.py` implements the literal inertia and lazy-decay formulas from
sections 28.2 and 28.3. `Mood` is immutable. `MoodModel` reads descriptions,
coefficients, events, and baseline modifiers from `config/mood.yaml`; local
dayparts come from `config/life.yaml`. Cycle phase offsets and coefficient
multipliers come directly from the configured 28-day cycle.

`MoodService.record_event` and `MoodService.fire_trigger` are the only mutation
entry points. Both append SQLite snapshots transactionally. `view(at, context)`
computes decay without writing, so extra reads cannot change later results.
Snapshots and sleep debt are rounded with `round(value, 4)`. Migration 4 adds a
storage constraint for sleep-debt precision and validity.

Callers provide a stable aware cycle epoch, initial state, and explicit baseline
facts. There is no inferred duration for a recent exam or correction. A trigger
caller also supplies the relevant week's start and the next known exam time.
Frequency limits, cooldowns, and exam guards apply before the state is committed.

Trigger resolutions are inserted into `mood_queue` in the same transaction as
the triggering snapshot. Read due items with `pending_resolutions(until)`, then
consume them with `record_event(..., queue_id=..., at=resolution.fire_at)` before
advancing time. Consumption and the resulting snapshot commit atomically.
Unspecified probability outcomes fail without changing mood.

`mood_block` returns only the configured octant name, hint, and three band texts.
It cannot include raw PAD values, phase IDs, cycle days, or physiology details.
Mood remains independent of the knowledge graph.

## Controlled two-week simulation

```sh
nix develop --command python scripts/simulate_mood.py \
  --start 2026-09-14 --days 14 --seed 1 --output data/simulations/example
```

Use a fresh output directory. The script preserves existing state databases.
It writes a daily console table, `report.json`, `samples.csv`, `transitions.csv`,
`state.sqlite3`, and structured `events.jsonl` logs. CSV timestamps and SQLite
timestamps use UTC; the simulated calendar and displayed dates use Almaty time.
It makes no model calls and publishes nothing.

The workload in `tests/fixtures/mood_scenario.json` is an explicit test scenario,
not a guessed production event-frequency policy. The mood-only run uses explicit
sleep-debt fixtures while the scheduling stage is developed.

The verified mood-only run produced 336 hourly samples and 60 events. Mean PAD
was P=0.1831, A=-0.0481, D=0.0145. No sample reached either extreme. This exercises
the specified coefficients; it does not establish psychological validity.

## Specification conflicts

See [TODO.md](TODO.md) for the inconsistent six-hour decay assertion, baseline
clamp example, cycle epoch and weekday claim, undefined history windows, trigger
outcomes, and conflicting sleep/wake descriptions. The formulas and supplied
configuration values remain unchanged.
