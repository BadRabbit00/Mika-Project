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
- TODO(SOURCE-DATE), sections 2 and 38.1, step 2: sources.published_at is a DATE,
  while the later rule requires UTC timestamps for database dates. This schema
  enforces the explicit UTC rule. A date-only publication value cannot be turned
  into an instant without an agreed timezone/time-of-day policy; reject it or
  leave published_at NULL until that policy is defined. Do not invent midnight.
- TODO(TRACE-IDENTITY), sections 19.2 and 19.3: runs.trace_id remains the legacy
  primary key although a trace spans multiple model calls. Calls already have
  distinct IDs in JSONL. Writer rows use unique attempt IDs in the legacy column
  and retain the root trace in params_json. The proposed call_id/trace_id split
  in [interface-storage.sql](interface-storage.sql) is not an automatic migration.
  Native SQL indexing of the shared trace awaits TODO(INTERFACE-TRACE-SCHEMA).
- TODO(CORRECTION-ID), sections 2 and 4.4, step 9: nodes.corrected_by is TEXT,
  while exams.id is INTEGER. Define the intended identity format before adding
  a foreign key; the documented column type is preserved.
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
- TODO(MODEL-CONFIG), section 18.1, step 2: config/models.yaml is referenced but
  not supplied. Existing settings and prompts also contain model settings.
  Establish the source of truth for model deployment settings. The implemented
  client accepts explicit endpoint URLs; sampling temperatures are read from the
  supplied prompt metadata. No replacement models.yaml is invented.

## Extraction and self-quiz

- TODO(CLAIMS-GRAMMAR): section 4.1 references undefined string, ws, and nl rules.
  The grammar supplies JSON string escaping, horizontal whitespace, and a line
  terminator while preserving the specified claim fields and relation vocabulary.
- TODO(CLAIMS-TERMINATION): the local Gemma repeatedly hits the output limit with
  the literal mandatory final newline. A diagnostic allowing an optional final
  newline terminates. JSON Lines permits omitting the last newline, but applying
  that convention requires changing the supplied production structure so record
  separators remain mandatory. Clarification has been requested; the committed
  grammar retains the literal structure until then. Truncated output is rejected
  in full and never committed. Automated three-article validation uses a mocked
  generation transport and must not be described as a successful live-model run.
  The unapplied proposal is [claims-final-newline.patch](claims-final-newline.patch).
  It keeps newlines mandatory between claims and only makes the final one optional.
- TODO(RETRIEVAL-POLICY): the architecture specifies hybrid FTS/cosine search but
  no fusion formula or cutoff for self-quiz. Retrieval parameters must be supplied
  explicitly. The provided policy uses reciprocal rank fusion, selected explicitly
  by constructing RetrievalPolicy or supplying the CLI's required --rrf-k and
  --min-similarity arguments. No production values are inferred from the document.
- TODO(QUIZ-PERSONA): section 12 limits the question context to node names and
  earlier questions, while section 16 and selfquiz_ask.md add persona. The supplied
  persona includes technical and mood content. The question context preserves
  the user's stricter names-only contract with an empty persona substitution.
  Mood is implemented, but adding it would still violate that isolation rule.
  No persona or mood text is invented; supplied files stay intact.
- TODO(TRUST-PRIOR): section 4.3 does not define the reliability lookup for source
  kind, peer review, and publisher. Extraction preserves explicit trust_prior and
  origin_key metadata, and deduplicates each normalized triple per source. It does
  not manufacture trust scores or collapse independent sources into one claim.
- TODO(SOURCE-REVISION): a reused source ID with changed text is rejected. Define
  graph retraction and re-extraction semantics before supporting source revisions.
- TODO(RUNS-PERSISTENCE): multiple calls in one trace are logged in full JSONL with
  distinct call IDs. The contradictory runs primary key is unchanged; multi-call
  learning traces are not squeezed into that table or silently overwritten.
  Writer rows retain unique attempt IDs in the legacy primary-key column;
  params_json links them by post_id and shared trace_id. JSONL carries the shared
  trace directly. Native SQL trace indexing awaits TODO(INTERFACE-TRACE-SCHEMA).
- TODO(QUIZ-CONFIDENCE): the answer prompt includes confident, but section 4.2
  supplies no grading rule for it. The field is type-validated and logged. Verdicts
  follow the documented citation checks; the model's confidence does not replace
  evidence. Define any additional abstention semantics explicitly.

## Mood and sleep contracts

- TODO(DECAY-ASSERTION): section 31.1 expects a distance below 0.1 after six
  hours, but the literal section 28.3 formula and A half-life of two hours give
  0.1625. Tests preserve that exact result and verify the below-0.1 condition at
  eight hours. No coefficient or formula is changed to satisfy the inconsistent
  example.
- TODO(BASELINE-CLAMP): the -0.93 example in section 28.3 conflicts with the
  configured baseline bounds [-0.6, 0.6]. The configured clamp is authoritative.
- TODO(BASELINE-HISTORY): no durations define recent exams/corrections or a good
  quiz streak. Callers supply these facts explicitly; mood does not infer them
  by reading the knowledge graph. Baseline targets are evaluated at access time.
- TODO(CYCLE-EPOCH): no production launch epoch is configured. Callers must pass
  a stable aware epoch on every restart. start_offset_days counts elapsed days,
  so offset 11 starts on cycle day 12. Confirm if an ordinal day was intended.
- TODO(CYCLE-WEEKDAY): 28 is divisible by seven, so section 35.2's claimed drift
  across weekdays is mathematically impossible. The specified 28-day cycle stays
  unchanged; no random phase drift is added.
- TODO(MOOD-BOUNDARIES): adjacent band endpoints overlap in the YAML. Bands use
  lower-inclusive, upper-exclusive intervals, with +1 in the final band. Octants
  follow the literal >= -0.15 formula, including its boundary.
- TODO(MOOD-TIMESTAMP): mood.at is the only primary key. Two mutations at the
  same instant cannot both be appended. Reject non-increasing mutation times;
  do not invent microsecond offsets or overwrite history.
- TODO(TRIGGER-POLICY): a week is not defined as calendar or rolling. Callers
  supply its start explicitly. Some resolution probabilities sum to less than
  one, and some triggers have no resolution despite the prose. An unspecified
  probability outcome must fail before mutation; no new event is invented.
- TODO(SLEEP-WAKE): sections 25.1 and 29.1 specify different wake algorithms.
  Keep the provisional sleep plan and the actual schedule-based wake event
  explicit, without replacing either formula silently.
- TODO(SLEEP-RECOVERY): the literal debt formula reduces debt only by surplus
  sleep. The extra 2.5-hour good-night recovery has no defined condition or
  composition rule; do not subtract it in addition to the formula.
- TODO(WAKE-TIMES): dasha_hairdryer, delivery_doorbell, and overslept have no
  structured wake times. Missing times require explicit caller input; text hints
  are not parsed into scheduling rules.
- TODO(CLASS-GRACE): grace_min does not specify which side of a lesson boundary
  it affects. Class interiors remain blocked, with the explicit timetable gaps
  available for posts; no grace interval is invented inside a lesson.
- TODO(COMMUTE-WINDOW): the fixed 08:20–09:00 blackout differs from the
  35-minute commute and Friday's later classes. No weekday restriction or return
  route is supplied. Use the explicit blackout window; do not infer extra routes.
- TODO(SLEEP-MOOD): no rule maps PAD to the sleep formula's stuck/down labels.
  Callers must supply the label as an explicit fact.
- TODO(SEMESTER-EPOCH): schedule.yaml references a semester start in life.yaml,
  but only a week number is supplied. Semester pressure requires a start date.
- TODO(WORLD-LOCATION): no deterministic movement policy is specified. Day
  context requires the caller's known location, validated against life.yaml.
  It does not randomly invent a location or infer one from a publication chance.
- TODO(SLEEP-HISTORY): no schema or life_state key contract specifies planned
  and actual sleep intervals, wake reasons, or once-per-night debt application.
  Sleep calculators accept explicit facts; mood snapshots persist the supplied
  debt. The implemented scheduler consumes a caller-supplied blackout provider;
  it does not persist sleep plans or once-per-night debt application. Durable
  sleep-history identities remain undefined. The simulation exports these facts.
- TODO(MOOD-DISABLED): the settings describe neutral output when mood is disabled,
  but do not define its numeric state or treatment of queued resolutions. Current
  supplied defaults enable mood. Disabling mutation fails explicitly until those
  semantics are specified; no replacement neutral state is invented.

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
- TODO(WEATHER-MONTHS): life.yaml has no July or August fallback. If the API
  fails in those months, omit weather instead of inventing seasonal conditions.

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
- TODO(SETTINGS-SCHEMA): section 36 requires persistent overrides but supplies no
  schema. The concrete proposal is docs/interface-storage.sql. SQLiteSettingsStore
  implements that contract and restart persistence is tested against explicitly
  installed tables. Default startup leaves it disabled; /set mutations report
  this gap. The user was asked whether to add these migrations; no answer has
  been received. The existing config files are not used as writable storage.
- TODO(INTERFACE-TRACE-SCHEMA): the proposed runs migration separates call_id
  from trace_id. Until approved, full JSONL carries shared trace IDs and call IDs.
  Writer rows keep their legacy unique attempt key and store the chain trace in
  params_json.trace_id; publication resolves that chain identity. No run is
  overwritten to accommodate another call.
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
- TODO(LEARNING-STORAGE): docs/learning-storage.sql specifies the proposed
  event receipts, action dependencies, durable pauses, and activity reservations.
  SQLiteLearningStore implements it, and isolated tests exercise restart
  behavior. No production migration is applied without the missing schema
  decision. run --dry-run installs it only in its own new temporary database.
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

## Code marker index

Reconciled against the working tree on 2026-09-16: **33 distinct IDs in 41
locations**. Scope: tracked source, tests, scripts, grammars, SQL proposals,
Nix files, and TOML files. Both `TODO(<id>)` annotations/runtime messages and
`todo="<id>"` log fields are included. IDs are unique below; repeated uses share
one description above. Other entries in this registry record architecture or
integration questions that do not yet have an inline marker.

| Marker ID | Locations |
| --- | --- |
| ACTION-HANDLER | [src/runner.py:279](../src/runner.py#L279) |
| CHAT-MOOD-METRIC | [src/chat.py:427](../src/chat.py#L427) |
| CHAT-SUMMARY-PROMPT | [src/core/chat_memory.py:21](../src/core/chat_memory.py#L21) |
| CLAIMS-GRAMMAR | [grammars/claims.gbnf:5](../grammars/claims.gbnf#L5) |
| CURATOR-GRADING-POLICY | [src/pipeline.py:221](../src/pipeline.py#L221) |
| DAILY-ISOLATION | [src/core/context.py:268](../src/core/context.py#L268) |
| DECAY-ASSERTION | [tests/test_mood_and_schedule.py:61](../tests/test_mood_and_schedule.py#L61) |
| FACT-DELETION | [src/chat_gateway.py:65](../src/chat_gateway.py#L65) |
| INSIGHT-PROMPT | [src/core/context.py:272](../src/core/context.py#L272) |
| INVALIDATION-LINEAGE | [docs/interface-storage.sql:3](interface-storage.sql#L3), [src/commands.py:413](../src/commands.py#L413), [src/defects.py:70](../src/defects.py#L70) |
| LEARNING-STATE | [src/commands.py:126](../src/commands.py#L126), [src/runner.py:74](../src/runner.py#L74) |
| LIVE-RUNNER | [src/cli.py:158](../src/cli.py#L158) |
| MODEL-CONFIG | [src/core/llm_vendor.py:70](../src/core/llm_vendor.py#L70) |
| MOOD-DISABLED | [src/core/mood.py:239](../src/core/mood.py#L239) |
| OFFTOP-BINDINGS | [src/offtop.py:247](../src/offtop.py#L247) |
| OFFTOP-ENTITY | [src/offtop.py:61](../src/offtop.py#L61), [src/offtop.py:100](../src/offtop.py#L100) |
| OFFTOP-PERSONA | [src/core/context.py:306](../src/core/context.py#L306) |
| OUTBOX-DELIVERY | [src/core/db.py:466](../src/core/db.py#L466) |
| POST-REGENERATION | [src/commands.py:362](../src/commands.py#L362) |
| PROMPT-ECHO | [src/validator.py:206](../src/validator.py#L206) |
| QUIZ-CONFIDENCE | [src/selfquiz.py:82](../src/selfquiz.py#L82) |
| QUIZ-PERSONA | [src/core/context.py:203](../src/core/context.py#L203) |
| RETRIEVAL-POLICY | [src/retrieve.py:20](../src/retrieve.py#L20) |
| RHYTHM-CONFIG | [src/scheduler.py:88](../src/scheduler.py#L88) |
| RUNTIME-CONTEXT | [src/chat_gateway.py:74](../src/chat_gateway.py#L74), [src/commands.py:62](../src/commands.py#L62) |
| SETTINGS-SCHEMA | [docs/interface-storage.sql:2](interface-storage.sql#L2), [src/core/settings.py:32](../src/core/settings.py#L32), [src/core/settings.py:137](../src/core/settings.py#L137) |
| SOURCE-DATE | [src/ingest.py:60](../src/ingest.py#L60) |
| STAGE-CONTRACTS | [tests/test_stage_contracts.py:8](../tests/test_stage_contracts.py#L8) |
| TOPIC-CATALOGUE | [src/pipeline.py:276](../src/pipeline.py#L276) |
| TRACE-IDENTITY | [docs/interface-storage.sql:2](interface-storage.sql#L2), [src/core/db.py:96](../src/core/db.py#L96) |
| TRIGGER-POLICY | [src/core/mood.py:459](../src/core/mood.py#L459) |
| WAKE-TIMES | [src/core/schedule.py:209](../src/core/schedule.py#L209) |
| WEATHER-MONTHS | [src/core/weather.py:104](../src/core/weather.py#L104) |
