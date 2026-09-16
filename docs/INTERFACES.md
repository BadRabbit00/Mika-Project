# Publication, curator, and Telegram

## Publication

Publisher atomically inserts delivery intents with a unique post/channel key.
OutboxWorker durably claims before sending. Successful primary delivery records
both the Telegram receipt and the post's publication state in one transaction.
Concurrent workers and restarts do not resend completed or uncertain claims.

An explicit remote rejection may be retried. A timeout or crash around a send
leaves acceptance unknown. `/outbox review` lists uncertain rows; reconciliation
requires an externally verified Telegram message ID. This is the approved
at-most-once policy, not a cross-system exactly-once guarantee.

Public delivery checks current blackout again. A locally deferred send is
rescheduled without touching Telegram. Operational messages remain available.
Each destination retains its own receipt, and every payload carries trace_id.

## Curator

The devShell provides Claude Code. The backend uses subprocess.run in a worker
thread with stdin disabled. Long system and user contexts use mode-600 temporary
files, including --append-system-prompt-file. The CLI is restricted to read and
research tools, without project settings, persistent sessions, or external MCPs.
Model, effort, and timeout are read from the settings provider for each call.

The parser accepts one unambiguous JSON object and validates it through Pydantic.
Schema errors get one immediate repair. Each attempt gets a distinct durable
call_id under the shared trace_id, with CLI-reported cost or an explicit unknown.
Quota, auth, transport, and unknown failures have separate runner policies; see
[LIVE_RUNTIME.md](LIVE_RUNTIME.md).

An exam receives an atomic `exam-YYYYMMDD-NN` identity. Questions reference that
exam. Validated corrections update summaries transactionally and record
`corrected_by = exam:<exam_run_id>`. Grades must cover one complete exam; aggregate
pass requires weighted pass/partial/fail points to reach study.quiz_threshold and
at most one fail. Honest unknown answers remain failures for scoring.
The pass-rule text lives in prompts/curator_pass_rule.md.

Source trust separates reliability and independent claim corroboration. Curator
context includes these measurements. `/trust` exposes them; first-topic evidence
without corroboration remains unknown according to the approved rule.

## Bot boundaries and controls

Three bot identities share one process. TOPIC_ROLES controls ingress. Diary user
messages are ignored; owner commands run in Machine, Control, or ops DM. Model and
network work runs through the background queue, never synchronously in handlers.
The explicit layout and token-variable contract is in LIVE_RUNTIME.md.

Owner operations include state, graph inspection/reindex, trust, settings,
health, outbox review, defects, publication/regeneration, pause/resume, curator
retry, and personal-memory controls. Reindex builds replacement vectors before
an atomic compare-and-swap, so failure cannot leave a mixed index.

`/facts wipe` and `/forget-fact <id>` create an owner-only, single-use confirmation
with a sixty-second timeout. The confirmation snapshots the selected fact IDs.
Listings and deletion receipts go to the owner privately.

Defects preserve public evidence, exclude narrative, mark explicitly derived
threads stale and nodes suspect, and queue suspect nodes for curator review.
Read-only context node IDs are not treated as derivation. The migrated lineage
adapter is always installed.

Regeneration uses Writer's typed snapshot and current provider observations.
The standalone diagnostic bot can run without world providers; generation and
chat then return a missing-provider error. Use the assembled `run` command for
all components.

Settings overrides, lineage, sleep, review, and comment tables are installed by
ordered migrations. Full JSONL logs are authoritative. The Telegram mirror and
ordinary command/chat/library jobs have the approved ephemeral recovery policy.
Only publication and learning have durable work identities.
