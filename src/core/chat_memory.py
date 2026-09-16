"""Bounded file-backed compression and extraction for closed conversations."""

import json
import re
from pathlib import Path

from src.core.context import ContextBuilder, ContextOverflow, Request

_COMMENTS = re.compile(r"<!--.*?-->", re.S)
_FIELD = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


class ChatMemory:
    def __init__(self, llm, prompt_dir, *, grammar_dir=Path("grammars"), budget=16000):
        self.llm, self.prompt_dir, self.budget = llm, Path(prompt_dir), budget
        self.grammar_dir = Path(grammar_dir)

    def _request(self, name, values):
        path = self.prompt_dir / f"{name}.md"
        if not path.exists():
            raise ValueError(f"TODO(CHAT-SUMMARY-PROMPT): missing {path.name}")
        raw = path.read_text(encoding="utf-8")
        temperature = re.search(r"\btemp\s+([0-9.]+)", raw)
        if temperature is None:
            raise ValueError("Missing memory sampling metadata")
        template = _COMMENTS.sub("", raw).strip()
        if set(_FIELD.findall(template)) != values.keys():
            raise ValueError("Unexpected memory template fields")
        first = _FIELD.search(template)
        boundary = template.rfind("\n", 0, first.start()) + 1
        user = _FIELD.sub(
            lambda match: json.dumps(values[match[1]], ensure_ascii=False),
            template[boundary:],
        )
        return Request(
            name, template[:boundary].strip(), user, self.budget, float(temperature[1])
        )

    async def _batches(self, name, turns, **values):
        batch = []
        for turn in turns:
            candidate = [*batch, turn]
            request = self._request(name, values | {"dialog": candidate})
            tokens = await self.llm.prompt_tokens(request)
            if len(tokens) > self.budget:
                if not batch:
                    raise ContextOverflow(
                        "A single archived turn exceeds the memory budget"
                    )
                yield batch
                batch = [turn]
                request = self._request(name, values | {"dialog": batch})
                ContextBuilder._enforce(request, await self.llm.prompt_tokens(request))
            else:
                batch = candidate
        if batch:
            yield batch

    async def summarize(self, *, turns, previous, trace_id):
        summary = previous
        async for batch in self._batches("dialog_summary", turns, previous=previous):
            request = self._request(
                "dialog_summary", {"dialog": batch, "previous": summary}
            )
            ContextBuilder._enforce(request, await self.llm.prompt_tokens(request))
            summary = (await self.llm.generate(request)).strip()
            if not summary:
                raise ValueError("Empty dialogue summary")
        return summary

    async def facts(self, *, turns, trace_id):
        grammar = (self.grammar_dir / "facts.gbnf").read_text(encoding="utf-8")
        facts = []
        user_turns = [turn for turn in turns if turn["role"] == "user"]
        async for batch in self._batches("facts_extract", user_turns):
            request = self._request("facts_extract", {"dialog": batch})
            ContextBuilder._enforce(request, await self.llm.prompt_tokens(request))
            result = json.loads(await self.llm.generate(request, grammar=grammar))
            if not isinstance(result, list):
                raise ValueError("Expected a personal fact array")
            facts.extend(result)
        return facts
