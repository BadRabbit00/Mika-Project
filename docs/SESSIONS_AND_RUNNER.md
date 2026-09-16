# Sessions and orchestration

Each conversation channel (topic or dm) has a separate session and transcript.
Graph, mood, world, and owner facts are shared; conversation histories are not.
Topical replies receive only retrieved graph facts and must cite supplied nodes.
Personal replies use life data. Unknown replies must acknowledge missing knowledge.
Rejected answers and facts are not stored as successful results.

The default context budget is 16,000 exact server tokens. Overflow compresses the
head with dialog_summary.md and keeps the final twelve turns intact. The rebuilt
request is checked again. After six idle hours, closure is recorded before model
work; failed summarization/fact extraction can finish after restart. Personal
facts require direct user evidence and pass the approved conservative filters.

Private unknown questions create dm threads. Public context and scheduler loaders
select public threads only. Personal-memory deletion is owner-confirmed and expires
after sixty seconds; listings are delivered privately.

Session exports include first/last reply cosine, citation coverage, unknown-reply
counts, compression counters, and PAD measurements. Mood drift is
`norm(PAD_end - PAD_start) / (2 * sqrt(3))`, with separate P/A/D deltas and the
number of observed band changes over all axes, including stored mood events between replies and intermediate turns.
PAD observations are rounded in SQLite and excluded from model histories. A
closure retry preserves the original end observation. Old sessions without PAD
observations report an unavailable metric instead of inventing one.

`orchestrator.transition(state, event)` remains a pure function. SQLiteLearningStore
commits each event, new state, and action intents atomically. Duplicate event IDs
are idempotent; changed payloads are rejected. ActionRunner executes side effects
through injected handlers. Interrupted running effects remain uncertain.

The supplied rhythm file defines sessions and literal lognormal pauses. Almaty
windows are aware datetimes. Session jitter is stable for a calendar day and slot;
settings changes affect subsequent scheduling. Blackout is checked at admission,
again before queued generation, and before public delivery. Private threads never
influence public post selection.

The live composition, autonomous world, and temporary overrides are described in
[LIVE_RUNTIME.md](LIVE_RUNTIME.md). The one-command dry run uses isolated fixtures
and mocked external boundaries; it is not evidence of live Telegram delivery.
