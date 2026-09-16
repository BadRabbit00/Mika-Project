# Dialogue routing and personal memory

The owner's September 17 correction supersedes the keyword router in
ARCHITECTURE.md sections 22.1 and 33.4. A technical word in a personal
conversation must not force a graph-only answer. Missing personal information
calls for a natural question to the person, using existing dialogue and memory
first. Technical assertions still need evidence; posts and quizzes retain their
existing contracts.

## Implementation checklist

- [x] Add regression tests before implementation for mixed personal/technical
  messages, missing names, remembered names, delivery isolation and failure paths.
- [x] Let the model request an optional knowledge lookup from conversational
  intent and confirmed history. Do not search the graph for ordinary dialogue.
- [x] Remove the literal missing-knowledge phrase requirement. Keep citation,
  output, exact-token budget and delivery validation.
- [x] Supply file-backed instructions for asking about missing personal data.
- [x] Preserve direct user facts, including a name given in reply to a delivered
  introduction question, across session closure and restart.
- [x] Verify that rejected, unsent and internal-error output cannot become
  model-visible history or personal memory.
- [x] Run the complete Nix checks, update Graphify, and record verification.

Work takes place in an isolated checkout with temporary databases. No running
process, production database, credentials or deployment configuration is changed.

## Runtime behavior

The `chat_route` call receives the bounded conversation, stored personal facts
and current world data. Its grammar permits only an optional search query. Code
validates that request, performs the lookup if requested, and assembles the final
answer context. Model routing and factual recall remain probabilistic; regression
tests verify data boundaries and error handling rather than claiming that every
possible sentence is classified perfectly.

Personal dialogue never needs the embedding service. A failed optional lookup is
marked unavailable so the answer can distinguish a temporary failure from empty
notes. Clarifying replies can omit citations with `confident=false`; any supplied
citations are still checked against the retrieved, nonsuspect nodes. Post and
self-quiz validators are unchanged.

Active sessions use durable user turns and only delivery-confirmed Mika turns.
Personal facts are extracted at session closure, as before. Exact names can come
from an explicit introduction or a short answer to a delivered name question.
The extraction batches keep that question with its answer even across a budget
boundary. Facts remain scoped to the configured person and excluded from posts.

No database migration or reset is required. Existing sessions, personal facts,
graph, life state and outbox remain compatible. The new helper calls use the
existing local model, reasoning limits, tokenization and trace logging.

## Verification, 2026-09-17

- `nix flake check --no-update-lock-file --print-build-logs`: 547 tests passed
  on x86_64-linux, including all 64 chat tests, offline CLI startup, Ruff,
  formatting and lock checks.
- `nix build .#default --no-link --no-update-lock-file`: passed.
- `nix develop --command python scripts/check_repository.py`: passed.
- Gitleaks working-tree scan and actionlint: passed.
- `nix develop --command graphify update .`: 1,953 nodes, 5,138 edges,
  167 communities. The existing optional SQL parser warning concerns two
  unchanged fixtures; no dependency was installed outside Nix.

The new regressions were observed failing before implementation. They cover
mixed technical/personal messages, optional retrieval failures, name questions,
short name answers, session restart, source and person isolation, exact budgets,
malformed routing requests, unsupported citations and rejected output.
