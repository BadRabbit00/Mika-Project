# Life model: authored scope and remaining limits

The owner authorized fictional world design and selected **no daily post quota**.
Busy gaps are 10–30 minutes; rest gaps are 5–20 minutes. Actual events, sleep,
validation and delivery govern publication. Mandatory activity notices retain
priority. See [DETAILED_WORLD.md](DETAILED_WORLD.md) for the implemented rules.

## Resolved gaps

- NPC work/class/sleep calendars, optional commitments, appointment conflicts,
  disposable wallets, income, expenses, mood and contact history are persisted.
- Ingredients have quantities, ownership and expiry. Recipes consume ingredients,
  permit explicit substitutions and create perishable prepared portions.
- Elapsed hunger, optional meal slots and cravings affect routes, portion size,
  productivity and mood. Thirteen fictional food venues and shops have distinct
  menus, prices and hours; purchase and consumption are separate saved actions.
- Coffee repair/replacement coexists with headphone, charger, umbrella, kettle
  and washing-machine state. Effects and actual purchases are explicit rules.
- Outings include travel, opening hours, park subactivities, a possible cafe and
  saved route disruptions. Posting frequency does not shorten the enclosing arc.
- Home hobbies, paid work, groceries, laundry, clinic visits and social plans
  compete for free time. Recurring chores use exponential deadline urgency.
- Illness has acute, weak and recovering stages. Productivity controls home study.
  Sleep debt can replace optional preparation with sleep and an affordable taxi.
  Low-productivity work earns 20–30% less, paid only after completion.
- Delayed invitations, disappointment, support, extensions and agreements have
  concrete branches, persistent effects and mood consequences.
- Operators receive before/after changes and one pinned editable state document.

## Deliberate boundaries

These are modeling limits, not unresolved permission requests:

- The city is a fictional route graph with configured hours and travel times,
  not a street map or a live transport service. Disruptions are authored draws.
- Food uses ingredient units and portions, not calories, grams or dietary advice.
  Existing portions are adopted without an invented opening purchase.
- NPCs follow authored calendars; they are not independent generative agents.
  Disposable wallets govern extra assistance and loans. Approved regular family
  housing/allowance streams remain separate household funding accounts.
- Coursework has progress, extensions and submission, but no invented grades.
  Health stages are fictional simulation states, not diagnoses or treatment.
- Device histories cover the authored rules. More possessions require explicit
  rules before they can fail, be bought or repaired.
- Physical follow-ups wait for a compatible saved interval. Passive waits may
  cross days; unresolved scenario branches have a configured deferral horizon.
  Material obligations, paid orders and earned receivables are exempt from that
  expiry; prior consequences remain recorded when an optional branch ends.
- The state attachment contains simulation state and plans, not every historical
  database row or private user dialogue. Uncertain sends need receipt reconciliation.

## Verification boundary

Development uses temporary databases and offline prose/transport adapters. No
local model was called for this expansion. Language quality and real Telegram
delivery/pin permissions remain deployment checks. Production data and the
running process remain untouched; the requested `topics.state` entry is a
separate private deployment configuration change.
