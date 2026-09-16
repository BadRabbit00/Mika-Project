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
