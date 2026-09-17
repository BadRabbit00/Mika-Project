# Bounded local reasoning

The application uses the Gemma 4 chat template supplied by the generation server.
The implementation is tied to the llama.cpp version in `flake.lock`; an unrelated
model template is rejected when reasoning is enabled. Start both servers through
the [Nix startup guide](../mika-startup/ЗАПУСК.md#3-start-the-model-services).

## Controls

These settings live in `config/settings.yaml`. Owner `/set` commands persist
overrides in SQLite and affect the next generation without restarting Mika.
The `run`, `bot`, `extract`, `quiz`, and writing smoke-test entry points use them.

| Setting | Default | Allowed values |
| --- | --- | --- |
| `llm.reasoning_enabled` | `true` | `true`, `false` |
| `llm.reasoning_budget_tokens` | `8192` | `0` through `16384` |
| `llm.reasoning_timeout_sec` | `600` | `30` through `1800` seconds |

A larger token budget permits longer thinking; it does not force the model to use
all of it. Zero forces the reasoning channel to end immediately. Disabling
reasoning uses the original non-thinking template and direct output grammar.
Bare `LocalLLM` instances without a settings provider retain that non-thinking
behavior for isolated fixtures; application entry points always supply a registry.

The reasoning deadline covers generation and its transport retries together.
`system.llm_timeout_sec` still controls other local requests and non-thinking
generation. Slower hardware may need a longer reasoning deadline within the
declared range. A timeout produces a failed call receipt, not an accepted draft.

The wall-clock deadline raises an HTTP transport timeout tagged with the local
`/completion` endpoint. Learning actions follow the local-service policy: pause
for fifteen minutes, retain the pending action, then retry through normal study
admission. The alert is `local_model_unavailable`; it does not change curator
authentication or scheduling. Task cancellation during shutdown stays cancellation.
Terminal failures already recorded by older versions are not automatically
requeued; their trace and delivery receipts need review before resuming them.

## Context and output limits

For each reasoning request the client reads the actual per-slot context capacity
from `/props`, renders the server's chat template, and counts the complete prompt
through `/tokenize`, including special tokens and the reasoning prefix.

The effective thinking budget is the smaller of the configured budget and:

```text
slot capacity - prompt tokens - reserved answer tokens - 5
```

The five-token reserve covers one reasoning-end token, up to three tokens needed
to finish a partial UTF-8 character at the cutoff, and one end-of-sequence token.
If the prompt and answer reservation do not fit, generation raises
`ContextOverflow`. Input profile limits are enforced independently; no prompt is
silently shortened. A reduced thinking budget is logged.

`max_tokens` on `LocalLLM.generate` remains the final-answer limit (normally
2,048). Native `n_predict` caps the entire generated sequence at the effective
thinking budget plus the answer reservation and protocol reserve. The server
forces the reasoning-end marker when the thinking budget runs out. If thinking
ends early, a final answer can consume the unused allowance during generation;
the client rejects it if its exact `/tokenize` count exceeds the answer limit.

Generated token IDs and server usage counts must agree. Repeated or missing
reasoning boundaries, truncated input/output, excessive token counts, and a
changed context capacity fail validation. Completed answers are returned to
callers only after these checks. Context shifting is disabled in the launch guide.

## Grammar and reasoning separation

Native `/completion` is intentional: a grammar active from the first token would
prevent free-form reasoning. The client enables the Gemma template's thinking
channel, prefills its protocol marker, and supplies the server's budget sampler
with explicit start/end markers and an empty budget message. No new instructions
are embedded in Python or injected when the budget expires.

For structured requests, a lazy grammar starts at the reasoning-end marker. Its
wrapper consumes that marker before activating the existing domain grammar.
Post closing tags are not sent as stop strings in reasoning mode because a
thought can mention one. The existing output validator still requires the proper
final post structure.

Only the final answer reaches extraction, citation checking, post validation, or
dialogue history. Thoughts remain in `runs.thought` and the `thought` log field;
`runs.output` holds the answer. `tokens_out` counts the full generated sequence,
including reasoning and protocol tokens. Completion logs also report separate
reasoning and answer counts. `/set log.show_thoughts false` removes thoughts from
machine-topic attachments, while local JSONL logs and receipts retain them.

Longer reasoning does not guarantee factual correctness. Grounding, graph-only
answers, citation checks, empty-retrieval handling, and context isolation remain
mandatory. Thinking text is never treated as retrieved knowledge.

## Verified upstream behavior

- [Server API and flags](https://github.com/ggml-org/llama.cpp/blob/v0.4.0/tools/server/README.md).
- [Budget sampler and UTF-8 completion](https://github.com/ggml-org/llama.cpp/blob/v0.4.0/common/reasoning-budget.cpp).
- [Prefill and grammar suppression during reasoning](https://github.com/ggml-org/llama.cpp/blob/v0.4.0/common/sampling.cpp).
- [Native request fields](https://github.com/ggml-org/llama.cpp/blob/v0.4.0/tools/server/server-schema.cpp).

`--reasoning` enables template support; `--reasoning-budget` supplies a server
default. Mika also sends an explicit per-request budget, because the native API
needs the channel delimiters and prefill state. This Gemma template does not
support qualitative `--reasoning-effort` levels; use token and time limits.
