# Contexts, writing, and output validation

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
