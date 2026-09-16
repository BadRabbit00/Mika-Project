# Autonomous life implementation checklist

The owner approved the financial table and authorized deeper causal scenarios.
New scenario rules are authored simulation rules, not claims about real people.
Existing PAD/cycle formulas, UTC storage, exact tokenization and prompt-file
boundaries remain mandatory. Work stays in BlogAI-life with temporary databases.

## Ordered delivery gates

- [x] Add compatible storage and delivery-confirmed chat history.
- [x] Record approved money parameters and awake/busy/rest availability.
- [x] Persist a complete itinerary with travel, subjects and half-open intervals.
- [x] Make world queries, model contexts and admission use that itinerary.
- [x] Support revisions of future activities while preserving past decisions.
- [x] Persist resource state, NPC state, dependent tasks and causal event links.
- [x] Apply purchases, transfers, debts and effects atomically and once.
- [x] Record each mood consequence through the existing mood service, with a
      receipt linking the cause, actual application time and before/after state.
- [x] Connect food, money, appliance, relationship, health, coursework, leisure,
      cat, family and weather chains, including mixed and delayed outcomes.
- [x] Let unmet needs and commitments change future plans and event selection.
- [x] Enforce home-study admission before queue, execution and result commit.
- [x] Remove immediate learning from the library upload path.
- [x] Build truthful event contexts and an evening event/mood retrospective.
- [x] Generate off-topic, daily, situation and continuation posts through Writer.
- [x] Revalidate context before send; rewrite or expire the same saved event.
- [x] Configure busy/rest cadence, bursts and event-level deduplication. Keep the
      daily target unset until the separately requested count is approved.
- [x] Persist required activity notices, chosen breaks and intended returns;
      regenerate stale prose and confirm grouped notices with delivery receipts.
      See [ACTIVITY_TRANSITIONS.md](ACTIVITY_TRANSITIONS.md).
- [x] Run life independently of article availability, learner state and curator.
- [x] Feed the shared life state into chat and persist incoming messages first.
- [x] Defer unread night messages and reconcile them with session expiry.
- [x] Preserve delivery-confirmed history during retry and restart.
- [x] Run multi-day simulations, including help, delay and refusal branches.
- [x] Check restart, departure during learning, stale output and migration safety.
- [x] Run all Nix checks, refresh Graphify and review repository privacy.
- [x] Integrate the verified code into local develop; leave the running checkout alone.
- [x] Publish [PR #3](https://github.com/BadRabbit00/Mika-Project/pull/3) for deployment review.
- [ ] Deploy separately with graceful shutdown, backup and pending-action review.

## Connected causal depth

Every significant event records its cause, result, effects and possible next
task. A publication is optional. The language model cannot choose the outcome.

| Chain | Immediate consequence | Later connection and mood consequence |
| --- | --- | --- |
| Low balance, empty pantry | Cancel cafe plan; create shopping or assistance task | Hunger and uncertainty reduce energy/agency; a stocked pantry relieves pressure without erasing a difficult conversation |
| Mother's delayed response | Keep request pending; defer discretionary spending | A later contact reuses persisted availability and tension; no money appears before transfer |
| Help with a lecture | Funds arrive, spending plan becomes feasible | Relief and hurt coexist; the next contact reflects remembered tension rather than a permanent negative relationship |
| Refused help, Dasha loan | Debt and repayment date are created | Repayment later relieves obligation and improves trust; overdue debt blocks a new loan |
| Early debt repayment | Smaller discretionary balance | Trust improves and future consent becomes more likely, but today's outing may be postponed |
| Roommate eats food | Pantry falls; shopping may become necessary | A conversation leads to food compensation or an unresolved grievance; trust affects later lending |
| Temporary coffee-machine repair | Small expense, appliance usable again | Repeated failure creates frustration and a saving task; replacement resolves the specific recurring problem |
| Cafe spending | Coffee costs reduce the same cash balance as purchases | A smaller balance can block replacement or create an assistance task and an economy plan |
| Short sleep, demanding class | Sleep debt lowers the energy baseline; the class sets busy cadence | Sleep history and current mood reach chat and writing; a task cannot run during class |
| Illness before a deadline | Classes/outing cancelled; rest and food prioritized | Coursework moves closer to deadline; accepting Aika's help restores agency while leaving real work to complete |
| Aika shares notes | Recovery creates a later contact task and relief | Notes do not mark material learned; actual coursework and article study still require home time |
| Timur is unavailable | A conversation remains pending | Saved fatigue and relationship tension affect explanation, delay or conflict, each with its own mood effect |
| Conflict followed by a quiet walk | Some immediate agitation decays | The unresolved conversation still exists; a pleasant walk does not automatically mark reconciliation |
| Promised brother help and coursework | Competing evening tasks reserve distinct intervals | Helping improves connection or creates a next-day follow-up; that occupied time cannot also count as article study |
| Rain during a planned outing | Shelter, delayed departure or real return travel | The missed cafe saves money; a home meal/series replaces the outing without claiming the planned meeting happened |
| Series episode watched | Episode advances once and creates a discussion task | A later conversation with Aika refers to the saved cause; generated text cannot advance the series |
| Gym subscription nearing expiry | A feasible visit competes with chores/study | Attendance can improve agency; a skipped visit retains the expense and can discourage renewal rather than trigger an automatic purchase |
| Cat needs care | Care reserves a home interval and produces warmth | The care interval cannot count as article study; departure ends recurring tasks and creates a mixed mood event |
| Completed coursework | Real progress and submission receipt | Deadline pressure resolves, freeing the evening and making deferred social plans feasible |

Further authored directions, including explicit promise negotiation, shared
cooking, NPC budgets and richer reconciliation, are listed in
[LIFE_GAPS.md](LIFE_GAPS.md). They are not claimed as existing runtime branches.

## Behavioral boundaries

- Persist a person's availability and response outcome before generating prose.
- Retain mixed outcomes, partial repayment, unfinished conversations and delayed
  tasks. Do not force every chain to resolve on the day it starts.
- Apply configured mood events through literal PAD dynamics. Do not directly
  assign PAD, manufacture a target emotion, or reset mood after a positive event.
- Mood can influence choices and interpretation, but cannot fabricate factual
  outcomes such as a transfer, attendance, a reply or recovery.
- Home study is an activity in the plan; ordinary chat about familiar topics is
  not permission to ingest new knowledge while away from home.
- Sleep is authoritative. Busy awake activities reduce cadence; they do not
  suppress all life posts. Operational bot messages are separate from persona
  availability.
- Preserve activity and event identities across retry. Changed plans revise
  future intervals; completed history is immutable.
