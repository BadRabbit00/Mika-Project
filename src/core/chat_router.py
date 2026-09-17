"""Bounded, read-only knowledge requests selected from conversation intent."""

from dataclasses import replace
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from src.core.context import ContextBuilder


class KnowledgeRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    knowledge_query: str | None = Field(min_length=1, max_length=512)


class ChatRouter:
    def __init__(self, llm, prompt_dir, *, grammar_dir=Path("grammars")):
        self.llm = llm
        self.instructions = (Path(prompt_dir) / "chat_route.md").read_text().strip()
        self.grammar = (Path(grammar_dir) / "chat_route.gbnf").read_text()

    async def query(self, dialogue):
        request = replace(
            dialogue,
            profile="chat_route",
            system=self.instructions,
            temperature=0.0,
        )
        ContextBuilder._enforce(request, await self.llm.prompt_tokens(request))
        raw = await self.llm.generate(request, grammar=self.grammar, max_tokens=512)
        query = KnowledgeRequest.model_validate_json(raw).knowledge_query
        if query is not None:
            query = query.strip()
            if not query:
                raise ValueError("An optional knowledge query cannot be blank")
        return query
