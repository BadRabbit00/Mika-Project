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
Direct Russian first-person clauses can omit a pronoun: project, study and
preference statements remain eligible, while third-person descriptions do not
become facts about the speaker. Verbatim source and sensitive-data filters still
apply.

No database migration or reset is required. Existing sessions, personal facts,
graph, life state and outbox remain compatible. The new helper calls use the
existing local model, reasoning limits, tokenization and trace logging.

## Verification

The gates run through Nix on x86_64-linux. Current CI results are attached to
[PR #4](https://github.com/BadRabbit00/Mika-Project/pull/4).

- `nix flake check --no-update-lock-file --print-build-logs`: the complete test
  suite, offline CLI startup, Ruff, formatting and lock checks.
- `nix build .#default --no-link --no-update-lock-file`: production environment.
- `nix develop --command python scripts/check_repository.py`: repository privacy.
- Gitleaks working-tree and complete-history scans, plus actionlint.
- `nix develop --command graphify update .`: 1,955 nodes, 5,142 edges,
  158 communities. The existing optional SQL parser warning concerns two
  unchanged fixtures; no dependency was installed outside Nix.

The new regressions were observed failing before implementation. They cover
mixed technical/personal messages, optional retrieval failures, name questions,
short name answers, session restart, source and person isolation, exact budgets,
malformed routing requests, unsupported citations and rejected output.

## Live local-model check, 2026-09-17

Gemma 4 12B, with the existing bounded-reasoning settings, completed these cases
using temporary SQLite storage and simulated delivery receipts:

| Input | Observed result |
| --- | --- |
| A name question before introductions | Asked how to address the person. |
| An introduction with a name and information-security studies | Used the supplied name in a conversational reply. |
| An AI-agent project description followed by a mood question | Answered about mood in personal mode; no graph lookup. |
| A name question in a fresh session after closure | Recalled the name from people_facts. |

The real extraction output supplied name, study and project facts with source
turn IDs. Revalidating it with the final filter accepted all three. The name was
also persisted and recalled through the actual session-close/store-reopen path.
The regression suite covers short name answers to delivered questions separately.

The first delivery harness used a frozen clock and hit the correct outbox pacing
guard. Its already validated introductions were replayed with advancing fixture
time before continuing the model check. There were no Telegram API sends.
Some calls consumed the full 8,192-token reasoning allowance and took several
minutes; this change does not alter the deployment's reasoning budget.
