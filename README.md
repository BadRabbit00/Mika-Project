# BlogAI

Step 1 of [ARCHITECTURE.md](ARCHITECTURE.md): the Python 3.12 project scaffold,
SQLite storage, schema migrations, time rules, and executable tests.

## Development

Nix builds the environment from `uv.lock` through uv2nix. Python and developer
commands run inside the devShell. Python downloads by uv are disabled.

```sh
nix develop
pytest -q
ruff check src tests
ruff format --check src tests
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
Only dependencies needed for step 1 are installed in the application environment.

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
Add new migrations instead of editing released ones.

Use `enqueue_outbox` inside the same transaction that saves a post. Its key is
the unambiguous JSON encoding of `[post_id, channel]`. Repeating the same intent
returns the original row ID and preserves retry/delivery metadata. A changed
payload under the same key raises an error. This stage only persists intent.
The unresolved remote-delivery crash window is TODO(OUTBOX-DELIVERY).

`insert_mood` validates and rounds the six PAD/baseline values with
`round(value, 4)`, then appends a snapshot. SQL constraints reject unrounded
values, and an update trigger preserves history. Mood formulas remain in step 4.

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
`prompts/`, and the duplicate root YAML files remain unchanged.

After code changes, regenerate and inspect the Graphify graph:

```sh
nix develop --command graphify update . --no-cluster
nix develop --command graphify query 'Database transaction enqueue_outbox'
```

The generated `graphify-out/graph.json` is versioned. Runtime databases, logs,
backups, Nix results, and tool caches are ignored. Explicit database snapshots
outside runtime directories can be tracked intentionally.

## Open requirements

[docs/TODO.md](docs/TODO.md) records architecture gaps, including the undefined
learning-state schema, embedding format, trace identity, date-only metadata,
and outbox delivery guarantees. Find them with:

```sh
rg -n 'TODO\(' docs src tests CLAUDE.md
```

Later-stage invariant names are recorded in `tests/test_stage_contracts.py`.
That gate requires behavioral tests before those modules are introduced; it
does not claim to test behavior that has not been implemented.
