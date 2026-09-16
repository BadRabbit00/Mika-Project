# Publication, curator, and Telegram interfaces

## Durable publication

`Publisher.enqueue_post` atomically queues a validated draft and its destinations.
The diary is the primary destination; a public channel can be a second delivery.
Each destination gets the existing unique `(post_id, channel)` idempotency key.
Changed payloads under the same key fail instead of modifying an in-flight post.

`OutboxWorker.run_once` claims one due row inside an immediate SQLite transaction,
increments attempts, and clears `next_try_at` before the network call. Other
workers and restarted processes cannot claim it again. Success records the
Telegram receipt; the primary receipt updates the post in the same transaction.
The outbox retains separate receipt IDs for each destination.

An explicit flood-control rejection can schedule a retry. A timeout, ambiguous
network failure, cancellation, or process crash leaves the claim uncertain.
`uncertain()` lists such rows, and `reconcile(id, message_id=..., at=...)` accepts
an externally verified receipt. Automatic resend is prohibited in this state.
This also means a crash before the network call can require manual attention.

Section 37.2 overstates the guarantee: the
[Telegram Bot API](https://core.telegram.org/bots/api#sendmessage) has no supplied
idempotency-key parameter and cannot commit atomically with SQLite. The sender
prevents automatic duplicates; it cannot guarantee both delivery and no duplicates
through every network failure. See TODO(OUTBOX-DELIVERY).

Every payload carries the originating `trace_id`, which is rebound around worker
execution and survives process restarts. Operational cards, attachments, edits,
and pins use the same outbox with an explicit stable operation key.

The publication tests use an asynchronous fake transport, including concurrent
workers, restart after success, remote acceptance followed by timeout, and failure
to commit a received message ID. No live Telegram message has been sent.

## Headless curator

The devShell pins Claude Code through the flake's nixpkgs input and permits that
specific unfree package. `ClaudeCodeBackend` calls `subprocess.run` with argument
arrays, a timeout, `check=True`, and `stdin=DEVNULL`. It uses mode-600
`NamedTemporaryFile` files for both the request and system text, inside a private
temporary working directory. No project history is resumed.

The command uses `-p @<request-file>`, configured `--model` and `--effort`,
`--append-system-prompt-file`, and `--output-format json`. The file flag preserves
the append behavior while keeping long text out of process arguments. See the
[Claude CLI reference](https://code.claude.com/docs/en/cli-reference) and
[file-reference documentation](https://code.claude.com/docs/en/common-workflows#reference-files-and-directories).
Built-in tools are restricted to reading and web research; write/command tools
and external MCP tools are unavailable. Local settings and skills are excluded.

`extract_json` accepts one object within optional prose/fences. It rejects
duplicate keys, non-finite JSON constants, multiple objects, and Pydantic schema
violations. A schema failure gets one retry with structured validation feedback;
both call costs are logged. Process/timeout errors propagate instead of triggering
an immediate exam retry. `total_cost_usd` is CLI-reported usage cost, not a claim
that a separate subscription charge occurred. Missing cost metadata stays unknown.
Token usage is reported from the CLI when available; no estimates are manufactured.

`Curator` reads the supplied system and exam/grade/select templates. It takes a
snapshot of graph facts, source metadata, and the last 20 topic verdicts. Unknown
graph/source references and invented/repeated article IDs fail before database
writes. Exam rows and the public verdict commit together in `curator_log` before
any publication can be requested. Replaying the same phase and trace returns its
receipt. Corrections remain reviewable proposals pending the exam-lineage contract.

Run a workflow from an explicit JSON context file:

```sh
nix develop --command blogai curator exam \
  --context data/exam-context.json --database data/blogai.sqlite3 \
  --log-file logs/curator.jsonl --timeout 300 --trace-id exam-001
```

For `exam`, the context keys are `topic`, `summary_post`, `given`, `read`, and `n`.
For `grade`, use `topic`, `answers` (exam-row ID to text), and the explicit
`pass_rule`. For `select`, use `topic`, `status`, `topics_map`, `library_index`
(objects with `id` and `topic`), `given_articles`, and `exam_result`.

Every CLI call has a distinct `call_id` under the shared `trace_id`; full inputs,
outputs, durations, usage, cost, and validation failures go to JSONL. The existing
single-row-per-trace `runs` schema is not overwritten to hide multiple calls.
Tests mock the subprocess and inspect the live temporary files, arguments,
cleanup, strict JSON validation, retries, and database validation boundaries.

## Telegram process

The runtime uses aiogram 3 with three `Bot` objects and one `Dispatcher`.
`BotIngress` checks owner, group, topic, and receiving bot before submitting any
work. Diary, Author, and Curator messages are never consumed as model input.
Chat sessions belong to step 11. Library documents and control commands enter a
bounded background queue; CLI/model work never runs synchronously in handlers.

`TOPIC_ROLES` follows section 23.2. The explicit user request additionally permits
owner commands in Machine and private chat with ops. Other users receive no reply.
Callback actions check the same owner and routing boundaries. Draft buttons are
Publish and Regenerate; published posts have the requested defect button.
Defect reasons are accepted in Control or DM for one minute, using a category and
reason on one line. The Diary message handler remains isolated during this flow.

Supply `MIKA_BOT_TOKEN`, `CURATOR_BOT_TOKEN`, and `OPS_BOT_TOKEN` in the environment.
The required deployment YAML contains:

| Field | Required value |
| --- | --- |
| owner_id | Your positive Telegram user ID |
| group_id | The negative supergroup chat ID |
| channel_id | Optional negative public-channel ID |
| topics | Mapping of diary, author, curator, chat, library, machine, control to seven distinct positive thread IDs |

No deployment IDs or credentials are inferred, and no supplied config is edited.

```sh
nix develop --command blogai bot \
  --layout config/telegram.yaml --database data/blogai.sqlite3 \
  --library library --log-file logs/blogai.jsonl
```

The layout file above must be supplied before startup. The outbox worker runs
alongside polling. A delivery-worker failure terminates the supervised runtime;
it does not silently leave polling active with a dead sender. Pending outbox rows
remain in SQLite. Queue statuses and exceptions are logged with the original
event trace, including work dispatched through `asyncio.to_thread`.

### Owner operations

- `/state`: stored topics, counters, pending/uncertain deliveries, and an explicit
  marker for the later learning-state contract.
- `/graph`, `/graph stats`, `/graph search <text>`, `/graph <id-or-name>`: SQL/FTS
  views of nodes, edges, and source references.
- `/set`, `/set <group>`, `/set <key>`, `/get`, `/help`, `/config`: generated registry
  metadata, current values, ranges, descriptions, and persisted change metadata
  when storage is configured. `/set` opens group buttons; group views link to
  individual keys and hide locked settings. Long results are attached without
  truncation, retaining their navigation keyboard.
- `/set <key> <value>`, `/reset`, `/diff`: strict types, ranges, enums, and locked
  fields; persistent changes require the explicit override storage contract.
- `/health`: database/outbox state, CLI availability, Telegram, and both local
  HTTP health endpoints. This command runs probes in the background queue.
- `/defects [week]`, `/invalid <post> <category> <reason>`: invalidation records.
- `/preview`, `/publish`, `/regen`, `/trace`: draft actions and complete trace data.

The standalone runtime has no current world/mood regeneration provider. Its
Regenerate action returns TODO(POST-REGENERATION); application callers can inject
an async provider. This is not reported as a completed generation.

Markdown uploads use aiogram's async download API, then validate UTF-8,
frontmatter, required source fields, and `origin_key` in a worker. Admission uses
an atomic no-overwrite link under the source ID. Replays are safe; changed content
under an existing ID and path traversal are rejected. The configured extractor
then runs in the same job/trace. Downloads never enter a chat context.

### Defects and explicit storage contracts

Invalidation records a reason, excludes the post's narrative entries, and keeps
the original post text. A defect card and its dependent pin use the outbox; the
pin waits for the card's receipt. Existing public deliveries receive an annotation
through an edit operation, rather than being deleted.

The architecture omits settings and provenance schemas. The reviewable proposal
is [interface-storage.sql](interface-storage.sql); it is not an installed migration.
Tests explicitly install its first three tables to verify restart persistence
and exact lineage propagation. If those tables are installed by an approved
migration, `--interface-storage` enables `SQLiteSettingsStore` and
`SQLiteLineageStore`. This flag does not create tables. The separate runs proposal
also requires adapting callers and must not be applied as an ad hoc live change.

Without that contract, `/set` writes are rejected explicitly, and defects report
that derived-node/thread propagation is unavailable. With explicit lineage,
only linked threads become stale and linked nodes become suspect; topic similarity
is never treated as evidence of derivation. A durable curator-review queue remains
an open architecture requirement.

### Operational logs

Every queued event has a stable trace ID. Existing extraction and quiz operations
inherit it, writer calls have separate attempt IDs under it, and publication
payloads preserve it across restarts. Full JSONL always stays on disk.

`OpsMirror` sends short event cards from ops to Machine. `full` verbosity adds the
complete JSON attachment, honoring the supplied attachment/thought settings.
`quiet` keeps errors and state transitions. Outbox/mirror bookkeeping is excluded
from the mirror itself to prevent recursive log delivery. Secret fields and the
runtime token values are redacted at the logging boundary.

The job queue and events waiting for the log mirror are process-local; automatic
replay of those events is an explicit TODO. Durable publication is handled by the
outbox. No live Telegram deployment or paid curator invocation is claimed by the
mocked integration tests.
