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

## Unresolved live extraction contract

The literal section 4.1 structure requires a newline after every claim, including
the final one. With this grammar, the local model repeated claims until its output
limit on both full-document and excerpt probes. Truncated responses committed no
article or graph data. The client recognizes both older `stopped_limit` responses
and the installed server's `stop_type: "limit"` response.

A diagnostic grammar allowing an optional final newline terminated successfully.
[JSON Lines permits omitting the final newline](https://jsonlines.org/), but
section 4.1 leaves its `nl` production undefined. Clarification was requested
before changing the production structure. The committed grammar remains literal;
see TODO(CLAIMS-TERMINATION) in [TODO.md](TODO.md). Full live extraction of three
articles is therefore still unverified.
