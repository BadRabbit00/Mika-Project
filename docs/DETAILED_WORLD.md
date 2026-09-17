# Detailed causal world

## Timeline and persistence

`Itinerary` saves the complete local day. `world_plans` records a containment tree:

```text
Day
  Awake
    University: preparation -> walk -> wait -> bus -> walk -> classes -> return
    Recreation (110–150 minutes including travel)
      Travel -> park walk -> fountain rest -> optional cafe -> travel home
        Kiosk -> purchase -> consumption -> return to the fountain
    Home life: cooking / drawing / movie / paid work / appointments
    Home study: study -> tea preparation -> saved break -> resume or finish
  Wind down -> sleep
```

This tree derives from saved activities and completed steps, not a second random
location provider. Fixed commitments retain their boundaries. A meeting can revise
a future home-rest interval only when travel, attendance and return fit. World
queries, writing, chat and admission read the same active itinerary.
An ongoing nested action supplies its own label and end time to shared context;
learning admission and final delivery also check that action's identity.

Each scenario saves its rules, node, duration, parent activity, due time and branch
receipt. One physical scenario action can occupy an instant. Passive waits for
replies, washing or transfers can coexist with other activity. Physical follow-ups
wait for a suitable place and enough time; a local episode cannot follow Mika home
from a venue. Restart preserves both completed consequences and unfinished work.

## Authored rules

`world_details.yaml` holds parameters and `world_scenarios.yaml` holds event trees.
These are fictional defaults, not live prices or facts about real people. Original
approved living costs remain in `life_simulation.yaml`.
`nutrition.yaml` adds authored menus, appetite and discretionary food choices;
see [NUTRITION.md](NUTRITION.md) for the portion and meal-slot rules.

Recurring chores use `expm1(k * progress) / expm1(k)` with bounded progress and
configured `k = 6`. A seventh-day obligation has little weight on day three and
much more near day six. Actual food shortage can take priority. Date seeds and
saved plans prevent restart rerolls. Low funds favor work; illness favors rest.

Productivity combines a configured baseline, PAD, sleep debt, hunger and class
workload, then applies the health-stage multiplier. It changes study duration,
not the literal PAD/cycle equations. The acute day has zero productivity. Illness
normally lasts 3–7 days; the 10/14-day cases have weights 2/1 out of 100. Recovery
permits an earlier home-study window and a bounded extra allowance. A clinic
interval must finish before its certificate receipt exists.

Sleep debt can extend an uncompleted night using the existing sleep planners.
Optional preparation is removed in order; hygiene, packing and travel remain.
A normal 35-minute commute includes two five-minute walks, a three-minute wait
and the remaining bus ride. An affordable saved taxi decision uses 18 minutes
and KZT 2,200. Only the paid leg incurs a fare; completed debt receipts persist.

Work has a brief, a timed execution step and delayed payment. Low productivity
applies a saved 0.70–0.80 factor to the KZT 3,500 base. Submission creates a fixed
receivable; payment credits it once. Prose cannot change or duplicate the amount.

## Concrete event trees

Every row has configured durations and preconditions. Blocked actions produce no
completion effect. Mood changes use durable `life_effects` and `record_event`.

| Scenario | Saved sequence and branches | Consequences |
| --- | --- | --- |
| Park treat | Kiosk -> buy once -> eat -> fountain | KZT 700 debit, real consumption and rest |
| Park carousel | Ticket -> queue -> ride -> recover | Paid ticket, waiting frustration and enjoyment |
| Park sketch | Choose view -> draw -> save unfinished sketch | Home continuation; no invented finished artwork |
| Invite Dasha | Invite -> delayed busy/tired/free reply -> agree or alternative -> attend -> reflect | Calendar conflict or an actual route; uncertainty/warmth |
| Invite Aika | The same stages using her own obligations and mood | Distinct contact and appointment history |
| Invite Timur | The same stages using his project/work calendar | Disappointment need not become a breakup |
| Home drawing | Materials -> drawing -> continue or stop | Saved progress and achievable satisfaction |
| Movie evening | Choose -> watch bounded segment -> pause | Viewing state records what actually happened |
| Freelance | Brief -> work -> receivable -> next-day payment | Productivity-dependent wage, credit once |
| Rice meal | Ingredients -> shortage or cooking -> store | Ingredients become prepared portions; a separate meal consumes them |
| Omelette | Eggs/milk check -> permitted substitution -> cook -> store | No food without owned, unexpired ingredients |
| Shared kitchen | Mess -> Dasha busy/free -> clean/discuss -> agreement | Household tension and remembered agreement |
| Laundry | Due-date choice -> sort -> wash wait -> hang -> next-day fold | Appliance use, clean clothing, recurrence reset |
| Headphones | Fault -> temporary repair -> later check -> fixed/broken -> replacement | Frustration; actual KZT 4,900 purchase when needed |
| Charger | Loose cable -> test -> later store purchase | An errand and KZT 2,500 expense |
| Umbrella | Fresh rain -> inspect/pack -> actual return -> dry | Preparation cannot fabricate an outing |
| Mother reconnect | Existing tension -> contact -> delay/calm talk -> reflect | Repair can ease tension without erasing disagreement |
| Timur boundary | Existing tension -> propose talk -> delay/talk -> agreement | Saved expectations and reduced tension |
| Brother follow-through | Request -> explanation -> understood/new question -> follow-up | Reserved time and warmth/agency |
| Coursework backup | Work -> copy materials -> confirm -> return | Backup is neither learning nor submission |
| Assignment extension | Concern -> request -> wait -> agree/refuse | A revised deadline or continued pressure |
| Ill friend visit | Message -> unavailable/support/visit -> reservation -> actual visit -> rest | Support improves mood without curing illness |
| Recovery notes | Ask Aika -> wait -> notes -> arrange home work | Receiving notes never marks them learned |
| Budget review | Actual shortfall -> inspect -> protect essentials -> defer expense | Planning restores agency without inventing money |
| Coffee savings | Broken machine -> reserve if feasible -> wait -> replacement task | Cash/savings transfers; reserve can fund replacement |
| Cat supplies | Cat present -> need -> store purchase -> contact owner | Expense and reimbursement discussion remain distinct |
| Gym recovery | Recent real visit -> soreness -> stretch -> next-day recovery | No invented extra workout |
| Grocery queue | Store -> list -> queue -> finish | Waiting consumes real time |
| Cafe seat | Cafe -> choose -> unavailable/available -> settle | No additional automatic coffee charge |
| Forgotten charger | Outing -> notice -> cope -> packing reminder | A future reminder, not an invented purchase |
| Sleep routine | Low productivity late in day -> prepare -> quiet pause | Mood benefit; actual sleep follows the saved plan |
| Family photo | Find -> share -> delayed reply -> remember | Positive contact distinct from financial requests |
| Promise conflict | Competing obligations -> contact -> prioritize/reschedule -> follow-through | The unresolved promise stays visible |
| Ill food delivery | Ill and low food -> affordable order -> wait -> receive -> eat | KZT 2,500 debit, two portions arrive, one is consumed |

Earlier coffee-machine, series, submission, gym, cat, family-assistance and loan
chains remain active. All share the same resources and ledger. Calendars and NPC
funds can prevent otherwise plausible outcomes. New facts precede their prose.

## Blog and chat

No daily or burst quota is enforced. Delivery starts a saved 10–30 minute busy
gap or 5–20 minute rest gap. One ordinary life publication waits in outbox at a
time; required notices retain priority and may combine related transitions.

Expired present-tense activity observations stay in history, outside the ordinary
queue. Older outcomes retain their timestamps for past-tense writing. Validation
and final delivery admission check place, activity and planned intentions again.
Evening summaries receive real events and verbal mood changes. Chat sees the same
world, subject, resources and mood; sleep keeps messages unread. Repeated sleep
polls do not rewrite the inbox. Only validated, delivered replies enter memory.

## One operator state message

The optional topic belongs in the private YAML alongside the seven existing ones:

```yaml
topics:
  # Keep the existing topic entries.
  state: 1266
```

The ops bot sends a state photo, saves its receipt, pins that message and edits
the same ID thereafter. The caption shows current place/activity, mood, health,
productivity and money. The [PAD plot](PAD_STATE.md) shows its current mood with
fixed axes and camera. `/state` exports JSON with resources, tasks, appointments,
NPC calendars and the nested plan. Historical rows and private user chat are
excluded. The bot needs photo/media and pin permissions in the state topic.

The machine topic receives a separate before/after record with its cause. Large
diffs use JSON attachments. Identical snapshots produce no new record. Bookkeeping
stays in the local audit, avoiding duplicate Telegram cards. An uncertain send
blocks a new state operation until its receipt is reconciled; it does not blindly
create another pinned message.

## Verification and upgrade

Migration 16 adds world tables without resetting earlier state. Existing saved
days stay intact; the new rules apply to future unsaved days. Follow
[LIFE_UPGRADE.md](LIFE_UPGRADE.md) for a graceful, backed-up deployment.

The offline suite and simulator run through Nix. The simulator uses temporary DBs,
real planning/accounting and factual prose/transport adapters. It never calls the
local models. [DEEP_WORLD_SIMULATION.md](DEEP_WORLD_SIMULATION.md) reports several
days and alternative financial outcomes; it does not claim generated prose or
real Telegram delivery quality.
