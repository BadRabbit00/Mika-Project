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
channel_id: -100124
topics: {diary: 1, author: 2, curator: 3, chat: 4,
         library: 5, machine: 6, control: 7}
bots: {mika: MIKA_BOT_TOKEN, curator: CURATOR_BOT_TOKEN, ops: OPS_BOT_TOKEN}
```

Put tokens in the named environment variables. Layout validation rejects token
strings in `bots`; all seven distinct topic IDs are required. `group_id` remains
an accepted legacy spelling for `supergroup_id`.

`library/topics.yaml` is the supplied catalogue. The first topic requires
`ab-01.md` through `ab-06.md`, with matching IDs/topics and nonempty origin keys.
Missing later-topic articles remain absent from curator selection; shortage is
reported explicitly. Startup never generates replacement source articles.

## Current world observations

`--world-state` names a JSON file supplied by the operator. This example describes
its structure only; it is not a production default:

```json
{
  "location": "дом",
  "observed_at": "2026-09-21T14:00:00Z",
  "valid_until": "2026-09-21T16:00:00Z",
  "road_roll": 0.99,
  "initial_mood": {"P": 0.1, "A": 0.0, "D": 0.0},
  "initial_mood_at": "2026-09-21T14:00:00Z",
  "initial_sleep_debt": 0,
  "sleep": [{
    "planned_bedtime": "2026-09-20T20:00:00Z",
    "bedtime": "2026-09-20T20:00:00Z",
    "wake": "2026-09-21T02:50:00Z",
    "reason": "alarm"
  }]
}
```

Provide observed or explicitly planned sleep intervals for the relevant calendar
horizon. The file is read again for current location and sleep inputs. Update it
atomically when observations change. `valid_until` is the caller's assertion of
validity; expired observations stop generation and defer public delivery. The
runtime does not invent location or extend a sleep schedule from stale inputs.
The approved cycle epoch comes from life.yaml, independently of the initial PAD
observation. Initial PAD is used only when no persisted mood snapshot exists.

`StoredSleepProvider` imports intervals idempotently and applies each completed
night's debt once. `DatabaseMoodProvider` assembles exam/correction history,
waiting duration, quiz streak, and semester pressure outside the mood model.
It consumes due trigger resolutions through `record_event` before reading PAD.
`ObservedWorldProvider` supplies calendar facts, objects, blackout, and an optional
relevance-filtered weather observation.

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
