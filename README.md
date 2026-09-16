# Mika / BlogAI

A Python 3.12 application for grounded learning, isolated post generation,
PAD mood simulation, dialogue sessions, and Telegram publication. The architecture
is in [ARCHITECTURE.md](ARCHITECTURE.md); approved corrections take precedence in
[docs/DECISIONS.md](docs/DECISIONS.md).

## Development and verification

Dependencies are locked with uv and built through uv2nix. Run Python and developer
tools inside the flake devShell:

```sh
nix develop --command pytest -q
nix develop --command ruff check src tests scripts
nix develop --command ruff format --check src tests scripts
nix develop --command graphify update .
nix flake check
```

After changing dependencies:

```sh
nix develop .#bootstrap --command uv lock
```

Work on feature branches and merge verified changes into `develop`. `main`
contains the initial instructions. Configuration is in `config/`; all model
instructions and examples are files in `prompts/`.

## Offline integration run

```sh
nix develop --command python -m src.cli run --dry-run
```

This uses a fresh disposable SQLite database, a virtual Almaty calendar, three
synthetic articles, and mocked external services. It exercises extraction,
self-quiz, validated writing, and curator exam assignment. No posts or paid model
calls occur. Add `--workdir <new-directory>` to retain evidence.

## Live application

Run separate llama-server services: generation on port 8080 and embeddings on
8081. The embedding model must match the stored vector identity. The flake exposes
`embedding-model`; rebuild vectors explicitly with `/graph reindex` after a change.
Host llama-server processes are the allowed exception to the devShell rule.

```sh
nix develop --command python -m src.cli run \
  --layout /path/to/telegram.yaml \
  --database data/mika.db \
  --log-file logs/mika.jsonl
```

Provide `MIKA_BOT_TOKEN`, `CURATOR_BOT_TOKEN`, and `OPS_BOT_TOKEN` in `.env` in the
working directory; [config/.env.example](config/.env.example) lists the keys.
Both `run` and `bot` load this file automatically. Use `--env-file /path/to/bots.env`
to select another file. Exported environment variables take precedence.

The supplied catalogue requires the six real first-topic articles and layout IDs.
World and sleep are autonomous
by default; --world-state optionally supplies an initial snapshot and expiring
overrides. Without it, startup logs neutral PAD and zero initial sleep debt.
Their contracts and startup checks are in [docs/LIVE_RUNTIME.md](docs/LIVE_RUNTIME.md).
The deployment gaps are listed in [docs/TODO.md](docs/TODO.md).

## Boundaries

- SQLite uses WAL, foreign keys, ordered migrations, and bounded lock retries.
  Models return data; validated Python code performs writes.
- Python instants are aware Asia/Almaty datetimes; SQLite stores UTC strings.
  Publication dates and calendar keys are dates, not invented midnight instants.
- Token counts come from llama-server `/tokenize`, including full rendered chat
  templates and token five-gram prompt-echo checks.
- Writing calls are stateless. Off-topic contexts exclude technical material;
  self-quiz questions receive names only. Dialogue history is scoped to its channel.
- Mood changes only through `record_event` or `fire_trigger`. Stored PAD values
  use four decimal places. Prompts receive descriptions, never PAD coordinates.
- Outbox intents survive restart. Uncertain sends require manual review and are
  never automatically resent. Telegram and SQLite cannot commit atomically.
- Every operation carries a shared `trace_id`; each model call has its own
  durable `call_id`. Full JSONL logs remain authoritative.

## Operations

Initialize storage with `nix develop --command blogai init-db --database <path>
--log-file <path>`. Additional CLI commands are `extract`, `quiz`, `curator`, and
`bot`; inspect their `--help` inside the devShell.

Owner commands include `/state`, `/graph`, `/graph reindex`, `/trust`, `/set`,
`/health`, `/outbox review`, `/defects`, `/pause`, `/resume`, `/exam`, `/facts`,
and `/forget-fact`. Destructive personal-memory operations require an expiring
confirmation. Fact listings go to the owner privately.

See [interfaces](docs/INTERFACES.md), [writing](docs/WRITING.md),
[mood and sleep](docs/MOOD_AND_SLEEP.md), [sessions](docs/SESSIONS_AND_RUNNER.md),
and [validation evidence](docs/VALIDATION.md).
