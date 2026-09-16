"""Stateless, file-backed contexts for the implemented learning profiles."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_COMMENTS = re.compile(r"<!--.*?-->", re.S)
_PROFILES = {
    "extract": (6000, frozenset({"existing_node_names", "article_chunk"})),
    "selfquiz_ask": (2000, frozenset({"topic_node_names", "asked_questions", "n"})),
    "selfquiz_answer": (3000, frozenset({"question", "retrieved_nodes"})),
}


@dataclass(frozen=True)
class Request:
    profile: str
    system: str
    user: str
    budget: int
    temperature: float


class ContextBuilder:
    def __init__(self, prompt_dir: Path):
        self.prompt_dir = Path(prompt_dir)

    def build(self, profile: str, **blocks: object) -> Request:
        if profile not in _PROFILES:
            raise ValueError(f"Unsupported context profile: {profile}")
        budget, allowed = _PROFILES[profile]
        if blocks.keys() != allowed:
            raise ValueError(
                f"Expected exactly these blocks for {profile}: {sorted(allowed)}"
            )
        values = {}
        for name, value in blocks.items():
            if name == "retrieved_nodes":
                if not isinstance(value, (list, tuple)) or not value:
                    raise ValueError("Answer context requires retrieved nodes")
                for node in value:
                    if not isinstance(node, dict) or node.keys() != {
                        "id",
                        "name",
                        "summary",
                        "edges",
                    }:
                        raise TypeError("Unexpected retrieved node fields")
                values[name] = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, (list, tuple)) and all(
                isinstance(item, str) for item in value
            ):
                values[name] = "\n".join(value)
            elif isinstance(value, str):
                values[name] = value
            else:
                raise TypeError(f"Invalid context block: {name}")
        raw = (self.prompt_dir / f"{profile}.md").read_text(encoding="utf-8")
        temperature = re.search(r"\btemp\s+([0-9.]+)", raw)
        if temperature is None:
            raise ValueError(f"Missing sampling metadata for {profile}")
        template = _COMMENTS.sub("", raw).strip()
        if profile == "selfquiz_ask":
            # TODO(QUIZ-PERSONA): section 12 excludes the later mood/persona state.
            template = template.replace("{persona}", "").strip()
        if set(_PLACEHOLDER.findall(template)) != allowed:
            raise ValueError(f"Unexpected placeholders in {profile}")
        first = _PLACEHOLDER.search(template)
        boundary = template.rfind("\n", 0, first.start()) + 1
        # Values are substituted once; braces inside source material stay data.
        user = _PLACEHOLDER.sub(lambda match: values[match[1]], template[boundary:])
        return Request(
            profile, template[:boundary].strip(), user, budget, float(temperature[1])
        )
