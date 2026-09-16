# Architecture questions and deferred contracts

Find markers with `rg -n -i '\bTODO\b' src tests scripts grammars docs`.
This registry covers specification gaps, unfinished integration, runtime guards,
and maintained test contracts. An existing marker does not necessarily mean that
its entire subsystem is unimplemented; each entry describes the remaining work.
The [code marker index](#code-marker-index) maps every code ID to its locations,
including structured log fields written as `todo="<id>"`.

## Storage and delivery

- TODO(UNSPECIFIED-STORES), sections 5, 20.5, 22.4, 31.4 and 36: the initial
  document did not define every persistence contract. Off-topic repeat history
  now uses canonical entities in life_journal; explicit proposals cover settings
  overrides, post lineage, and learner actions. Diary comments and curator review
  still lack contracts, and no general cross-subsystem event schema is supplied.
  The proposed schemas remain separate from automatic production migrations.
## Extraction and self-quiz

## Mood and sleep contracts

- TODO(BASELINE-HISTORY): the configured history windows and BaselineContext.from_history are implemented.
  Live providers must still assemble exam, correction, waiting, and quiz facts.
## Curator and interface contracts

- TODO(CURATOR-SUBSCRIPTION-ERRORS): ActionRunner now persists six-hour curator
  deferrals, up to three reschedules after the initial attempt. A JSON/schema
  failure alone gets one immediate repair attempt in the transport. CLI failures now carry the approved limit/auth/transport/unknown categories;
  the live runner still needs the corresponding pause and alert bindings.
- TODO(POST-REGENERATION): the runtime accepts an async regeneration provider,
  but persisted writing attempts do not contain the typed, current world/mood
  inputs needed to rebuild ContextBuilder safely. The standalone bot reports
  this missing provider. It does not replay stale prompt text as a fresh context.
- TODO(TELEGRAM-DEPLOYMENT): no bot tokens or chat/topic/owner IDs are supplied.
  The runtime requires an explicit layout file and three environment variables.
  Tests mock Telegram; live publication is not claimed. The user explicitly
  requested commands in Machine and DM, so owner commands work there in addition
  to Control, while TOPIC_ROLES still prevents reading Machine as model context.
- TODO(SETTINGS-CONSUMERS): the registry and installed override adapter expose
  current values; the log mirror reads them on each batch. Existing mood/study/
  writing constructors and runtime factories still need a defined refresh path
  for changed settings. A successful override write does not update an already
  created model instance automatically.
## Dialogue and orchestration contracts

- TODO(RUNTIME-CONTEXT): live composition requires explicit current sleep,
  location, cycle epoch, and mood baseline inputs. Do not reuse stale prompt
  text as a current world snapshot.
- TODO(RHYTHM-CONFIG): config/rhythm.yaml is absent. Section 6 supplies an
  example, but no configured production rhythm. The missing file and durable
  learner/scheduler schema have been raised for a user decision.
- TODO(LIVE-RUNNER): the offline composition is executable. Unattended live
  startup still needs the missing rhythm and summary files, a durable storage
  decision, and current world/mood providers. run without --dry-run refuses
  startup instead of pretending these inputs exist. Both services can be
  attached to the Telegram runtime through its explicit factory interfaces.
- TODO(POST-EXAM-INPUTS): LearningPipeline binds grading, literal allocation,
  remediation, and topic switching. Grading and selection require an explicit
  pass rule and a supplied topic/library catalogue. See
  TODO(CURATOR-GRADING-POLICY) and TODO(TOPIC-CATALOGUE).
- TODO(TOPIC-CATALOGUE): LearningPipeline.select_articles() requires an explicit
  topics_map and a supplied Source catalogue. library/topics.yaml is absent.
  The existing curator validates selections against the supplied library index
  and literal article allocation; it does not invent the topic map or ingest a
  production catalogue automatically. Supply these inputs and their loading
  contract before enabling selection in live composition.
- TODO(ACTION-HANDLER): ActionRunner fails and records an action when the
  injected handler mapping has no entry for its kind. LearningPipeline already
  supplies handlers for every action emitted by the current state machine.
  This marker guards incomplete custom composition or persisted actions whose
  handler was removed; keep handler registration and any future action migration
  policy consistent. It does not identify an unimplemented current transition.
- TODO(ACTION-RESUME): interrupted running effects are retained as uncertain.
  Pending timers and receipts recover automatically, but replay of an unknown
  model outcome needs a verified receipt or an explicit operator decision.

## Maintained validation contracts

- TODO(STAGE-CONTRACTS): the marker in tests/test_stage_contracts.py labels the
  requirement that implemented stages have their named behavioral tests.
  All currently listed contracts have those tests; steps 11 and 12 also have
  dedicated session, scheduler, and integration suites. Keep the stage gate and
  behavioral coverage current as modules evolve. This is an enforced regression
  requirement, not an unimplemented delivery stage.

The step 1 suite tests time, UTC persistence, storage validation, the closed
relation vocabulary, FTS synchronization, transaction atomicity, retry limits,
outbox keys, PAD storage precision, and absence of copied prompt strings.
Step 2 additionally tests grounded extraction, overlap, per-source deduplication,
atomic article writes, embedding compatibility, tokenizer budgets, and server
termination signals.

Step 3 tests names-only context, empty retrieval without model calls, strict
citation subsets, fresh answer contexts, topic scope, top-six retrieval, suspect
exclusion, replay behavior, and the minimum question count and passing fraction.

Steps 6 and 7 test all three output-validation layers, isolated writing memory,
quoted curator material in the user role, exact server token budgets, rejection
retries, killed drafts, read-only event selection, publication-time continuity,
and weather fallback/relevance. Mocked regression tests and three successful live Gemma drafts verify the output envelope; see VALIDATION.md.

The executable stage gate in tests/test_stage_contracts.py requires behavioral
tests before each later-stage module may be introduced.

Steps 11 and 12 test channel isolation, exact context compression, recoverable
expiry, direct-fact validation, pure transitions, action receipts, retry timing,
activity gates, and the complete offline article-to-exam scenario. All ten tests
listed in section 38.2 and the section 18.2 boundaries run in the full suite.

## Registry maintenance

Approved decisions are being implemented in architecture stage order. This file
retains open integration work; completed decisions and evidence are recorded in
[DECISIONS.md](DECISIONS.md) and [VALIDATION.md](VALIDATION.md).
