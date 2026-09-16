# BlogAI

Steps 1–10 of [ARCHITECTURE.md](ARCHITECTURE.md): the Python 3.12 scaffold,
storage, extraction, self-quiz, immutable PAD mood, biological time, isolated
contexts, draft writing, world events, three-layer output validation, durable
publication, a headless curator, and Telegram interfaces.
Live generation and prompt-contract limitations are recorded below.

## Development

Nix builds the environment from `uv.lock` through uv2nix. Python and developer
commands run inside the devShell. Python downloads by uv are disabled.

```sh
nix develop
pytest -q
ruff check src tests scripts
ruff format --check src tests scripts
nixfmt --check flake.nix
```

Initialize or upgrade a database, with persistent structured logs:

```sh
nix develop --command blogai init-db \
  --database data/blogai.sqlite3 --log-file logs/blogai.jsonl
```

Build the installed application and run all checks in the Nix build sandbox:

```sh
nix build
nix flake check
```

The packaged command is also available through `nix run . -- init-db ...`.
The default devShell uses editable application code. After adding a dependency,
update its lock inside Nix and enter a fresh shell:

```sh
nix develop .#bootstrap --command uv lock
```

The flake follows the official
[uv2nix development template](https://pyproject-nix.github.io/uv2nix/usage/getting-started.html).
Application dependencies are locked separately from development tools.

## Knowledge extraction

Run two independent llama-server processes: generation on port 8080 and pooled
embeddings on port 8081. The generation model comes from the local model store.
Only `llama-server` itself runs directly on the host; all other commands use Nix.

```sh
llama-server --model ~/.eva/models/gemma-4-12b-it-uncensored-GGUF/gemma-4-12b-it-uncensored-Q4_K_M.gguf \
  --host 127.0.0.1 --port 8080 --ctx-size 32768 --parallel 1 --reasoning off
```

The flake provides a revision-pinned, hash-verified embedding model:

```sh
nix build .#embedding-model --out-link result-embedding
llama-server --model ./result-embedding --host 127.0.0.1 --port 8081 \
  --embedding --ctx-size 2048 --parallel 1
```

Each Markdown article needs YAML frontmatter with `id`, `title`, and `topic`.
Optional metadata includes `origin_key`, `url`, `kind`, `publisher`, `given_by`,
`trust_prior`, and an aware `published_at` timestamp. Date-only values are logged
and stored as NULL because the architecture does not define their time of day.

```sh
nix develop --command blogai extract library/topic/article.md \
  --database data/blogai.sqlite3 --log-file logs/extract.jsonl
```

The client renders each request with `/apply-template` and counts its complete
token sequence through `/tokenize`, including model boundary tokens. The same
IDs go to `/completion`; article chunks use at most 2000 tokens with exactly 200
overlapping tokens. Unicode boundaries are checked before generation.

Claims use `grammars/claims.gbnf`. Code validates the closed relation vocabulary,
distinct normalized entities, and both entity names occurring in the chunk.
Normalized names and FTS candidates are checked before cosine merging at 0.85.
NPY embeddings carry their model identity; incompatible models or dimensions
fail explicitly. Node summaries accumulate validated claim text deterministically.

An article commits in one transaction after every chunk has been parsed and
validated. Malformed or truncated generation commits nothing. Replaying the same
source ID and content skips model calls; changed content requires an explicit
revision policy. Duplicate triples within a source collapse, while independent
sources retain separate claims. Rejected claims and their reasons are logged.
Use `--max-output-tokens` to set an explicit generation cap (default 2048).
The local model's final-newline compatibility issue is tracked as
TODO(CLAIMS-TERMINATION); passing mocked integration tests does not establish
successful extraction quality with that model.

## Retrieval and self-quiz

Run a round for one topic, or answer a supplied question against its graph:

```sh
nix develop --command blogai quiz --topic linux-sandbox \
  --database data/blogai.sqlite3 --log-file logs/quiz.jsonl \
  --min-similarity 0.8 --rrf-k 60

nix develop --command blogai quiz --topic linux-sandbox \
  --question 'How does seccomp use BPF?' \
  --database data/blogai.sqlite3 --log-file logs/quiz.jsonl \
  --min-similarity 0.8 --rrf-k 60
```

The two ranking values above are explicit examples, not architecture defaults.
The architecture omits the fusion rule and retrieval cutoff, so the CLI requires
both values. `RetrievalPolicy` selects reciprocal rank fusion: each matching
ranking contributes `1 / (rrf_k + rank)`, with ranks starting at one. Lexical
matches and cosine matches above the supplied cutoff form the candidate set.
Results contain at most six nodes and only edges whose endpoints were selected.
Suspect nodes and nodes outside the requested topic are excluded.

`SelfQuiz.ask` selects only names and previous questions. It never selects node
summaries or edges. `SelfQuiz.answer` performs retrieval before constructing an
answer request; empty retrieval persists `no_knowledge` without calling the model.
Each model call receives a newly built context. Profile budgets are 2000 tokens
for questions and 3000 for answers, counted by the server tokenizer.

The answer grammar comes from the object in the supplied answer prompt. Code
requires every cited ID to be both retrieved and currently present and usable in
the database. Mixed valid/invalid citations fail. Empty citations mean
`no_knowledge`; malformed answer objects mean `invalid_citation`. Citations prove
which graph records were referenced; they do not prove semantic entailment.

Rounds use the defaults in `config/settings.yaml`: five questions and an answered
fraction of at least 0.6, with a hard minimum of five questions. Repeated questions
are deduplicated per topic. A completed question can be replayed by its ID without
another retrieval or model call. Learning-state transitions remain in step 12.

## Contexts and draft writing

[docs/WRITING.md](docs/WRITING.md) describes the ContextBuilder, Writer,
OfftopGenerator, output validator, and explicit policy inputs.

Every production path builds a fresh context and checks its complete token
sequence using the model server. Technical and off-topic memory have separate
queries. Rejected output retries up to three attempts, then persists a killed
draft. Draft generation does not publish or advance life events.

The supplied shared persona conflicts with off-topic isolation, so the default
fails explicitly. A live Gemma writing smoke test also omitted the required mode
tags in all three attempts and was correctly killed. These prompt contracts need
resolution before successful live draft generation can be claimed. Supplied
configurations and prompts remain unchanged; see the writing TODOs.

## Interaction interfaces

[docs/INTERFACES.md](docs/INTERFACES.md) covers publication recovery, the curator
CLI, three-bot deployment, commands, trace logs, and pending storage contracts.
Telegram tests use mocks; real bot tokens and chat/topic IDs must be supplied
before a live transport check. No live message has been sent by this work.

## Storage

`src/core/db.py` contains the complete explicitly defined schema: 24 ordinary
tables from the architecture, `node_embeddings`, and the FTS5 `nodes_fts` table.
FTS shadow tables are managed by SQLite.

| Area | Tables |
| --- | --- |
| Knowledge | sources, nodes, edges, claims, node_embeddings, nodes_fts |
| Learning | questions, exams, topics, posts, curator_log |
| Life memory | life_journal, life_state |
| Narrative and people | narrative, threads, dialog, people_facts, invalidated |
| World and mood | mood, mood_queue, npc, arcs |
| Sessions | sessions, session_turns |
| Operations | runs, outbox |

Each connection enables WAL and foreign keys. Connections are owned by one
thread and closed explicitly. `Database.run_transaction(callback)` uses
`BEGIN IMMEDIATE`, rolls back failed work, and retries lock failures five times
with a 200 ms delay. The callback must contain only database work; it may replay.
Other errors propagate immediately. `Database.transaction(connection)` provides
a context manager that retries transaction boundaries; its body cannot replay.

Migration 1 creates the tables. Migration 2 adds the documented source
complexity and invalidation columns, synchronizes FTS on insert/update/delete,
backfills the index, and adds lookup indexes. All pending migrations and
`PRAGMA user_version` changes commit together. Reinitialization is safe;
downgrades, newer schemas, and unversioned existing databases are rejected.
Migration 3 adds source content hashes, embedding model identities, and uniqueness
of `(source_id, norm_hash)`. Legacy duplicate claims fail this migration and must
be reviewed explicitly; no existing evidence is silently deleted. Add new
migrations instead of editing released ones.

Use `enqueue_outbox` inside the same transaction that saves a post. Its key is
the unambiguous JSON encoding of `[post_id, channel]`. Repeating the same intent
returns the original row ID and preserves retry/delivery metadata. A changed
payload under the same key raises an error. This stage only persists intent.
The unresolved remote-delivery crash window is TODO(OUTBOX-DELIVERY).

`insert_mood` validates and rounds the six PAD/baseline values with
`round(value, 4)`, then appends a snapshot. SQL constraints reject unrounded
values, and an update trigger preserves history. Mood formulas are in core/mood.py.

## Time

- All datetime-returning helpers return aware `ZoneInfo("Asia/Almaty")` values.
- `require_aware` rejects naive inputs, including tzinfo objects without offsets.
- `to_utc_iso` emits UTC text with six fractional digits and a `Z` suffix.
- `from_utc_iso` accepts validated UTC strings ending in `Z` or `+00:00` and
  returns Almaty time, preserving the instant and microseconds.
- SQLite's datetime adapter uses the same serializer. Timestamp column checks
  reject invalid, naive, and non-UTC text. Reads return strings; use
  `from_utc_iso` explicitly. Python's legacy timestamp converter is disabled.
- Use `Database.connection()` for database access: it registers the validation
  function required by timestamp constraints, including integrity checks.

Tests include both occurrences of Almaty's repeated hour in February 2024.
Date-only source publication metadata needs a policy; see TODO(SOURCE-DATE).

## Logs

The CLI configures structlog JSONL output. The file receives all levels even when
console verbosity is lower. Events include UTC timestamps, schema versions,
lock retries, transaction outcomes, outbox deduplication, and full tracebacks.
Temporary SQLite triggers log before/after row images; BLOBs are encoded as
`blob_hex`. Changes are marked `attempted` and linked by `transaction_id` to
their commit or rollback. Application writes must use the transaction wrappers.

Library callers configure logging at their entry point with
`src.core.logging.configure_logging(path)`. Importing storage does not modify
logging handlers.

## Repository workflow

`main` contains only the initial architecture. Implementation branches merge
into `develop`. Git's attributes file is `.gitattributes`. Supplied `config/`,
`prompts/` remain unchanged.

After code changes, regenerate and inspect the Graphify graph:

```sh
nix develop --command graphify update . --no-cluster
nix develop --command graphify query 'Database transaction enqueue_outbox'
```

The generated `graphify-out/graph.json` is versioned. Runtime databases, logs,
backups, Nix results, and tool caches are ignored. Explicit database snapshots
outside runtime directories can be tracked intentionally.

## Open requirements

[docs/MOOD_AND_SLEEP.md](docs/MOOD_AND_SLEEP.md) describes mood events, cycle
modifiers, persistence, and the reproducible two-week simulation.

[docs/VALIDATION.md](docs/VALIDATION.md) separates automated checks, live quiz
results, and the unresolved live extraction grammar contract.

[docs/TODO.md](docs/TODO.md) records architecture gaps, including the undefined
learning-state schema, embedding upgrades, trace identity, date-only metadata,
and outbox delivery guarantees. Find them with:

```sh
rg -n 'TODO\(' docs src tests CLAUDE.md
```

Later-stage invariant names are recorded in `tests/test_stage_contracts.py`.
That gate requires behavioral tests before those modules are introduced; it
does not claim to test behavior that has not been implemented.
