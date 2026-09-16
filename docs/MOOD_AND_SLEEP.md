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
  --start 2026-09-14 --days 14 --seed 1 --with-schedule \
  --output data/simulations/example
```

Use a fresh output directory. The script preserves existing state databases.
It writes a daily console table, `report.json`, `samples.csv`, `transitions.csv`,
`state.sqlite3`, and structured `events.jsonl` logs. CSV timestamps and SQLite
timestamps use UTC; the simulated calendar and displayed dates use Almaty time.
The console includes daily averages, sleep statistics, and every mood transition.
The scheduled run also writes `sleep.csv` and `world.csv`, preserving planned
and actual wake times, accumulated debt, location, and blackout decisions.
It makes no model calls and publishes nothing.

The workload in `tests/fixtures/mood_scenario.json` is an explicit test scenario,
not a guessed production event-frequency policy. Omit `--with-schedule` to repeat
the independently verified step 4 run with explicit sleep-debt fixtures.
The scheduled run calculates debt from actual sleep intervals, using fixture
inputs for missing interruption times, sleep labels, and known locations.

The verified mood-only run produced 336 hourly samples and 60 events. Mean PAD
was P=0.1831, A=-0.0481, D=0.0145. No sample reached either extreme. This exercises
the specified coefficients; it does not establish psychological validity.

The scheduled run produced 336 hourly PAD samples, 70 events, and 14 actual sleep
intervals. Mean PAD was P=0.1383, A=-0.0554, D=-0.0203, with no samples at either
extreme. An additional daily 08:40 check exercises the commute blackout between
hourly samples. These checks are separate from the PAD averages.

## Sleep and day context

`Schedule.from_config(Path("config"))` reads the timetable, wake rules, and
blackout windows from `schedule.yaml`, plus the sleep formula values from
`life.yaml`. All returned datetimes are aware and normalized to Almaty.

- `plan_sleep(activity_day, last_complexity=..., mood=..., rng=...)` implements
  section 25.1. The usual 01:00 bedtime belongs to the following calendar day.
  The explicit sleep label is `neutral`, `stuck`, or `down`; there is no inferred
  mapping from PAD. The result is a provisional `SleepWindow`.
- `wake_up(day, rng=..., interruption_times=..., trigger_states=...)` implements
  section 29.1 in configuration order. The first matching interruption that
  passes its probability check wins. Otherwise, wake is before the first lesson
  by the configured alarm interval, or uniformly within the free-day window.
- Create `SleepWindow(plan.bedtime, wake.at)` for actual sleep. Reject impossible
  intervals instead of resampling or silently changing the formula. Elapsed
  hours use timestamps, including Almaty's repeated hour in February 2024.
- `sleep_debt(previous_debt, actual_sleep)` uses the literal deficit-and-clamp
  formula. Feed its result into `BaselineContext`; mood applies the configured
  P and A penalties and baseline bounds.
- Pass a non-null `wake.event_id` or a lesson's `event_id` to
  `MoodService.record_event`. Their deltas come from the supplied schedule.
  The schedule does not mutate PAD or insert mood rows.
- `blackout(at, sleep=..., road_roll=...)` checks half-open sleep and lesson
  intervals. Timetable breaks remain available. During the configured road
  window, rolls below 0.3 allow publication. Supply the same roll when rechecking
  one proposed post; the method does not draw new randomness on each read.

`World.day_context` assembles `bedtime`, `wake_time`, `sleep_debt`, `location`,
`daypart`, available objects, and the blackout result. The caller supplies the
relevant sleep interval and known location; location names and objects are
validated against `life.yaml`. This module returns structured facts without
constructing model prompts. Writer/context integration belongs to step 7.

Sleep calculations are pure with respect to storage. Mood debt is persisted with
event snapshots; the caller owns the actual sleep interval and wake history.
Production persistence for those facts needs the schema decision recorded in
`TODO(SLEEP-HISTORY)` before scheduling across restarts.

## Specification conflicts

See [TODO.md](TODO.md) for the inconsistent six-hour decay assertion, baseline
clamp example, cycle epoch and weekday claim, undefined history windows, trigger
outcomes, and conflicting sleep/wake descriptions. The formulas and supplied
configuration values remain unchanged.
