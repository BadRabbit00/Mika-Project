# Contexts, writing, and output validation

## Context boundary

`ContextBuilder` renders only supplied files from `prompts/`. Source files contain
data serialization and validation, not model instructions. `build()` remains a
synchronous rendering API for existing callers; production paths use
`await build_checked(profile, llm=llm, **blocks)`. This counts the complete rendered
chat template through `/apply-template` and `/tokenize`. `_enforce()` raises
`ContextOverflow` instead of truncating. `LocalLLM.generate()` repeats the check
before completion, so a render-only request cannot bypass the transport boundary.

| Profile | Budget | Allowed memory |
| --- | ---: | --- |
| extract | 6000 | Existing names and article chunk |
| selfquiz_ask | 2000 | Node names and previous questions |
| selfquiz_answer | 3000 | Question and one to six retrieved nodes |
| write_tech | 8000 | Variant-specific facts, last 12 technical gists, topic threads |
| write_offtop | 3000 | Life event, last four slot labels, last five life gists, life threads |

Memory blocks use one read-only SQLite snapshot. Excluded or invalidated narrative
entries and threads older than five days are omitted. A summary includes at most
12 nonsuspect nodes and their induced topic edges. An unread-article announcement
contains metadata, not article text or graph summaries. Neither writing profile
reads `people_facts`. Off-topic contexts never contain graph data, article blocks,
or topic state; graph names are kept separately for boundary checks.

Each template variant has an explicit input whitelist. Replacement happens once,
so braces in curator text stay data. Correction material is JSON-quoted in the
user role. Mood uses file-backed verbal descriptions, never PAD coordinates or
physiological phase data. Current time, sleep, location, and available objects
come from the caller's validated `DayContext`.

The supplied `_base.md` mentions AI agents. Default off-topic rendering therefore
raises `ContextIsolationError`. An explicit
`offtop_persona="nontechnical_sections"` selects the existing character and voice
sections and takes identity from `life.yaml`; this is an opt-in policy, not a
changed production default. The user decision is tracked in TODO(OFFTOP-PERSONA).

## Draft generation

`Writer` supports `found`, `impression`, `struggle`, `summary`, `correction`,
`offtop`, and `situation`. Mode, character limits, and sampling temperature come
from each supplied template's metadata. Missing `insight` instructions and the
conflicting `daily` template remain explicit TODOs.

Library callers configure logging, initialize the database, and supply the
existing world/mood state. For example, inside an async caller:

```python
from pathlib import Path

from src.core.context import ContextBuilder
from src.core.llm_local import LocalLLM
from src.validator import OutputValidator, lexical_echo_similarity
from src.writer import Writer

context = ContextBuilder(Path("prompts"), database=db, mood_model=mood_model)
async with LocalLLM() as llm:
    validator = OutputValidator(llm, echo_similarity=lexical_echo_similarity)
    writer = Writer(db, llm, context, validator)
    result = await writer.generate(
        "summary", day=day, mood=mood_snapshot.mood,
        wake_reason=wake_reason, topic=topic,
    )
```

The explicit echo metric above is an example choice. All technical variants can
receive `topic` to scope their open threads. Supply plain JSON-compatible facts
for `fresh_nodes`, `confusion`, or other variant inputs.

A blackout returns `blocked` without calling the model. Otherwise each attempt
uses a fresh request. The validator checks closed mode tags, length, artifacts,
semantic rules, and similarity against the latest 30 valid published posts.
Duplicate feedback contains the similar post as user data. Each attempt records
its inputs, raw output, exact token counts, duration, status, and reason codes in
`runs`. Attempts have separate trace IDs linked by `params_json.post_id` because
the supplied schema makes `trace_id` the primary key.

Accepted output becomes a `draft`; three rejected attempts become `killed` with
NULL publishable text. Draft creation never sends a message, enqueues delivery,
updates narrative/threads, or advances life progress. Those transitions belong
to the publication stage.

## Life events and weather

`OfftopPlanner.from_config(db, Path("config"), max_slot_uses=..., slot_weight=...)`
requires explicit values for the architecture's unspecified overuse threshold
and inverse-frequency rule at zero uses. `pick()` also receives the current week
boundary, exam status, random generator, grammatical person labels, and relevant
weather. It applies configured weekly limits, weekdays, slot cooldowns, frame
LRU, 30-day variable reuse prevention, and entity deduplication.

Selection is read-only. `OfftopEvent.entity` is canonical JSON of the slot and
variable values; this preserves enough information to reconstruct frame history
without adding undocumented tables. Frame history survives the variable cooldown.
Legacy recent entities and ambiguous frame signatures fail explicitly. Missing
person labels or unbound placeholders make the affected frame ineligible.

`OfftopGenerator` connects weather, event selection, ContextBuilder through Writer,
and validation. It returns both the draft result and proposed event. After actual
publication, the later sender must call
`planner.remember(event, published_at=..., text=...)`. That transaction inserts
the journal record and advances progress once; repeated identical calls return
the original journal ID. No progress advances for rejected or unposted drafts.

`WeatherClient.from_config(Path("config"))` is an async context manager using
[Open-Meteo's geocoding API](https://open-meteo.com/en/docs/geocoding-api) and
[forecast API](https://open-meteo.com/en/docs). It resolves the configured Almaty
location, requests explicit units and Unix timestamps, and converts instants to
aware Almaty time. Responses are cached for 1800 seconds. Network or malformed
response failures use the supplied seasonal table; missing July/August entries
produce no weather. The `off` setting makes no network request.

Relevance follows section 26.2: the three-day mention cooldown comes first, then
extreme conditions or a 15-degree daily change, then probability 0.4 outdoors or
the configured indoor probability. Weather enters the context as factual fields,
without API metadata or instructions.

## Output validator

`src/validator.py` implements the three layers from section 34. It has no storage
or publication side effects. `ValidationResult` contains cleaned text, stable
rejection codes, cleanup operations, and the matching prior-post ID for duplicates.

1. Cleanup removes outer fences, labels, quotes, service wrappers, and parsed
   output tags. Each cleanup is logged. Writer callers specify the required
   output mode; missing, mismatched, or unclosed modes are rejected.
2. Artifact checks cover Unicode CJK, kana outside recognized kaomoji, template
   markers, LaTeX, JSON structures, placeholders, refusals, unsupported or broken
   HTML, short text, truncation signals, and prompt echo above 0.8.
3. Semantic checks cover the known physiological vocabulary and euphemisms,
   off-topic domain/graph terms, current-time contradictions, the documented
   sleep-debt contradiction, and cosine similarity above 0.86 to the last 30
   supplied posts. Artifact failures do not call the embedding server.

The standard HTML allowlist follows the
[Telegram Bot API HTML documentation](https://core.telegram.org/bots/api#html-style).
Attributes and nesting are checked; unsupported tags are rejected, not stripped.
HTML entities and formatting cannot hide CJK or prohibited phrases from the scan.

The architecture does not specify the prompt-echo similarity metric.
`OutputValidator(llm, echo_similarity=...)` requires an explicit function;
`lexical_echo_similarity` offers normalized sequence similarity as an opt-in.
No heuristic tokenizer is involved.

Deterministic rules cannot prove the absence of every semantic contradiction or
invented euphemism. These limitations and the missing semantic-judge contract are
recorded in [TODO.md](TODO.md). The validator does not claim general fact checking.

## Verification and current live limitation

The automated suite exercises production context rendering with mocked HTTP
tokenization, SQL memory isolation, retries and killed drafts, cosine boundaries,
configured event history, weather caching/fallback, and existing steps 1–5.

A live Gemma 12B run on 2026-09-16 used the supplied found template, an empty
temporary database, and the host's local model. All three calls used 1259 input
tokens counted by `/tokenize`. Output counts were 126, 140, and 148 tokens.
All three outputs omitted the required mode tags; the latter two also exceeded
the template's character limit. The writer correctly persisted a killed draft.
No post was published and no embedding service was needed for empty history.

This establishes live transport and rejection behavior, not successful live
writing. TODO(WRITE-MODE-INSTRUCTIONS) records the missing envelope instruction.
Supplied prompts remain unchanged. Together with TODO(OFFTOP-PERSONA), this is a
prompt-contract blocker to resolve before enabling unattended writing.
