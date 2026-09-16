# Validation of architecture steps 2 and 3

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
