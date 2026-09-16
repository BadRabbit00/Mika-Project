# Sessions and application orchestration

## Dialogue sessions

`ChatService` keeps one active owner session per channel (`topic` or `dm`).
Opening, appending, and finalizing sessions use the existing SQLite tables.
Message trace IDs make replay return the stored reply. The complete turn log
remains available after context compression and closure.

`ChatContext` reads the supplied session and mode instructions. Only topical
requests receive graph nodes; only personal requests receive `life.yaml` data.
History is shared between modes inside one session, as required by section 33.4.
It never crosses channels. Personal facts are explicitly shared by owner.
Private dialogue events stay in local JSONL and are excluded from the group log
mirror. Chat narrative rows do not enter the existing public-writing profiles.

The default context budget is 16,000 server tokens. Overflow compresses only the
head, retains the last 12 turns in full, and checks the complete rebuilt request
again. An oversized protected tail raises `ContextOverflow`. The JSON value in
`sessions.summary` stores the text, compressed-through turn index, closure receipt,
and first-compression token count. This is a checkpoint encoding in an existing
column; archived turns are never replaced or deleted.

After six hours without turns, the session closes before memory extraction.
Failed finalization remains recoverable from SQLite after restart. The summary,
filtered facts, and finalization receipt commit together. Facts must quote a
user turn from that session, use an allowed kind, contain a direct first-person
statement, and pass the conservative sensitive-content filter. Model statements
and inferred facts are rejected. These lexical checks cannot prove semantic
truth or exhaust every euphemism; see the TODO list.

`ChatGateway` handles `/chat on`, `off`, `status`, `reset`, and `export` through
the existing background queue. Exports contain a JSONL header and complete turn
records. `/facts` reads personal memory. A supplied `chat_factory` attaches the
gateway to `run_telegram`; expiry runs through the same queue once per minute.
Model work never executes in aiogram handlers.

`ChatMemory` reads `facts_extract.md` and validates grammar-constrained candidates
in code. The required `dialog_summary.md` is absent from the supplied prompts;
until that contract is supplied, compression/finalization needs an injected
summarizer. No existing prompt or config file has been modified.

The current world, sleep history, cycle epoch, and mood baseline inputs must
come from an explicit context provider. Missing inputs stop generation; the
gateway does not manufacture a location, sleep schedule, or cycle epoch.
