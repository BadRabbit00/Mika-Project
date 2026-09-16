# Life model: remaining preferences and authoring scope

The owner approved the financial table and authorized deeper fictional rules.
The connected runtime is described in [AUTONOMOUS_LIFE_WORK.md](AUTONOMOUS_LIFE_WORK.md).
These limits must not be filled with model-invented facts.

## Remaining preference

The exact daily publication range still needs agreement. `daily_target` remains
null. Busy/rest gaps, lengths, bursts and occasional evening reflections have
explicit adjustable defaults. Life remains a strict majority of published posts
unless an explicit share is configured. The engine never invents an event to
meet a quota.

## Implemented fictional scope

- Money: approved balances, scheduled income, restricted housing funds, actual
  purchases, assistance, debts, reserve-aware repayments and unpaid bills.
- Food: portions, shopping baskets, meal consumption, shortage and roommate
  compensation. Ingredients, nutrition and spoilage are not modeled.
- People: persisted daily availability and mood, contact history, trust/tension,
  calm help, lectures, delays, refusals, explanations and unresolved conversations.
  NPCs do not have complete independent calendars or financial ledgers.
- Home: temporary coffee repair, repeated failure, affordable replacement and
  delivery. Other possessions are static unless a rule changes them.
- Health: onset, disturbed future sleep, changed plans, several recovery days and
  help with missed material. These are fictional states, not medical diagnoses.
- Obligations: task dependencies, places, hours, priorities, deadlines, reserved
  duration, progress and submission. No invented grade follows submission.
- Leisure/family: episodes and later discussion, gym visits/expiry/renewal,
  temporary cat care/departure, brother help and follow-up problems.
- Weather: only a fresh observation can trigger a weather replan. Returning from
  a walk takes time; the engine cannot rewrite a completed journey.

## Useful future depth

| Area | Additional decisions needed for a richer model |
| --- | --- |
| Income | Reasons and probabilities for a late scholarship or parental transfer; partial amounts and revised due dates |
| Food | Ingredient inventory, recipes, spoilage, shared ownership, cooking failures and substitutes |
| Parents | Work calendars, their own commitments and limits; longer-term repair after difficult conversations |
| Dasha | Her cash and obligations, consent to repeated help, household agreements and shared shopping |
| Timur | Specific plans/promises, reasons for missed contact, boundaries and distinct reconciliation actions |
| Health | Fictional symptom stages and return-to-activity rules, without unsupported medical claims |
| Coursework | Assignment-specific workload, extensions, lateness consequences and externally recorded grades |
| Cat | Owner, exact collection agreement, supplies and who pays when collection is delayed |
| City | Named routes, opening hours, shelters, transport disruption and venue-specific costs |
| Promises | Explicit negotiation when two deadlines conflict, partial completion and communicated rescheduling |

These are extensions to the stored causal model. They are not permission for the
language model to claim an unrecorded transfer, conversation, diagnosis, purchase,
attendance or reconciliation. New outcomes must first be implemented as persisted
events with effects and follow-up tasks.

## Delivery boundary

Internal errors and rejected/unsent replies remain diagnostic data. Confirmed
dialogue alone is visible to future responses. Sleep, current activity and actual
receipt times explain a delayed answer. Freshness checks can cancel a draft;
they must never repeat the underlying event or its consequences.
