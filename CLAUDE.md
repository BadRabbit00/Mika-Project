# Implementation rules

Read ARCHITECTURE.md before making changes. Follow the delivery order in
section 38.3. Only step 1 (storage, migrations, time, and its tests) is implemented.

- Keep main limited to the initial architecture. Work on feature branches and
  merge completed, verified changes into develop.
- Run Python, uv, tests, and developer tools inside the flake devShell.
- Use Python 3.12, uv2nix, and the standard-library sqlite3 module. Do not add an ORM.
- Keep config/ and prompts/ unchanged. Write new comments, logs, and documentation
  in English. Never put model prompts or model settings in Python source.
- Use timezone-aware Asia/Almaty datetimes internally. Store UTC ISO-8601 strings
  in SQLite. Reject naive datetimes. Never call datetime.now() without a timezone.
- Count model tokens with llama-server /tokenize, never an estimate.
- Keep the state machine a pure transition function; keep model calls out of
  Telegram handlers and use a task queue when those stages are implemented.
- Write the applicable invariants from sections 18.2 and 38.2 as tests before
  implementing each stage. Later-stage contracts are tracked in docs/TODO.md.
- Preserve the formulas in sections 28 and 35 literally. Round stored PAD
  values with round(value, 4). Use 200-token overlap and norm_hash deduplication
  when extraction is implemented.
- Use transactional outbox insertion. An idempotency key cannot resolve the
  remote-send/local-commit crash window; see TODO(OUTBOX-DELIVERY).
- Update Graphify after code changes: nix develop --command graphify update .
- Record incomplete or conflicting requirements as searchable TODO entries;
  do not silently invent missing domain rules.
