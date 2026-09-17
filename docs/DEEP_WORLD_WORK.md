# Detailed world delivery checklist

The owner authorized fictional scenario design and these publication intervals:
10–30 minutes while busy, 5–20 minutes at rest, without a daily or burst quota.
Events must exist independently of posts. All prices added here are authored
simulation parameters, not claims about current real-world prices.

Work stays in the isolated `BlogAI-life` checkout. Verification uses temporary
databases and offline adapters. Do not call a generation or embedding server,
restart the running installation, or send Telegram messages during development.

## Ordered gates

- [x] Specify regression tests before implementation: additive migration,
      deterministic replay, money conservation, expiry, calendar conflicts,
      sleep/study admission, meaningful changes and delivery idempotency.
- [x] Add compatible storage for persisted scenario branches and world changes.
- [x] Add saved NPC calendars, appointments, budgets and contact outcomes.
- [x] Add ingredient lots, recipes, substitutes, expiry and household possessions.
- [x] Add venue hours, route facts, saved disruptions and plan consequences.
- [x] Persist a day → obligation/free-time arc → activity → timed event tree;
      nested actions must fit without shortening journeys or fixed commitments.
- [x] Choose free time from home hobbies, paid work, errands, social and outdoor
      options. Scheduled needs use exponential urgency as their deadline nears.
- [x] Model illness duration (mostly 3–7 days, rarely 10/14), early incapacity,
      gradual recovery, certificate appointments and cancelled commitments.
- [x] Derive productivity and evening study duration from PAD, debt, health,
      food and workload. Record the planning inputs rather than rerolling them.
- [x] Save morning hygiene/packing/optional preparation and debt-driven sleep
      extensions; optional taxi travel must be affordable and paid exactly once.
- [x] Author concrete multi-stage branches for social invitations, park visits,
      food, home, money, family, health, coursework and obligations.
- [x] Connect the rules to autonomous life, existing mood effects and chat context.
- [x] Apply the approved busy/rest cadence without quotas or duplicate actions.
- [x] Send explicit world-change cards and maintain one pinned current-state card
      through the existing durable outbox, including restart and uncertain sends.
- [x] Eliminate repeated sleep deferral writes and hide bookkeeping noise from
      Telegram while keeping the local audit.
- [x] Run multi-day offline simulations with alternative social outcomes.
- [x] Run the full Nix suite and checks, update Graphify, and reconcile scope docs.

## Food and hunger extension

- [x] Separate elapsed hunger, owned food and optional cravings; breakfast, lunch
      and dinner are planned anchors, not artificial resets of appetite.
- [x] Let a moderate lunch hunger lose to a social walk; above 40/100 hunger
      becomes irritating, with a larger, earlier dinner after a skipped meal.
- [x] Add six cafes/coffee shops/restaurants, two fast-food venues and five shops
      with distinct fictional menus, hours, travel times and budgets.
- [x] Persist selection, purchase, owned stock and consumption as separate steps;
      snacks give modest satiety, gum/coffee do not replace a meal.
- [x] Carry hunger, cravings, meal timing and consequences into plans, mood,
      productivity, chat and the state document, with replay-safe tests.

## Contracts

An invitation is not a meeting. A reserved appointment is not attendance. A
purchase is not consumption. A recipe consumes actual unexpired ingredients.
An unavailable friend cannot appear at a venue. Every outcome has a stable cause
and receipt; retrying prose cannot repeat it. NPC circumstances persist and can
change independently of Mika. Delays create later events rather than resolving
an entire conversation in one tick. Consequences use `record_event` through the
existing life-effect mechanism. Home study, UTC storage, prompt-file separation
and delivery-confirmed dialogue remain enforced.

The world observer records meaningful before/after changes. Repeated polling and
clock cursors are not world events. Operational notices can update during sleep;
Mika's posts and chat cannot. A pinned operational summary states its observation
time and uses actual delivery receipts for its message identity.

## Verification evidence

The complete `nix flake check --no-update-lock-file --print-build-logs` passed
with **603 tests** on Python 3.12, the offline CLI startup, Ruff lint/formatting,
lock verification and Nix formatting. The CLI reached the exam phase with zero
external deliveries. Graphify's offline AST update produced 2,142 nodes and
5,759 edges. The repository privacy check and staged Gitleaks scan passed.

Regressions cover optional breakfast with sleep debt, normal preparation time,
perishable food, real travel, delayed eating, protected cooking/eating sequences,
actual hunger overriding a planned park detour, and idempotent consumption.
The multi-day report is [DEEP_WORLD_SIMULATION.md](DEEP_WORLD_SIMULATION.md).
It records three days, 255 life events, 29 ledger entries, 64 activity
transitions, one completed study break and 167 offline outbox deliveries.
The midpoint restart retained plans, resources and receipts. Four additional
fixtures demonstrate help, delay, refusal and help with a lecture, including
different money, mood and pending-task consequences.
Real model prose and Telegram delivery/pin permissions were not tested during
this expansion; all adapters in the acceptance run are offline.
