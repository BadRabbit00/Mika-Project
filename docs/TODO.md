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
- TODO(EMBEDDING-FORMAT), section 2, steps 2 and 3: node_embeddings is specified
  only as node-associated BLOB vectors. Storage exposes node_id and embedding;
  vector dimension, dtype, byte order, normalization, and model-version handling
  remain unspecified. No vector encoder or search is implemented in step 1.
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
  Establish the source of truth before implementing model calls. Root mood.yaml
  and settings.yaml duplicate the supplied config files and remain unchanged.

## Invariants for later delivery stages

The step 1 suite tests time, UTC persistence, storage validation, the closed
relation vocabulary, FTS synchronization, transaction atomicity, retry limits,
outbox keys, PAD storage precision, and absence of copied prompt strings.
No later-stage module is implemented or presented as tested. The executable
stage gate in tests/test_stage_contracts.py requires the following behavioral
tests before the corresponding module may be introduced.

- TODO(STEP-2-CONTRACTS): model output is validated before writes; model tools
  cannot write; only the seven relation names are allowed; 200-token overlap;
  norm_hash deduplication; token counts come from /tokenize.
- TODO(STEP-3-CONTRACTS): selfquiz_ask receives names only; empty retrieval gives
  no_knowledge with zero model calls; invalid citations fail validation.
- TODO(STEP-4-CONTRACTS): copy the inertia, piercing, decay, and clamp tests from
  section 31.1; implement sections 28 and 35 literally and run the two-week
  simulation. PAD persistence rounding is already tested in step 1.
- TODO(STEP-6-CONTRACTS): reject CJK output, strip fences, and test all three
  validation levels.
- TODO(STEP-7-CONTRACTS): isolate off-topic and quiz contexts; curator material
  uses the user role; context overflow raises instead of truncating; people_facts
  cannot enter posts; /tokenize determines the budget.
- TODO(STEP-12-CONTRACTS): state transitions are pure, without storage or model
  side effects. Model calls run through a task queue, outside Telegram handlers.
