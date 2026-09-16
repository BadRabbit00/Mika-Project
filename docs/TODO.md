# Architecture questions and deferred contracts

Find markers with `rg -n -i '\bTODO\b' src tests scripts grammars docs`.
This registry covers specification gaps, unfinished integration, runtime guards,
and maintained test contracts. An existing marker does not necessarily mean that
its entire subsystem is unimplemented; each entry describes the remaining work.
The [code marker index](#code-marker-index) maps every code ID to its locations,
including structured log fields written as `todo="<id>"`.

## Storage and delivery

- TODO(OUTBOX-DELIVERY), section 37.2, delivery step 8: a unique local key
  deduplicates enqueue operations. If Telegram accepts a message and the process
  dies before saving its message ID, SQLite cannot determine whether it was sent.
  Step 8 now commits a durable claim before sending and never automatically
  resends an uncertain claim. A verified message ID can reconcile the row.
  Automated reconciliation remains undefined; a crash before sending can also
  leave an uncertain claim. This favors duplicate
  prevention over automatic recovery and does not claim exactly-once delivery.
- TODO(LEARNING-STATE), sections 3 and 13: the pure state machine and
  SQLiteLearningStore are implemented. The architecture omits the durable schema;
  [learning-storage.sql](learning-storage.sql) is the explicit proposal exercised
  by tests and dry runs. The store requires these tables to be installed already.
  The remaining work is the production migration decision and wiring
  CommandService._state() to this store: /state still returns the marker instead
  of the learner snapshot. Do not substitute life_state for learning state.
  See also TODO(LEARNING-STORAGE).
- Embedding storage in schema version 3 uses the self-describing NumPy NPY format,
  float64 values, and an explicit model identity. Dimensions come from the server;
  cosine similarity normalizes vectors at comparison time. Pickle is disabled.
  TODO(EMBEDDING-UPGRADE): legacy unlabelled blobs and model changes require an
  explicit reindexing policy; incompatible vectors fail instead of being mixed.
- TODO(INVALIDATION-LINEAGE), section 27: Defects excludes invalidated posts from
  narrative. SQLiteLineageStore also marks explicitly linked threads stale and
  nodes suspect when the proposed post_threads/post_nodes tables are installed
  and the adapter is supplied. Without it, commands and logs report this marker.
  The production migration and population of those links remain to be defined;
  the curator review queue still has no storage contract. See
  TODO(INTERFACE-LINEAGE) and [interface-storage.sql](interface-storage.sql).
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
## Output and writing contracts

- TODO(OFFTOP-PERSONA): the shared prompts/_base.md names AI agents, while
  sections 12 and 16 prohibit technical terms anywhere in off-topic context.
  Prompt files remain read-only. The persona-selection policy needs an explicit
  decision; an isolation failure must not be silently bypassed.
  ContextBuilder defaults to rejecting that conflict. Its explicit
  offtop_persona="nontechnical_sections" option selects the existing character
  and voice sections and obtains identity from life.yaml. Tests exercise that
  opt-in; no production default or prompt-file modification is applied.
- TODO(WRITE-MODE-INSTRUCTIONS): section 5 requires a matching closed output
  mode, but supplied writing templates name only the opening mode in metadata.
  A live Gemma 12B smoke test on 2026-09-16 omitted mode tags in all three
  attempts (1259 input tokens each); attempts two and three also exceeded the
  template's length limit. The writer rejected every attempt and stored killed
  with NULL text. The templates need an explicit output-envelope instruction
  before successful live generation can be claimed. Prompt files remain intact;
  the writer does not manufacture tags or weaken validation to accept these runs.
- TODO(VALIDATOR-SEMANTICS): no finite list can recognize every euphemism or
  semantic contradiction. Deterministic rules cover explicit physiological
  terminology, known euphemisms, current-time claims, and the five-hour debt
  example. A broader semantic judge has no supplied prompt or grading contract.
- TODO(PROMPT-ECHO): section 34 specifies a threshold but no similarity metric.
  The validator requires an explicit scoring function. Lexical sequence
  similarity is available as an opt-in implementation, not an inferred default.
- TODO(OUTPUT-TRUNCATION): arbitrary mid-word termination cannot be inferred
  reliably from punctuation. Reject a server truncation signal and trailing
  commas; do not require sentence-final punctuation for casual messages.
- TODO(KAOMOJI): the architecture does not define kaomoji syntax. Bracketed
  expressions with recognizable face punctuation are exempted from the kana
  rule; parentheses around Japanese prose are not an exemption.
- TODO(OFFTOP-FREQUENCY): overused() has no threshold and inverse-frequency
  weights have no zero-count rule. The planner requires explicit max_slot_uses
  and slot_weight inputs. No production threshold or smoothing is inferred.
- TODO(OFFTOP-ENTITY): legacy entity strings do not preserve variable names or
  frame history. New events use canonical JSON containing slot and variable
  values. Frame history is recoverable from unique placeholder sets in the
  supplied frames. Ambiguous frame signatures or legacy history fail explicitly.
- TODO(OFFTOP-PEOPLE): people entries provide IDs and descriptions, but not
  grammatical name forms for frame references. Callers supply reference labels;
  frames with unresolved references are ineligible instead of inventing names.
- TODO(OFFTOP-BINDINGS): the coffee_state placeholder has no explicit binding
  in the supplied slot. Callers can supply bindings; unresolved frames are logged
  and ineligible. Other complete frames remain usable.
- TODO(DAILY-ISOLATION): write_daily.md requests article complexity inside an
  off-topic profile. Keep that variant unavailable under strict isolation until
  the supplied template is corrected. The slot and situation variants are separate.
- TODO(INSIGHT-PROMPT): insight is listed as a post kind but has no supplied
  write_insight.md. Do not substitute a different prompt silently.
## Curator and interface contracts

- TODO(CURATOR-MODEL-SOURCE): config/models.yaml remains absent. The explicitly
  supplied config/settings.yaml defaults provide the requested CLI model and
  effort without hardcoding either string. Curator timeout is a required CLI
  argument. Consolidate these sources when supplying models.yaml.
- TODO(CURATOR-SUBSCRIPTION-ERRORS): ActionRunner now persists six-hour curator
  deferrals, up to three reschedules after the initial attempt. A JSON/schema
  failure alone gets one immediate repair attempt in the transport. No
  subscription-limit error taxonomy is defined in the document.
- TODO(CURATOR-CORRECTION-APPLICATION): validated graph corrections are retained
  in the curator receipt, without changing graph summaries automatically. The
  exam schema stores one row per question and does not identify the whole exam;
  corrected_by and correction-post lineage need that identity decision first.
- TODO(CURATOR-GRADING-POLICY): curator_grade.md has a pass_rule placeholder,
  but no aggregate grading formula is supplied. Callers provide its text; code
  validates the verdict vocabulary and complete question-index coverage rather
  than inventing an overall pass threshold.
- TODO(INTERFACE-LINEAGE): explicit post_nodes/post_threads tables are proposed
  and tested through SQLiteLineageStore. Without an installed lineage contract,
  invalidation excludes narrative and preserves public evidence, but reports
  unavailable propagation. Do not guess derived nodes from a post's topic.
  A durable curator-review queue is still unspecified.
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
- TODO(JOB-RECOVERY): JobQueue is process-local. Publication intents persist in
  outbox, and learner events/actions persist through SQLiteLearningStore when
  its proposed schema is installed. Ordinary Telegram command, chat, and library
  jobs are not automatically journaled there. Recovery of those jobs still needs
  durable identities and replay rules; uncertain learner effects are tracked
  separately under TODO(ACTION-RESUME).
- TODO(OPS-LOG-RECOVERY): full JSONL is authoritative. Event cards are queued
  durably after the mirror drains them; an in-memory mirror event can be lost on
  a crash before that point. Automatic replay checkpoints are not specified.

## Dialogue and orchestration contracts

- TODO(CHAT-SUMMARY-PROMPT): section 33 requires dialogue-head compression, but
  no dedicated summary prompt is supplied. `gist.md` describes a single public
  post and is not silently repurposed. `ChatMemory` requires dialog_summary.md
  with dialog and previous placeholders; callers may inject a summarizer.
- TODO(CHAT-SEMANTIC-FILTERS): graph citation checks and explicit-ignorance
  markers cannot prove that every generated sentence avoids pretrained facts.
  Verbatim fact grounding and conservative sensitive-word checks cannot cover
  every euphemism. Rejected output is not stored as an answer or fact.
- TODO(CHAT-MOOD-METRIC): exports include the first/last reply cosine specified
  in section 33.5, citation and compression counters, and unknown-reply frequency.
  No mood-drift formula is specified. The undefined mood metric is explicitly
  unavailable; counts of ignorance markers do not prove semantic honesty.
- TODO(PRIVATE-THREADS): threads has no session/channel provenance. Unknown
  topic questions open public question threads. Unknown DM questions stay in
  session_turns until a scoped thread contract exists, preserving section 33's
  privacy boundary instead of feeding private questions to public posts.
- TODO(FACT-DELETION): personal-memory deletion requires the confirmation
  controls described in section 20.5; /facts is currently read-only.
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
and weather fallback/relevance. Successful generation tests use a mocked model;
the live output-envelope limitation is documented above.

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
