"""Stateless, file-backed learning and writing contexts with strict isolation."""

import asyncio
import hashlib
import html
import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import structlog
from ruamel.yaml import YAML

from src.core.content_rules import CYCLE, normalized_text, technical_match
from src.core.content_rules import FORBIDDEN_TECH as FORBIDDEN_TECH
from src.core.context_blocks import BLOCKS as BLOCKS
from src.core.context_blocks import register_value_blocks, writing_memory
from src.core.pad import Mood
from src.core.settings import SettingsRegistry
from src.core.world import DayContext

log = structlog.get_logger("blogai.context")
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_COMMENTS = re.compile(r"<!--.*?-->", re.S)
_PROFILES = {
    "extract": (6000, frozenset({"existing_node_names", "article_chunk"})),
    "selfquiz_ask": (2000, frozenset({"topic_node_names", "asked_questions", "n"})),
    "selfquiz_answer": (3000, frozenset({"question", "retrieved_nodes"})),
}


@dataclass(frozen=True)
class Profile:
    name: str
    budget: int
    system: tuple[str, ...]
    user: tuple[str, ...]


PROFILES = {
    "extract": Profile(
        "extract", 6000, ("extract_rules",), ("existing_node_names", "article_chunk")
    ),
    "selfquiz_ask": Profile(
        "selfquiz_ask",
        2000,
        ("quiz_rules",),
        ("topic_node_names", "asked_questions", "n"),
    ),
    "selfquiz_answer": Profile(
        "selfquiz_answer", 3000, ("answer_rules",), ("question", "retrieved_nodes")
    ),
    "write_tech": Profile(
        "write_tech",
        8000,
        ("persona", "tech_rules"),
        ("study_state", "thesis", "subgraph", "narrative_recent", "open_threads"),
    ),
    "write_offtop": Profile(
        "write_offtop",
        3000,
        ("persona", "identity", "offtop_rules"),
        ("offtop_event", "recent_slots", "narrative_offtop", "open_threads"),
    ),
}
WRITE_INPUTS = {
    "found": {"article_title", "article_source", "article_kind", "given_by", "topic"},
    "impression": {"article_title", "fresh_nodes"},
    "struggle": {"confusion", "articles_read"},
    "summary": {"topic"},
    "correction": {"issue", "correct", "wrong_post_gist"},
    "offtop": {"offtop_event"},
    "situation": {"weather"},
    "daily": {"weather", "sleep_state", "tired_reason", "recent_greetings"},
    "insight": {"node_a", "relation", "node_b", "edge_summary", "source_title"},
}
OFFTOP_KINDS = frozenset({"offtop", "situation", "daily"})
_DAY_FIELDS = {
    "when",
    "daypart",
    "location",
    "bedtime",
    "wake_time",
    "sleep_debt",
    "wake_reason",
    "available_objects",
}


register_value_blocks(
    _DAY_FIELDS
    | set().union(*WRITE_INPUTS.values())
    | {"mood", "recent_situations", "emoji_max", "output_envelope", "day_context"}
    | {
        name
        for profile in PROFILES.values()
        for name in (*profile.system, *profile.user)
    }
)


class ContextOverflow(ValueError):
    """The actual server-rendered request exceeds its profile budget."""


class ContextIsolationError(AssertionError):
    """A forbidden block or term crossed a context boundary."""


@dataclass(frozen=True)
class Request:
    profile: str
    system: str
    user: str
    budget: int
    temperature: float
    mode: str | None = None
    min_chars: int = 40
    max_chars: int | None = None
    tokens_in: int | None = None
    forbidden_terms: tuple[str, ...] = ()
    node_ids: tuple[str, ...] = ()
    thread_ids: tuple[int, ...] = ()


class ContextBuilder:
    def __init__(
        self,
        prompt_dir: Path,
        *,
        database=None,
        mood_model=None,
        config_dir: Path = Path("config"),
        offtop_persona: str | None = None,
        settings=None,
    ):
        self.prompt_dir = Path(prompt_dir)
        self.database, self.mood_model = database, mood_model
        self.config_dir, self.offtop_persona = Path(config_dir), offtop_persona
        self.settings = settings or SettingsRegistry.from_file(
            self.config_dir / "settings.yaml"
        )
        if offtop_persona not in (None, "nontechnical_sections"):
            raise ValueError("Unknown explicit off-topic persona selection")
        for kind, inputs in WRITE_INPUTS.items():
            path = self.prompt_dir / f"write_{kind}.md"
            if path.exists():
                fields = set(
                    _PLACEHOLDER.findall(
                        _COMMENTS.sub("", path.read_text(encoding="utf-8"))
                    )
                )
                known = (
                    inputs
                    | _DAY_FIELDS
                    | {
                        "persona",
                        "identity",
                        "mood",
                        "narrative_recent",
                        "open_threads",
                        "subgraph",
                        "recent_slots",
                        "recent_situations",
                        "output_envelope",
                        "day_context",
                    }
                )
                if fields - known:
                    raise ValueError(
                        f"Unregistered placeholders in {path.name}: {fields - known}"
                    )

    def build(self, profile: str, **blocks: object) -> Request:
        if profile in ("write_tech", "write_offtop"):
            return self._writing(profile, **blocks)
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
                if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 12:
                    raise ValueError(
                        "Answer context requires one to twelve retrieved nodes"
                    )
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
        if set(_PLACEHOLDER.findall(template)) != allowed:
            raise ValueError(f"Unexpected placeholders in {profile}")
        first = _PLACEHOLDER.search(template)
        boundary = template.rfind("\n", 0, first.start()) + 1
        # Values are substituted once; braces inside source material stay data.
        user = _PLACEHOLDER.sub(
            lambda match: BLOCKS[match[1]](values), template[boundary:]
        )
        return Request(
            profile, template[:boundary].strip(), user, budget, float(temperature[1])
        )

    async def build_checked(self, profile: str, *, llm, **blocks: object) -> Request:
        request = await asyncio.to_thread(self.build, profile, **blocks)
        tokens = await llm.prompt_tokens(request)
        self._enforce(request, tokens)
        request = replace(request, tokens_in=len(tokens))
        fingerprint = hashlib.sha256(
            json.dumps(
                [request.profile, request.system, request.user], ensure_ascii=False
            ).encode()
        ).hexdigest()
        log.info(
            "context_built",
            profile=profile,
            request_hash=fingerprint,
            tokens_in=len(tokens),
        )
        return request

    @staticmethod
    def _enforce(request: Request, tokens: list[int]) -> None:
        if not isinstance(tokens, (list, tuple)) or any(
            type(token) is not int or token < 0 for token in tokens
        ):
            raise ValueError("Exact server token IDs are required")
        if len(tokens) > request.budget:
            raise ContextOverflow(
                f"{request.profile}: {len(tokens)} > {request.budget}"
            )
        ContextBuilder._isolate(request)

    @staticmethod
    def _isolate(request):
        combined = html.unescape(request.system + "\n" + request.user)
        if request.profile == "write_offtop" and technical_match(
            combined, request.forbidden_terms
        ):
            raise ContextIsolationError("Technical content in off-topic context")
        if request.profile.startswith("write_") and CYCLE.search(
            normalized_text(combined)
        ):
            raise ContextIsolationError("Physiological content in writing context")

    @staticmethod
    def _data(value):
        return (
            value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        )

    def _writing(self, profile, *, kind, day, mood, wake_reason, **payload):
        if kind not in WRITE_INPUTS:
            raise ValueError(f"Unsupported writing kind: {kind}")
        offtop = kind in OFFTOP_KINDS
        if (profile == "write_offtop") != offtop:
            raise ContextIsolationError("Writing kind does not belong to this profile")
        if not isinstance(day, DayContext) or not isinstance(mood, Mood):
            raise TypeError("Writing requires validated day context and immutable mood")
        optional = (
            {"validation_feedback"}
            if offtop
            else {"study_state", "thesis", "validation_feedback", "topic"}
        )
        required = WRITE_INPUTS[kind]
        if not required <= payload.keys() or payload.keys() - required - optional:
            raise ValueError(f"Unexpected writing blocks for {kind}")
        if self.mood_model is None:
            raise ValueError(
                "A mood model is required to render file-backed descriptions"
            )
        yaml = YAML(typ="safe")
        life = yaml.load((self.config_dir / "life.yaml").read_text(encoding="utf-8"))
        emoji_max = self.settings.get("persona.emoji_max")
        mood_text = self.mood_model.mood_block(mood)
        persona = _COMMENTS.sub(
            "", (self.prompt_dir / "_base_core.md").read_text(encoding="utf-8")
        ).strip()
        if not offtop:
            persona += (
                "\n\n"
                + _COMMENTS.sub(
                    "", (self.prompt_dir / "_base_study.md").read_text(encoding="utf-8")
                ).strip()
            )
        persona_values = {"mood": mood_text, "emoji_max": str(emoji_max)}
        if set(_PLACEHOLDER.findall(persona)) != persona_values.keys():
            raise ValueError("Unexpected persona placeholders")
        persona = _PLACEHOLDER.sub(lambda match: persona_values[match[1]], persona)
        memory = writing_memory(
            self.database,
            day=day,
            offtop=offtop,
            topic=payload.get("topic"),
            include_graph=kind == "summary",
        )
        values = (
            dict(payload)
            | memory
            | {
                "persona": persona,
                "identity": life["identity"],
                "mood": mood_text,
                "when": day.at.isoformat(),
                "daypart": day.daypart,
                "location": day.location,
                "bedtime": day.bedtime.isoformat(),
                "wake_time": day.wake_time.isoformat(),
                "sleep_debt": round(day.sleep_debt, 4),
                "wake_reason": wake_reason,
                "available_objects": day.available_objects,
                "recent_situations": memory.get("recent_slots", []),
            }
        )
        for name in ("issue", "correct", "wrong_post_gist"):
            if name in values:
                values[name] = json.dumps(values[name], ensure_ascii=False)
        raw = (self.prompt_dir / f"write_{kind}.md").read_text(encoding="utf-8")
        temperature = re.search(r"\btemp\s+([0-9.]+)", raw)
        output = re.search(r"регистр\s+<(\w+)>\s*\|\s*(\d+)[–-](\d+)", raw)
        if temperature is None or output is None:
            raise ValueError("Missing writing temperature, mode, or length metadata")
        envelope = _COMMENTS.sub(
            "", (self.prompt_dir / "output_envelope.md").read_text()
        ).strip()
        envelope_values = {
            "mode": output[1],
            "min_chars": output[2],
            "max_chars": output[3],
            "envelope_example": (self.prompt_dir / "examples" / f"{output[1]}.md")
            .read_text()
            .strip(),
        }
        if set(_PLACEHOLDER.findall(envelope)) != envelope_values.keys():
            raise ValueError("Unexpected output-envelope placeholders")
        values["output_envelope"] = _PLACEHOLDER.sub(
            lambda m: envelope_values[m[1]], envelope
        )
        values["day_context"] = {"location": day.location, "daypart": day.daypart}
        template = _COMMENTS.sub("", raw).strip()
        fields = set(_PLACEHOLDER.findall(template))
        if fields - values.keys():
            raise ValueError(f"Missing template fields: {fields - values.keys()}")
        first = next(
            match
            for match in _PLACEHOLDER.finditer(template)
            if match[1] not in {"persona", "identity", "output_envelope"}
        )
        boundary = template.rfind("\n", 0, first.start()) + 1

        def render(text):
            return _PLACEHOLDER.sub(
                lambda match: self._data(BLOCKS[match[1]](values)), text
            )

        # Supplied output-mode metadata is retained verbatim as a system block.
        header = _COMMENTS.search(raw)[0]
        system = header + "\n\n" + render(template[:boundary]).strip()
        supplemental = {
            key: value for key, value in memory.items() if key not in fields
        }
        supplemental["day_context"] = {
            key: value
            for key, value in asdict(day).items()
            if key
            in {"at", "bedtime", "wake_time", "sleep_debt", "location", "daypart"}
        }
        supplemental["day_context"] = {
            key: value.isoformat() if hasattr(value, "isoformat") else value
            for key, value in supplemental["day_context"].items()
        }
        supplemental.update({key: payload[key] for key in optional if key in payload})
        user = (
            render(template[boundary:]).strip()
            + "\n\n"
            + json.dumps(supplemental, ensure_ascii=False)
        )
        request = Request(
            profile,
            system,
            user,
            PROFILES[profile].budget,
            float(temperature[1]),
            output[1],
            int(output[2]),
            int(output[3]),
            node_ids=tuple(node["id"] for node in memory.get("subgraph", [])),
            thread_ids=tuple(thread["id"] for thread in memory.get("open_threads", [])),
        )
        if offtop and self.database is not None:
            with self.database.connection() as connection:
                terms = tuple(
                    row[0]
                    for row in connection.execute("SELECT name FROM nodes ORDER BY id")
                )
            request = replace(request, forbidden_terms=terms)
        self._isolate(request)
        return request
