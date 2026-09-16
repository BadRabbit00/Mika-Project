# Validation evidence

## Bounded local reasoning: 2026-09-16

- `nix flake check --no-update-lock-file --print-build-logs`: passed on
  x86_64-linux, with 428 tests, lint, formatting, and the offline application
  reaching EXAM without external deliveries. aarch64-linux was not built here.
- `nix build .#default --no-link --no-update-lock-file`: passed.
- The 23 new regression cases cover separate reasoning/final-answer limits,
  actual context capacity, lazy grammar activation, malformed/truncated output,
  runtime settings, deadlines, receipts, and thought redaction in attachments.
- Live Gemma 4 12B extraction with a 1,024-token thinking limit stopped reasoning
  at exactly token 1,024 and ended normally after 1,125 generated tokens total.
  Two claims passed the existing grounding validator. The live embedding endpoint
  returned a 768-dimensional vector.
- Shorter-budget probes also produced empty claims or exhausted the total output
  limit; these were rejected. The live probes establish protocol and validation
  behavior, not general factual accuracy or an optimal budget for every task.
- Graphify refreshed the Python graph: 1,600 nodes and 4,101 edges. Its existing
  optional SQL-parser limitation remains.

Live checks used synthetic inputs and temporary storage. No Telegram delivery
or production database change was involved. See [REASONING.md](REASONING.md) for
the per-request protocol and controls.

## Autonomous world: 2026-09-16

- Full devShell pytest run: 365 passed, including 17 autonomous-provider cases.
- `nix flake check`: passed on x86_64-linux, including all tests and lint/format
  checks. The aarch64-linux outputs were not built on this host.
- `nix build .#default --no-link`: passed.
- The one-command dry run reached EXAM with three sources, five answered questions,
  five curator exam questions, and no external deliveries.
- Graphify refreshed the code graph. Operator layout/state files and article data
  are excluded from this graph; the SQL parser limitation described below remains.

Coverage includes no-file startup with logged neutral defaults, persisted initial
state across restart, date-seeded location probabilities, optional override expiry,
the existing bedtime/wake formulas, stuck/down labels, latest article complexity,
chronological debt accounting over two weeks, and migration of legacy night keys.
Midnight crossings and unusually early bedtimes retain sleep blackouts. Expiring
a distant future override cannot skip intervening nights. Completed nights retain
their debt receipts. The CLI accepts a live run without --world-state.

These checks use isolated databases and mocked external boundaries. They do not
publish to Telegram or call a model. The supplied local startup files were not
modified or included in the implementation commit.

## Approved decisions: final verification, 2026-09-16

- `nix develop --command pytest -q --tb=short --show-capture=no`: 348 passed.
- `nix flake check`: passed on x86_64-linux, including the complete pytest suite,
  locked dependencies, Ruff lint/format checks, and Nix formatting. The host
  does not build aarch64-linux checks.
- `nix build .#default --no-link`: application environment built successfully.
- `nix develop --command python -m src.cli run --dry-run`: reached EXAM with
  three sources, three claims, five answered questions, a summary draft, and
  five curator exam questions. It made 45 fixture tokenize calls and no external
  deliveries. The command uses mocked external services.
- Graphify updated the code graph: 1,477 nodes and 3,735 edges. The Python AST
  graph is available; SQL AST extraction is unavailable in the supplied Graphify
  package because its optional SQL parser is absent.

Regression coverage includes automatic migrations, live composition, curator
quota/authentication gates, publication blackout, runtime settings, and shutdown
that drains jobs before closing their providers. Code markers and docs/TODO.md
are checked for exact agreement.

Cycle tests cover every length from 26 through 30, fixed non-follicular phases,
the final-five-day late luteal window, and reproducible histories before and after
the epoch. Session exports use the literal denominator 2*sqrt(3), separate PAD
deltas, and band transitions from stored mood events and replies. An excursion
that returns to its starting PAD has zero drift but retains its band changes.
Open sessions do not invent an end observation.

This earlier run still required a world-state file. That requirement is superseded
by the autonomous-world correction below. Article content and real Telegram
deployment are separate operational checks.
No live Telegram delivery is claimed. See [TODO.md](TODO.md).

## Initial validation of architecture steps 2 and 3

Validated on 2026-09-16 with Python 3.12 in the uv2nix devShell.

## Automated checks

- `nix develop --command pytest -q`: 86 tests passed.
- Ruff lint and formatting checks passed for all source and test files.
- `nix flake check`: all three checks passed on x86_64-linux. The aarch64-linux
  outputs were not built on this host.
- `nix build`: the application built; its installed CLI exposes `init-db`,
  `extract`, and `quiz`.
- Graphify was regenerated and its extraction/storage dependencies inspected.

Tests cover three distinct article fixtures, per-source triple deduplication,
grounding, self-relation rejection, embedding merging, atomic rollback, Unicode
chunk boundaries, Unicode separators inside JSON strings, exact tokenizer budgets
including boundary tokens, and server
truncation signals. Quiz coverage includes context isolation, empty retrieval,
strict citation subsets, suspect-node checks at commit, topic isolation, top-six
selection, replay behavior, and round thresholds. These tests use mocked model
responses and do not measure extraction quality.

## Live transport and self-quiz

The generation service used the supplied local
`gemma-4-12b-it-uncensored-Q4_K_M.gguf` on port 8080. The embedding service used
the revision-pinned EmbeddingGemma Q8_0 package from `flake.nix` on port 8081.
Both were separate host llama-server processes, with reasoning disabled on the
generation service. Python clients ran inside the devShell.

Three source excerpts came from Linux kernel documentation:

- [Yama](https://docs.kernel.org/admin-guide/LSM/Yama.html).
- [No New Privileges Flag](https://docs.kernel.org/userspace-api/no_new_privs.html).
- [Seccomp BPF](https://docs.kernel.org/userspace-api/seccomp_filter.html).

For the isolated quiz fixture, extraction generation was mocked with three
explicit test claims. Tokenization, embeddings, validation, merging, and SQLite
storage used the implemented code and real local servers. This produced three
sources, six nodes, three edges, and three claims. It is not a successful live
extraction result.

On that fixture graph, real question generation produced five questions. The
answer model returned empty citations for all five because the three stored
facts did not answer those questions. The round correctly failed with five
`no_knowledge` verdicts. A separate supported question about Yama and Linux
Security Module received `answered` with two valid retrieved citations.

An absent-topic question, using deliberately unreachable model URLs, returned
`no_knowledge` with no generation events. The recorded live question context
contained the six node names and no stored summaries.

Local evidence is under the ignored `data/live/` directory:

- `quiz-fixture-results.jsonl` and `quiz-fixture-events.jsonl`.
- `quiz-positive-results.jsonl` and `quiz-positive-events.jsonl`.
- `quiz-empty-results.jsonl` and `quiz-empty-events.jsonl`.

## Approved grammar and live rerun, 2026-09-16

The approved grammar makes only the final newline optional. The rerun used the
three kernel excerpts above and real generation, tokenization, and embedding
servers. Yama accepted 2 claims and rejected 2; no-new-privileges accepted 1 and
rejected 3. Seccomp first hit the output limit without committing anything. Its
retry terminated and rejected all 3 claims. These are transport and validation
results, not an extraction-quality guarantee. No rejected claim entered the graph.

Evidence: data/live/decisions-extraction-events.jsonl and
data/live/decisions-extraction-retry-events.jsonl (ignored runtime files).
The storage and knowledge gate passes 79 tests, including migration upgrades,
calendar publication dates, atomic reindexing, independent source trust, and
multiple call receipts under one shared trace.

## Corrected mood model

Cycle lengths 26–30 retain 5 menstrual, 3 ovulatory, and 12 luteal days. All
variation belongs to the follicular phase; late luteal means the final 5 days.
Cycle histories reproduce across restarts and dates before the epoch.

The 14-day simulation starting 2026-09-21 (seed 42) produced 336 hourly samples
and 60 events. Mean PAD: 0.2034, -0.0291, 0.0234; no extreme samples or runs.
Evidence: data/decisions-mood-simulation/{report.json,samples.csv,transitions.csv}.

A live question on the newly extracted graph returned answered with two verified
Yama citations; data/live/decisions-quiz-events.jsonl records the request.

## Approved writing profiles

The writing gate passes 142 tests. Core and study personas are separated, daily
and insight templates are enabled, and every writing mode has an explicit
file-based envelope and example. The server consumes the closing stop word;
the client restores it only when the server confirms that exact stop word.

Three real Gemma calls on the kernel excerpts each produced a valid draft on
the first attempt. No envelope repair was enabled. These were generation tests
with explicit fixture sleep/location inputs, without publication. Evidence:
`data/live/decisions-writing-report.json` and the corresponding JSONL log.
Typed context snapshots retain PAD bands, world inputs, and memory identities
for regeneration from current observations.

## Dialogue decisions

The dialogue/storage/writing gate passes 117 tests. Session PAD observations are
rounded to four decimals in SQLite and excluded from prompt histories. Exports
include norm(delta)/(2*sqrt(3)), individual P/A/D deltas, and every observed band
transition, including stored events between replies. A full three-axis reversal
measures 1.0; a return trip still records its intermediate band changes after restart.

DM question threads have explicit provenance. Public context loaders exclude
them. Memory compression uses the supplied dialog_summary.md; runtime setting
changes apply to the next chat operation.
