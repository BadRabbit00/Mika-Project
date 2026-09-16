# Live composition

`python -m src.cli run` composes the migrated database, shared settings provider,
local model clients, headless curator, writer, dialogue memory, pure learner
transitions, APScheduler, three Telegram identities, and the outbox worker.
Run it inside `nix develop --command ...` as shown in README.md.

## Deployment inputs

The layout file uses this shape. These are illustrative IDs, not deployment values:

```yaml
owner_id: 123
supergroup_id: -100123
channel_id: 0
topics: {diary: 1, author: 2, curator: 3, chat: 4,
         library: 5, machine: 6, control: 7}
bots: {mika: MIKA_BOT_TOKEN, curator: CURATOR_BOT_TOKEN, ops: OPS_BOT_TOKEN}
```

Put the three tokens in `.env` in the working directory, using the variable names
from `bots`. [The example file](../config/.env.example) lists the default names:

```dotenv
MIKA_BOT_TOKEN=<mika token>
CURATOR_BOT_TOKEN=<curator token>
OPS_BOT_TOKEN=<ops token>
```

Both `run` and `bot` read `.env` automatically. Select another file with
`--env-file /path/to/bots.env`. Files accept quoted values, comments, and optional
`export` prefixes; shell commands and variable expansion are never evaluated.
The loader only reads credentials and does not modify the process environment.
Already exported variables take precedence, including empty values, which fail
validation. All three tokens must be nonempty and distinct. Logs omit their values.

A missing default `.env` is allowed when all tokens are exported. An explicit
`--env-file` must exist and be readable. The dry run never reads bot credentials.
Keep the local credentials file private (`chmod 600 .env`); `.env`, `.env.*`, and
`*.env` files are excluded from Git and Graphify, with `.env.example` allowed in Git.

Layout validation rejects token strings in `bots`; all seven distinct topic IDs
are required. `group_id` remains an accepted legacy spelling for `supergroup_id`.
`channel_id: 0`, a blank value, or an omitted channel disables public-channel
delivery. Posts then go only to the diary topic. A configured channel ID must be
negative. The filled `config/telegram.yaml` stays local; public templates live
under `mika-startup/startup/config/`.

`library/topics.yaml` is your local catalogue; the entire library is excluded
from Git. CI supplies its own synthetic data. The first topic requires
`ab-01.md` through `ab-06.md`, with matching IDs/topics and nonempty origin keys.
Missing later-topic articles remain absent from curator selection; shortage is
reported explicitly. Startup never generates replacement source articles.

## Autonomous world and optional initial snapshot

`--world-state` is optional. Without it, startup logs neutral PAD and zero initial
sleep debt. The initial snapshot is saved in life_state under runtime.initial;
a restart reuses it and the existing mood/sleep history. A supplied initial file
has this minimal shape:

```json
{
  "initial_mood": {"P": 0.1, "A": 0.0, "D": 0.0},
  "initial_mood_at": "2026-09-21T14:00:00Z",
  "initial_sleep_debt": 0
}
```

The snapshot is initialization data, not an instruction to reset a running
character. All instants must be aware; stored timestamps use UTC. The cycle epoch
still comes from life.yaml. An existing mood history takes precedence on upgrade.

The effective initial timestamp and latest mood history must not be later than
the startup clock. Validation runs before storing the initial snapshot or planning
sleep. Example snapshot dates must be replaced with an actual observation time.
An existing valid stored snapshot takes precedence over the file's initial fields.
Correcting a future timestamp saved by an older release requires a database
backup and an explicit repair; editing the input file alone does not reset it.

`DerivedWorldProvider` implements where(now) from section 26.1: sleep means home;
class days from 09:00 to 14:00 select university/transport with probabilities
0.85/0.15; 14:00–19:00 selects home/cafe/street with probabilities 0.6/0.25/0.15;
other times mean home. Each draw uses the Almaty date as its seed, so request order
and process restarts do not move the character. No operator file is read in the
default mode. Road admission and weather sampling use separate date-based seeds.

`ScheduledSleepProvider` calls the existing plan_bedtime and resolve_wake formulas.
It supplies the latest stored article complexity, or no article contribution when
no rated article exists. It obtains stuck/down labels from schedule.mood_label,
using learner history, quiz rounds, and PAD. It stores plans by their local wake
date, so bedtime jitter across midnight cannot collide with the previous night.
Legacy keys are adopted atomically without changing completed observations/debt.

Completed nights advance sleep debt exactly once through debt_applied. Nights
ending before the initial snapshot are already covered by its initial debt.
Missed days are processed chronologically. Future planning uses the configured
night window; an earlier bedtime is detected before that window as well.

DatabaseMoodProvider builds baseline history outside MoodModel and consumes due
resolutions through record_event. The optional weather provider retains its API,
seasonal fallback, and relevance filtering.

## Temporary overrides

The same file may additionally contain location, road_roll, and sleep intervals.
Each interval has planned_bedtime, bedtime, wake, and reason, as before. Overrides
require an aware valid_until; observed_at optionally sets their start, otherwise
the initial snapshot timestamp is used. Validity is start-inclusive and
end-exclusive. Fields that are absent continue to use automatic providers.

The file is reread for overrides; update it with an atomic rename. Expired fields
fall back to derived location, road admission, and sleep scheduling. They do not
stop generation or defer publication. Expired, uncompleted sleep overrides are
removed and replanned; completed observations and charged debt remain history.
A conflicting override cannot overwrite an already completed night.

## Recovery and settings

Migrations are automatic; the former interface-storage opt-in is unnecessary.
Settings are read at the next operation. Disabling mood records the configured
neutral state and removes queued resolutions through the mood mutation API.

Learner events/actions and publication intents are durable. Interrupted running
actions and uncertain sends require operator reconciliation. Ordinary command,
chat, and library jobs are ephemeral; this policy is logged at startup. JSONL is
the full record; the Telegram log mirror is a convenience view.

Quota failures pause curator actions until the next Almaty midnight. Authentication
failures stop curator actions until `/exam`. Transport and unknown failures retry
after six hours up to three times. Unrelated actions are not globally paused.
`/pause` stops learner scheduling while preserving the outbox.

Live Telegram publication has not been verified without real deployment IDs and
tokens. The offline scenario and mocked live-composition test exercise wiring;
see VALIDATION.md for the separate real local-model checks.
