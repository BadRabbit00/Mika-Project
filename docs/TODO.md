# Architecture questions and deferred contracts

Find open items with `rg -n 'TODO\(' docs src tests CLAUDE.md`.
These are specification gaps, not defaults chosen by the implementation.

## Storage and delivery

- TODO(OUTBOX-DELIVERY), section 37.2, delivery step 8: a unique local key
  deduplicates enqueue operations. If Telegram accepts a message and the process
  dies before saving its message ID, SQLite cannot determine whether it was sent.
  Define reconciliation or an explicit policy for uncertain deliveries before
  implementing the sender. This step does not claim exactly-once remote delivery.
- TODO(LEARNING-STATE), sections 3 and 13, step 12: the learning-state table is
  required in prose but has no name, columns, or transition identity specified.
  Do not substitute life_state for learning state. Define its schema and event
  identity before implementing the state machine.
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
- TODO(TRACE-IDENTITY), sections 19.2 and 19.3, step 2: runs.trace_id is a primary
  key, but one trace is supposed to span multiple model calls. The schema retains
  the documented key. Decide on a separate run identity before logging multiple
  model calls in a trace.
- TODO(CORRECTION-ID), sections 2 and 4.4, step 9: nodes.corrected_by is TEXT,
  while exams.id is INTEGER. Define the intended identity format before adding
  a foreign key; the documented column type is preserved.
- TODO(INVALIDATION-LINEAGE), section 27, steps 7 and 9: excluded and suspect
  flags are included. The schema has no origin-post linkage for threads or nodes,
  and no schema for the curator review queue. Define those relationships before
  implementing invalidation propagation.
- TODO(UNSPECIFIED-STORES), sections 5, 20.5, 22.4, 31.4 and 36: persistence for
  off-topic repeat prevention, diary comments, correction review, cross-subsystem
  events, and settings overrides is described without table schemas. Define
  these at their respective delivery stages; do not invent additional tables.
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
  persona also needs mood state from step 4. Until that dependency is implemented,
  the question context uses the narrow section 12 contract and an empty persona
  substitution. No persona or mood text is invented; supplied files stay intact.
- TODO(TRUST-PRIOR): section 4.3 does not define the reliability lookup for source
  kind, peer review, and publisher. Extraction preserves explicit trust_prior and
  origin_key metadata, and deduplicates each normalized triple per source. It does
  not manufacture trust scores or collapse independent sources into one claim.
- TODO(SOURCE-REVISION): a reused source ID with changed text is rejected. Define
  graph retraction and re-extraction semantics before supporting source revisions.
- TODO(RUNS-PERSISTENCE): multiple calls in one trace are logged in full JSONL with
  distinct call IDs. The contradictory runs primary key is unchanged; multi-call
  traces are not squeezed into that table or silently overwritten.
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
  debt. Define durable sleep-history identities before the step 12 scheduler
  takes ownership of these facts across restarts. The simulation exports them.
- TODO(MOOD-DISABLED): the settings describe neutral output when mood is disabled,
  but do not define its numeric state or treatment of queued resolutions. Current
  supplied defaults enable mood. Disabling mutation fails explicitly until those
  semantics are specified; no replacement neutral state is invented.

## Invariants for later delivery stages

The step 1 suite tests time, UTC persistence, storage validation, the closed
relation vocabulary, FTS synchronization, transaction atomicity, retry limits,
outbox keys, PAD storage precision, and absence of copied prompt strings.
Step 2 additionally tests grounded extraction, overlap, per-source deduplication,
atomic article writes, embedding compatibility, tokenizer budgets, and server
termination signals.

Step 3 tests names-only context, empty retrieval without model calls, strict
citation subsets, fresh answer contexts, topic scope, top-six retrieval, suspect
exclusion, replay behavior, and the minimum question count and passing fraction.

The executable stage gate in tests/test_stage_contracts.py requires the following
behavioral tests before each later-stage module may be introduced.

- TODO(STEP-6-CONTRACTS): reject CJK output, strip fences, and test all three
  validation levels.
- TODO(STEP-7-CONTRACTS): isolate off-topic and quiz contexts; curator material
  uses the user role; context overflow raises instead of truncating; people_facts
  cannot enter posts; /tokenize determines the budget.
- TODO(STEP-12-CONTRACTS): state transitions are pure, without storage or model
  side effects. Model calls run through a task queue, outside Telegram handlers.
