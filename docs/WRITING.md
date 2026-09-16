# Contexts, writing, and validation

ContextBuilder renders file-based instructions, serializes factual blocks, and
checks the complete llama-server chat template with `/tokenize`. Budget overflow
raises ContextOverflow; it never silently truncates context.

| Profile | Budget | Memory |
| --- | ---: | --- |
| extract | 6000 | Article chunk and existing names |
| selfquiz_ask | 2000 | Names and prior questions only |
| selfquiz_answer | 3000 | Question and retrieved nodes; configured top-k |
| write_tech | 8000 | Variant facts, technical gists, public topic threads |
| write_offtop | 3000 | Life event, life gists, slot history, public life threads |

`_base_core.md` supplies the nontechnical persona. Technical writing also receives
`_base_study.md`. Every write template includes output_envelope.md and a file-based
register example. The generation stop word is the matching closing mode tag.
When the server confirms consuming that exact stop word, the client restores it.
There is no unconditional envelope repair.

Writer supports found, impression, struggle, summary, correction, insight, daily,
offtop, and situation. Mode, sampling temperature, and length limits come from
file metadata. Daily inputs contain a neutral tired reason, not article complexity.
Corrections remain quoted user-role data. Raw PAD and physiological phase data do
not enter prompts. Off-topic isolation checks cover the complete request.

Each final draft stores a typed JSON context snapshot: PAD/bands, world and sleep
facts, template inputs, and memory identities. Regeneration loads the typed inputs
and rebuilds ContextBuilder with current world and mood observations. It does not
replay a stored prompt as current context. Publication provenance is separate from
nodes merely read during generation.

The off-topic planner uses supplied people-name forms and progress bindings.
A slot may be used twice per rolling fourteen days by default; its weight is
`configured_weight / (uses + 1)`. Overrides are read at selection time. Canonical
entities support deduplication; progress advances only when publication is recorded.
Weather uses Open-Meteo with the complete seasonal fallback table and relevance
filtering.

Validation has three layers:

1. Remove outer fences, quotes, and service wrappers.
2. Reject invalid mode envelopes, CJK, inappropriate kana, template markers,
   LaTeX, structural artifacts, refusals, malformed HTML, and truncation.
3. Reject documented semantic collisions, technical leakage into off-topic text,
   cycle references, prompt echo, and repetition against the last thirty posts.

Echo is Jaccard similarity of exact token five-gram sets, using `/tokenize` and the
configured threshold (default 0.8). Repetition uses embedding cosine, default
threshold 0.86. These deterministic rules are the approved first version; they do
not prove arbitrary factual correctness or cover every possible euphemism.

Three real Gemma writing calls passed after the envelope fix. The earlier rejected
run remains historical evidence, not an unresolved prompt contract. See
[VALIDATION.md](VALIDATION.md).
