# Autonomous life: missing decisions and scenario gaps

This audit compares the requested causal simulation with the existing code,
`life.yaml`, `schedule.yaml`, `rhythm.yaml`, and writing/chat templates. Items below
are authoring decisions, not facts the language model may invent. Proposed
calibration values must be approved before they become deployment defaults.
The owner has approved the complete financial table in LIFE_CALIBRATION.md.
Items settled by that approval are removed from the missing-decision column.

## Required decisions

| Area | Existing material | Missing decisions |
| --- | --- | --- |
| Publication frequency | Two daily posts, two weekly off-topic posts, study sessions | Approved daily range, minimum life share, minimum/maximum gaps, burst limit and recovery gap; behavior when fewer meaningful events occur |
| Availability | Owner confirmed: life posts and ordinary chat while awake; busy periods reduce frequency/length; learning only during home study | Exact busy/rest cadence and long evening-summary frequency remain to be calibrated |
| Money | Approved opening balances, income, housing split, prices, reserves and loan terms in LIFE_CALIBRATION.md | Failed/late scheduled income and effects on due obligations |
| Parent assistance | Sunday calls and food questions; approved request and monthly aid limits | Mother's own availability and persistent mood, refusal/lecture/help conditions, delays, contact cooldown, alternatives after refusal |
| Food | Dish names; approved opening pantry, eight-portion basket and prices | Ingredients and meal consumption, preparation time, spoilage, compensation after shared food is eaten |
| Home | Coffee-machine stages; approved repair-supply and replacement prices | Repair duration and success/failure conditions, saving schedule; whether other belongings can change state |
| Relationship | Timur's initial closeness and decay/contact coefficients | His schedule and own mood, realistic reply delays, misunderstanding/conflict/reconciliation conditions, boundaries and lasting consequences |
| Neighbour | Dasha's traits and approved loan terms | Availability, available funds, shared resources, consent and compensation outcomes, whether promises are kept |
| Health | Weekly illness chance, duration, mood and class/gym effects | Symptom onset/progression/recovery, effect on actual sleep and meals, plans cancelled or deferred, recovery restrictions; avoid invented medical claims |
| University | Exact class subjects/times, coursework stages and deadline week | Assignment issue dates, workload and progress units, deadlines as instants, class cancellations, deadline extensions, submission/grade rules |
| Leisure | Series/anime episode counts, gym stages and approved renewal price | Episode duration, opinion transitions and attendance opportunities; reconcile gym progress and arc stages |
| Cat | Four arc stages | Arrival date, owner and collection arrangement, feeding/supplies/tasks, responsibility for costs, delayed collection |
| Family | Brother asks about school | His availability, specific request and deadline, outcome criteria and follow-up task |
| City | Five broad locations and example objects | Travel edges/durations beyond the university commute, shop/park/gym locations, opening hours, resting stops, weather alternatives |
| Weather | Live/fallback observations | Replanning thresholds, persistence of the observation that changed a plan, safe fallback when current weather is unknown |
| Commitments | Open threads and narrative summaries | Priority/conflict resolution, dependencies, expiry, missed promises, debt repayment and rescheduling rules |
| Startup | Existing progress, NPC and arc tables; approved opening money and pantry | Remaining initial health, relationship and unfinished-task state; importing existing history must not create retroactive purchases or invented conversations |

## Contradictions to resolve explicitly

- `World.where()` chooses a location from broad hour bands, while actual classes
  can finish after those bands. It has no saved visits or return journey.
- The commute duration is 35 minutes, but the blackout describes a 40-minute
  interval starting 45 minutes before class. A persisted itinerary must define
  which interval means travel and where the remaining time is spent.
- `sleep_states` contains arbitrary claims, including not sleeping at all.
  Actual sleep history must be the only source of these claims.
- University frames can independently choose a subject that is not on today's
  timetable, skip class without changing the plan, or invent a deadline change.
- Food frames can claim a meal at four in the morning while the sleep model
  places Mika asleep. Cooking, delivery and consumption need separate receipts.
- Coffee-machine and gym state appear both in `progress` and in `arcs`; these
  cannot advance independently. Cat arrival is both a random home frame and an
  arc stage and must have one identity.
- `OfftopPlanner.remember()` advances life only after a publication. Real events
  and their consequences must also happen on days when no post is sent.
- Multi-day category cooldowns and exam-day exclusion suppress unrelated daily
  life. Deduplication must use the event identity and story stage instead.
- `write_situation.md` asks the model to invent a transient event.
  Causal-life generation needs a file-backed contract based on a recorded event.
- `write_daily.md` forbids daily summaries, while the new requirement includes
  evening reflection. Its autonomous variant needs a different explicit contract.
- Chat still reads static `life.progress`, has no connected durable unread inbox,
  and expires overnight before unread messages can receive an answer.
- Learner admission currently checks generation windows and blackout only for
  some post types. Extraction, quizzes and exam answers can commit away from home.
- A single background queue serves learning, owner commands and chat. A slow
  curator can delay unrelated life and conversation work.
- Empty-library startup currently fails before the life loop can start. Article
  availability must gate the learner, not the whole application.

## Required outcome branches

For each authored scenario, define its prerequisites, allowed location/time,
duration, dependency, chosen outcome, immediate state changes, mood event,
follow-up obligation and publication relevance. Persist the chosen outcome once.

- Money request: calm help, help with a lecture, delayed contact/transfer, refusal;
  each must differ in balance, relationship/mood, next task and spending plans.
- Empty pantry: affordable shopping, postponed shopping, substitute meal, shared
  meal; cooking consumes existing ingredients rather than creating them.
- Repair: temporary success, repeated fault, unusable machine, saving, replacement.
- Late reply: genuine unavailability, explanation, unresolved tension, reconciliation.
- Food conflict: compensation, shared dinner, unresolved disagreement.
- Illness: onset, changed sleep and plans, several recovery days, resumption.
- Coursework: issue, postponement, real work, deadline pressure, submission or delay.
- Series/gym/cat: one progressing history with completion/expiry/collection.
- Rain: change an unstarted walk, return from an active walk with real travel time,
  or wait at an existing shelter; never teleport or rewrite completed history.
- Brother: agreed evening time, explanation, understanding or a follow-up problem.
- Debt: loan receipt, due date, sufficient income, repayment or a recorded delay.

## Delivery and history boundaries

Rejected model output and internal errors are diagnostic data, never dialogue
turns. Generated replies are pending until a Telegram receipt confirms delivery.
Unread messages retain their arrival time and reason for delay. On becoming
available, Mika receives the actual current activity, subject, mood, sleep and
the period during which she could not respond. An overnight delay is data for a
natural response, not a canned explanation inserted into Python.

Before delivery, revalidate the activity/event identity and time. A stale draft
is withheld or rewritten from the same recorded event. Rewriting must not repeat
a purchase, a conversation outcome, a mood effect or a story transition.

## Resolved in the current development checkpoint

- Short substantive chat replies now pass independently of post length limits.
  The existing session template's lower-bound metadata no longer defines whether
  a chat response is empty; its upper limit and all content checks still apply.
- Validated output is staged separately. Only matching Telegram receipts make
  Mika's replies visible in conversation history. Internal errors are withheld.
- Old unsent rows remain in storage but are excluded from model context, along
  with any compressed history that included them.

These changes do not yet provide the shared itinerary or overnight inbox. Their
presence in the new schema must not be mistaken for completed runtime behavior.
