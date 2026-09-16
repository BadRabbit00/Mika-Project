# Sessions and application orchestration

## Dialogue sessions

`ChatService` keeps one active owner session per channel (`topic` or `dm`).
Opening, appending, and finalizing sessions use the existing SQLite tables.
Message trace IDs make replay return the stored reply. The complete turn log
remains available after context compression and closure.

`ChatContext` reads the supplied session and mode instructions. Only topical
requests receive graph nodes; only personal requests receive `life.yaml` data.
History is shared between modes inside one session, as required by section 33.4.
Chat retrieval explicitly requests section 22's 0.55 similarity cutoff.
It never crosses channels. Personal facts are explicitly shared by owner.
Private dialogue events stay in local JSONL and are excluded from the group log
mirror. Chat narrative rows do not enter the existing public-writing profiles.
Unknown public questions open question threads. Unknown DM questions stay in
their session log because the threads schema has no channel provenance.

The default context budget is 16,000 server tokens. Overflow compresses only the
head, retains the last 12 turns in full, and checks the complete rebuilt request
again. An oversized protected tail raises `ContextOverflow`. The JSON value in
`sessions.summary` stores the text, compressed-through turn index, closure receipt,
and first-compression token count. This is a checkpoint encoding in an existing
column; archived turns are never replaced or deleted.

After six hours without turns, the session closes before memory extraction.
Failed finalization remains recoverable from SQLite after restart. The summary,
filtered facts, and finalization receipt commit together. Facts must quote a
user turn from that session, use an allowed kind, contain a direct first-person
statement, and pass the conservative sensitive-content filter. Model statements
and inferred facts are rejected. These lexical checks cannot prove semantic
truth or exhaust every euphemism; see the TODO list.

`ChatGateway` handles `/chat on`, `off`, `status`, `reset`, and `export` through
the existing background queue. Exports contain a JSONL header and complete turn
records. `/facts` reads personal memory. A supplied `chat_factory` attaches the
gateway to `run_telegram`; expiry runs through the same queue once per minute.
Model work never executes in aiogram handlers.

Exports measure section 33.5's cosine between the first and last assistant reply,
as well as citation coverage, unknown-reply frequency, and tokens before the first
compression. Embedding failure leaves the transcript export available with an
explicit unavailable metric. No formula is supplied for mood drift.

`ChatMemory` reads `facts_extract.md` and validates grammar-constrained candidates
in code. The required `dialog_summary.md` is absent from the supplied prompts;
until that contract is supplied, compression/finalization needs an injected
summarizer. No existing prompt or config file has been modified.

The current world, sleep history, cycle epoch, and mood baseline inputs must
come from an explicit context provider. Missing inputs stop generation; the
gateway does not manufacture a location, sleep schedule, or cycle epoch.

## Learner transitions and effects

`orchestrator.transition(state, event)` is a pure function. Frozen values carry
all state, event identities, timestamps, article counts, and quiz outcomes.
There are no database, clock, logger, filesystem, or model calls in the module.
The caller supplies thresholds from the existing settings registry.

The transition table follows section 3, including WAITING as a normal state,
the minimum five-question quiz, and the literal 2+4 / 4+2 exam allocations.
Extraction can complete while ANNOUNCED without advancing the visible reading
phase. The reading event releases ingestion only when extraction has succeeded.
`SUMMARY_READY` in section 21.2 is treated as the summary-ready condition of
section 3's SUMMARY phase when selecting a post kind.

`ActionRunner` calls injected async handlers. `SQLiteLearningStore` commits an
event receipt, its new learner state, and action intents in one transaction.
Repeated event IDs with unchanged data do nothing; changed payloads are rejected.
Sibling actions have explicit predecessor receipts. Only one worker can claim
an action. Generation delays, deferred reading events, retries, and action
outcomes are durable under the proposed schema.

The concrete proposal is [learning-storage.sql](learning-storage.sql).
The architecture supplies no learner/action schema, so this is deliberately
not an automatic production migration. Tests and the dry run install it in new
isolated databases. Application code requires that contract to exist already.

Interrupted running actions remain `uncertain`; pending and deferred actions
retain their schedule. `recover_interrupted()` requires exclusive startup.
Do not automatically repeat an uncertain model side effect. Writer attempts
carry their durable action ID, allowing an adapter to reuse a saved draft.
The publication outbox retains its independent receipt protocol.

Local transport exhaustion preserves the action and pauses admission for fifteen
minutes. The HTTP client performs its existing three backoff retries first.
Curator timeouts are deferred six hours up to three times after the initial call,
then remain failed for operator review. Alerts are injected async callbacks.
Subscription-limit classification is still undefined in the architecture.

`LearningPipeline` connects the actual extractor, retriever, quiz, writer, and
curator. Grading requires an explicit pass rule; article selection requires a
supplied topic/library catalogue. Invalid or unsupported graph answers are never
submitted as verified exam answers. Saved answers and curator receipts are reused
on retries. Exam receipts use a summary-specific identity linked to the root
trace, so a remedial exam cannot reuse an earlier verdict.

Validated selection preserves assigned article IDs in the state on a topic
switch. The caller admits incoming articles through the same `article` event.
Remediation extracts the selected material before reassessment. Curator public
comments pass to the supplied `on_curator` callback; a library shortage alerts
the operator and leaves the learner waiting for materials.

## Activity scheduling

`Rhythm.from_file` reads an explicit rhythm file. No production fallback is
invented for the absent config/rhythm.yaml. The offline fixture uses section 6's
example literally. Pauses use the specified bounded `lognormvariate(mu, sigma)`.
The announcement-to-reading pause additionally respects section 3's 10–25 minute
bounds; a closed activity window defers it to the next session.

`ActivityScheduler` uses APScheduler 3's
[AsyncIOScheduler](https://apscheduler.readthedocs.io/en/3.x/modules/schedulers/asyncio.html)
with `ZoneInfo("Asia/Almaty")`. It checks activity windows, quiet hours, session
capacity, and the supplied sleep/class/road blackout before queuing generation,
then checks the time and blackout again immediately before execution.
`SQLiteReservations` preserves session capacity across process restarts.
`ActionRunner(generation_delay=...)` records the caller's sampled next-generation
time atomically with completion. APScheduler itself keeps only dispatch timers;
the proposed SQLite action store owns durable work.

`LearningApplication` connects APScheduler polling to the action runner and the
background queue. Its asynchronous `start()` performs exclusive restart recovery
before polling. `run_telegram(..., learning_factory=..., chat_factory=...)`
can host both services alongside the three bots. These factories must supply
the explicit current-world inputs, storage contract, and missing summary prompt.

`next_post_kind` follows the section 21.2 priority order. `load_threads` marks
threads older than five days stale in a transaction. Recent confusion suppresses
lower-priority posts until the twenty-hour threshold.

## Offline executable check

```sh
nix develop --command python -m src.cli run --dry-run
```

Run from the repository root. The command uses the real component implementations,
SQLite, UTC adapters, validation, and the task queue. Only external services are
fixtures: HTTP requests still go through `/tokenize`, `/apply-template`, completion,
and embedding endpoints on an `httpx.MockTransport`; its reversible artificial
tokenizer is test data, never a production token estimator. Curator replies pass
the same Pydantic and reference validators as real replies.

The scenario finishes at EXAM with a validated summary draft and five stored exam
questions. No external publication occurs. Supplying an existing database path
is rejected. Default temporary artifacts are removed after the report; an
explicit fresh `--workdir` retains them for inspection.
