"""Gemma 4 wire protocol and token limits for the pinned llama.cpp server."""

import re
from dataclasses import dataclass

from src.core.context import ContextOverflow

# Protocol delimiters, not model instructions. Verified against llama.cpp v0.4.0.
START = "<|channel>thought"
END = "<channel|>"
# The server finishes a partial UTF-8 character before forcing the end marker.
UTF8_TAIL = 3
EOS_RESERVE = 1


def context_capacity(properties):
    if not isinstance(properties, dict):
        raise ValueError("Invalid server properties")
    template = properties.get("chat_template", "")
    if not isinstance(template, str) or not all(
        marker in template for marker in (START, END, "<|turn>", "enable_thinking")
    ):
        raise ValueError("Bounded reasoning requires the configured Gemma 4 template")
    settings = properties.get("default_generation_settings")
    capacity = settings.get("n_ctx") if isinstance(settings, dict) else None
    if type(capacity) is not int or capacity <= 0:
        raise ValueError("The server must report its actual per-slot context size")
    return capacity


def after_reasoning(grammar):
    if re.search(r"(?m)^mika-answer\s*::=", grammar):
        raise ValueError("Grammar uses the reserved mika-answer rule")
    if len(re.findall(r"(?m)^root\s*::=", grammar)) != 1:
        raise ValueError("A root grammar rule is required")
    # Rename references as well as the declaration, preserving literals/comments.
    grammar = re.sub(
        r'"(?:[^"\\]|\\.)*"|\[(?:[^\]\\]|\\.)*\]|#[^\n]*|(?<![\w-])root(?![\w-])',
        lambda match: "mika-answer" if match[0] == "root" else match[0],
        grammar,
    )
    # Lazy triggers are consumed by the grammar, including the end delimiter.
    return f'root ::= "{END}" mika-answer\n' + grammar


@dataclass(frozen=True)
class ReasoningLimits:
    context: int
    prompt: int
    thinking: int
    answer: int
    end_token: int

    @classmethod
    def create(cls, *, context, prompt, requested, answer, end_tokens):
        if type(requested) is not int or requested < 0:
            raise ValueError("Reasoning budget must be a nonnegative integer")
        if len(end_tokens) != 1:
            raise ValueError("Gemma reasoning requires a single end-marker token")
        available = context - prompt - answer - (1 + UTF8_TAIL + EOS_RESERVE)
        if available < 0:
            raise ContextOverflow(
                "Prompt and reserved answer exceed the server context"
            )
        return cls(context, prompt, min(requested, available), answer, end_tokens[0])

    @property
    def total(self):
        return self.thinking + self.answer + 1 + UTF8_TAIL + EOS_RESERVE

    def parameters(self, grammar):
        params = {
            "n_predict": self.total,
            "return_tokens": True,
            "generation_prompt": START,
            "reasoning_budget_tokens": self.thinking,
            "reasoning_budget_start_tag": START,
            "reasoning_budget_end_tags": [END],
            "reasoning_budget_message": "",
            "preserved_tokens": ["<|channel>", END],
        }
        if grammar is not None:
            params.update(
                grammar=after_reasoning(grammar),
                grammar_lazy=True,
                grammar_triggers=[{"type": 1, "value": END}],
            )
        return params

    def separate(self, response):
        if response.get("stop_type") != "eos":
            raise ValueError("Reasoning requires a complete end-of-sequence response")
        tokens = response.get("tokens")
        if not isinstance(tokens, list) or any(
            type(token) is not int or token < 0 for token in tokens
        ):
            raise ValueError("Reasoning requires the generated token IDs")
        if (
            type(response.get("tokens_predicted")) is not int
            or response["tokens_predicted"] != len(tokens)
            or type(response.get("tokens_evaluated")) is not int
            or response["tokens_evaluated"] != self.prompt
        ):
            raise ValueError("Inconsistent server token accounting")
        if len(tokens) > self.total:
            raise ValueError("Generation exceeds its total token budget")
        content = response["content"]
        if tokens.count(self.end_token) != 1 or content.count(END) != 1:
            raise ValueError("Missing or repeated reasoning boundary")
        thinking_tokens = tokens.index(self.end_token)
        if thinking_tokens > self.thinking + UTF8_TAIL:
            raise ValueError("Reasoning exceeds its token budget")
        thought, _, answer = content.partition(END)
        if START in content or "<|channel>" in answer or not answer.strip():
            raise ValueError("Incomplete final answer or repeated reasoning channel")
        return thought, answer, thinking_tokens, len(tokens)
