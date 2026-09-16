# Mandatory activity transitions

Owner requirements supersede ordinary life-post quotas for significant transitions.
Implementation remains in the isolated life worktree; tests use temporary databases.

## Delivery checklist

- [x] Add compatible storage for selected breaks, transitions and delivery bindings.
- [x] Test durable duration, UTC storage, study admission, mandatory priority,
      restart, coalescing, stale generation and receipt handling before implementation.
- [x] Persist tea preparation, the break and intended study resumption as activities.
- [x] Choose configured durations using saved mood, sleep debt and available time.
- [x] Observe actual transitions and explain study endings from recorded reasons.
- [x] Prioritize transition intents independently of ordinary cadence and quotas.
- [x] Combine nearby undelivered transitions with a compatible actual life event.
- [x] Rebuild stale transition text without repeating actions or dropping transitions.
- [x] Validate activity claims against the saved plan and recorded event evidence.
- [x] Announce sleep during a real preparation interval; send nothing during sleep.
- [x] Run the complete Nix suite, offline simulation and refresh Graphify.
- [ ] Commit and integrate into local develop; preserve the running installation.

## Boundaries

An uncertain Telegram send remains bound until receipt review. A failed model
call leaves a durable publication obligation; it cannot justify sending invalid
text. After downtime, transitions may be reported retrospectively, in sequence.
Sleep announcements cannot be delivered during sleep or disguised as current
intentions on the following morning. Exact tokenization and existing PAD formulas
remain unchanged. All generation instructions stay in prompt files.

## Stored behavior

Migration 15 adds `life_breaks` and `activity_transitions`; existing tables and
receipts are retained. Break planning and the itinerary revision share one SQLite
transaction. The transition observer records a durable cursor and deterministic
transition IDs. A separate publication intent groups up to four transitions,
with an optional actual event in the same activity. Primary delivery confirms
all attached transitions and the related event in the receipt transaction.

If generation or delivery crosses an activity boundary, the old draft is killed.
Its underlying transitions remain pending and form a new intent with current
plan evidence. No activity, purchase or mood consequence is rerun. An uncertain
send retains its binding for receipt review; unrelated later activity can proceed.
A rejected draft leaves a timed retry obligation rather than disappearing after
the ordinary three-attempt event limit.
Required notices are excluded from ordinary daily and burst budgets. A standalone
notice does not reset the random-post timer; a combined notice and life event does.
This leaves room for a distinct recorded observation during a break without
posting the same event twice. The shared Telegram transport pacing still applies.

`config/activity_transitions.yaml` owns the adjustable defaults:

| Break | Preparation | Break duration | Additional condition |
| --- | --- | --- | --- |
| Tea | 3–5 minutes | 10–20 minutes | Home study; enough time to return |
| Food | 5–10 minutes | 15–25 minutes | Food available in the shared inventory |
| Rest | None | 8–18 minutes | Home study |
| Walk | Actual outward and return journeys | 15–30 minutes | No observed rain; travel and return fit |

After 50 continuous study minutes, a deterministic choice selects a feasible
break. Fatigue or sleep debt chooses from the longer half of its range; the next
commitment caps the result. At least 15 study minutes must remain after returning.
Severe fatigue cancels remaining home-study intervals for that day. Illness,
bedtime or other plan changes can cancel a saved return; the ending has its own
recorded reason. Tea/rest and food use the existing mood event machinery.

Preparation for sleep begins 12 minutes before saved bedtime. Older persisted
days receive that interval when bedtime approaches. While awake, its announcement
can join earlier transitions; sleep itself blocks delivery. Model/transport
unavailability can still prevent a timely announcement. Recovery reports that
history honestly rather than sending it during sleep.

## Prose validation boundary

The writer receives current and near-future activities, ordered recorded changes
and factual reasons through `prompts/write_transition.md`. Retry feedback contains
validation reasons. The deterministic guard covers the authored Russian/English
patterns for activities, intentions, purchases, meetings, participants, sleep
and unsupported fatigue explanations. The send gate checks both current plan
identity and these claims again. These checks are regression-tested filters,
not a proof of arbitrary natural-language entailment; new scenario vocabulary
requires corresponding rules and tests. The model never creates simulation facts.
