"""Three-layer output checks without publication or storage side effects."""

import html
import json
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

import structlog

from src.core.content_rules import CYCLE, normalized_text, technical_match
from src.core.pad import finite
from src.core.settings import SettingsRegistry
from src.core.time_utils import require_aware
from src.core.vectors import cosine

log = structlog.get_logger("blogai.validator")
_MODES = re.compile(r"</?(?:casual|result|struggle)>", re.I)
_SERVICE = re.compile(r"<\|?/?(?:turn|channel|think|image|audio)\|?>", re.I)
_KANA = re.compile(r"[\u3040-\u30ff]{4,}")
_KAOMOJI = re.compile(r"[（(][^()（）\n]*(?:\^[_ .-]?\^|[Tt]_+[Tt])[^()（）\n]*[)）]")
_REFUSAL = re.compile(
    r"как\s+(?:языковая\s+модель|ии)|в\s+качестве\s+ии|я\s+не\s+могу|"
    r"as\s+(?:an?\s+)?(?:ai|language\s+model)|i\s+(?:cannot|can't)",
    re.I,
)


@dataclass(frozen=True)
class CleanedOutput:
    text: str
    changes: tuple[str, ...]


def clean_output(raw: str, *, strip_modes: bool = True) -> CleanedOutput:
    if not isinstance(raw, str):
        raise TypeError("Model output must be text")
    text, changes = raw, []
    rules = (
        ("fence", r"^\s*```[a-z]*\s*\n?", ""),
        ("fence", r"\n?```\s*$", ""),
        ("format_label", r"^(json|markdown|md|yaml|html|text)\s*\n", ""),
        ("preamble", r"^(Вот|Конечно|Хорошо)[,:]?\s*(пост|текст|ответ)[^\n]*\n", ""),
        ("service_role", r"^\s*<\|turn>(?:model|assistant)\s*", ""),
        ("thought", r"<(?:\|)?think(?:\|)?>.*?</(?:\|)?think(?:\|)?>", ""),
    )
    while True:
        previous = text
        for name, pattern, replacement in rules:
            text, count = re.subn(pattern, replacement, text, flags=re.I | re.S)
            if count:
                changes.append(name)
        text, count = _SERVICE.subn("", text)
        if count:
            changes.append("service_tag")
        text = text.strip()
        if len(text) >= 2 and (text[0], text[-1]) in (
            ("«", "»"),
            ('"', '"'),
            ("'", "'"),
        ):
            text = text[1:-1].strip()
            changes.append("outer_quotes")
        if strip_modes:
            text, count = _MODES.subn("", text)
            if count:
                changes.append("mode_tag")
            text = text.strip()
        if text == previous:
            break
    if text != raw and not changes:
        changes.append("whitespace")
    if changes:
        log.info("output_cleaned", changes=changes)
    return CleanedOutput(text, tuple(dict.fromkeys(changes)))


class _TelegramHTML(HTMLParser):
    """Validate standard Bot API HTML, including attributes and balanced tags."""

    SIMPLE = {
        "b",
        "strong",
        "i",
        "em",
        "u",
        "ins",
        "s",
        "strike",
        "del",
        "tg-spoiler",
        "pre",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.parts = [], []
        self.invalid = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        valid = len(values) == len(attrs)
        if tag in self.SIMPLE:
            valid &= not values
        elif tag == "span":
            valid &= values == {"class": "tg-spoiler"}
        elif tag == "a":
            valid &= set(values) == {"href"}
            valid &= urlsplit(values.get("href") or "").scheme in {
                "https",
                "http",
                "tg",
                "mailto",
                "tel",
            }
        elif tag == "code":
            valid &= not values or (
                self.stack[-1:] == ["pre"]
                and set(values) == {"class"}
                and (values["class"] or "").startswith("language-")
            )
        elif tag == "blockquote":
            valid &= values in ({}, {"expandable": None}) and tag not in self.stack
        elif tag == "tg-emoji":
            valid &= (
                set(values) == {"emoji-id"} and (values["emoji-id"] or "").isdigit()
            )
        elif tag == "tg-time":
            valid &= (
                set(values) <= {"unix", "format"}
                and (values.get("unix") or "").isdigit()
            )
            valid &= "format" not in values or bool(
                re.fullmatch(r"[rwtTdD]+", values["format"] or "")
            )
        else:
            valid = False
        self.invalid |= not valid
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.invalid = True

    def handle_startendtag(self, tag, attrs):
        self.invalid = True

    def handle_data(self, data):
        self.parts.append(data)

    def handle_comment(self, data):
        self.invalid = True

    def handle_decl(self, decl):
        self.invalid = True


@dataclass(frozen=True)
class ValidationContext:
    at: datetime
    daypart: str
    sleep_debt: float
    offtop: bool
    prompt: str
    graph_terms: tuple[str, ...] = ()
    mode: str | None = None
    min_chars: int = 40
    max_chars: int | None = None
    complete: bool = True
    dialogue: bool = False

    def __post_init__(self):
        object.__setattr__(self, "at", require_aware(self.at))
        if finite(self.sleep_debt) < 0:
            raise ValueError("Sleep debt must be nonnegative")
        if self.mode not in (None, "casual", "result", "struggle"):
            raise ValueError("Unknown output mode")
        if self.min_chars < (1 if self.dialogue else 40) or (
            self.max_chars is not None and self.max_chars < self.min_chars
        ):
            raise ValueError("Invalid post length interval")


@dataclass(frozen=True)
class PastPost:
    id: str
    text: str


@dataclass(frozen=True)
class ValidationResult:
    text: str
    reasons: tuple[str, ...]
    cleanups: tuple[str, ...]
    duplicate_of: str | None = None

    @property
    def accepted(self) -> bool:
        return not self.reasons


async def token_echo_similarity(llm, left: str, right: str) -> float:
    """Jaccard similarity of five-token sets from the generation tokenizer."""
    sets = []
    for text in (left, right):
        tokens = await llm.tokenize(text)
        if not isinstance(tokens, list) or any(
            type(t) is not int or t < 0 for t in tokens
        ):
            raise ValueError("Echo scoring requires exact server token IDs")
        sets.append({tuple(tokens[i : i + 5]) for i in range(len(tokens) - 4)})
    union = sets[0] | sets[1]
    return len(sets[0] & sets[1]) / len(union) if union else 0.0


class OutputValidator:
    def __init__(
        self,
        llm,
        *,
        echo_similarity: Callable[[str, str], float] | None = None,
        settings=None,
    ):
        self.llm, self.echo_similarity = llm, echo_similarity
        self.settings = settings or SettingsRegistry.from_file("config/settings.yaml")

    async def validate(
        self,
        raw: str,
        context: ValidationContext,
        *,
        recent_posts: Sequence[PastPost] = (),
    ) -> ValidationResult:
        envelope = clean_output(raw, strip_modes=False)
        reasons = []
        echo_threshold = self.settings.get("validator.echo_threshold")
        repeat_threshold = self.settings.get("validator.repeat_threshold")
        echo_score = repeat_score = None
        if context.mode is not None:
            match = re.fullmatch(
                r"<" + context.mode + r">(.*?)</" + context.mode + r">",
                envelope.text,
                re.S,
            )
            if match is None or _MODES.search(match[1]):
                reasons.append("mode")
        cleaned = clean_output(envelope.text)
        text = cleaned.text
        parser = _TelegramHTML()
        try:
            parser.feed(text)
            parser.close()
        except (ValueError, AssertionError):
            parser.invalid = True
        visible = "".join(parser.parts)
        scan = normalized_text(html.unescape(text) + " " + visible)
        if any(
            "CJK" in unicodedata.name(char, "")
            and "IDEOGRAPH" in unicodedata.name(char, "")
            for char in scan
        ):
            reasons.append("cjk")
        if _KANA.search(_KAOMOJI.sub("", scan)):
            reasons.append("kana")
        if any(marker in scan for marker in ("{{", "}}", "{%", "%}")):
            reasons.append("jinja")
        if any(marker in scan for marker in (r"\begin{", r"\frac", "$$", r"\[")):
            reasons.append("latex")
        if text.startswith(("{", "[")):
            try:
                json.loads(text)
                reasons.append("structure")
            except ValueError:
                pass
        if re.search(r"\{[\w.:]+\}", scan):
            reasons.append("placeholder")
        if _REFUSAL.search(scan):
            reasons.append("refusal")
        if not any(character.isalnum() for character in visible) or (
            not context.dialogue and len(visible.strip()) < 40
        ):
            reasons.append("empty")
        if len(visible) < context.min_chars or (
            context.max_chars is not None and len(visible) > context.max_chars
        ):
            reasons.append("length")
        if (
            parser.invalid
            or parser.stack
            or re.search(r"&(?!(?:amp|lt|gt|quot|#\d+|#x[0-9a-f]+);)\w+;", text, re.I)
        ):
            reasons.append("markup")
        if not context.complete or visible.rstrip().endswith(","):
            reasons.append("truncated")
        if context.prompt:
            score = finite(
                self.echo_similarity(context.prompt, visible)
                if self.echo_similarity
                else await token_echo_similarity(self.llm, context.prompt, visible)
            )
            if not 0 <= score <= 1:
                raise ValueError("Echo similarity must be in [0, 1]")
            echo_score = score
            if score > echo_threshold:
                reasons.append("prompt_echo")
        if CYCLE.search(scan):
            reasons.append("cycle")
        if context.offtop and technical_match(scan, context.graph_terms):
            reasons.append("offtop_tech")
        current_claims = {
            "morning": (
                r"доброе\s+утро|сейчас\s+утро|good\s+morning|"
                r"it(?:'s|\s+is)\s+morning"
            ),
            "evening": (
                r"добрый\s+вечер|сейчас\s+вечер|good\s+evening|"
                r"it(?:'s|\s+is)\s+evening"
            ),
            "night": r"сейчас\s+ночь|it(?:'s|\s+is)\s+night",
        }
        actual = "night" if context.daypart == "deep_night" else context.daypart
        if any(
            key != actual and re.search(pattern, scan)
            for key, pattern in current_claims.items()
        ):
            reasons.append("time_conflict")
        if context.sleep_debt >= 5 and re.search(
            r"(?<!не )\bвыспалась\b|\bi\s+(?:am|feel)\s+well[- ]rested\b", scan
        ):
            reasons.append("sleep_conflict")
        duplicate = None
        if not reasons and recent_posts:
            vector = await self.llm.embed(visible)
            for post in recent_posts[:30]:
                score = cosine(vector, await self.llm.embed(post.text))
                repeat_score = score
                if score > repeat_threshold:
                    reasons.append("duplicate")
                    duplicate = post.id
                    break
        result = ValidationResult(
            text,
            tuple(dict.fromkeys(reasons)),
            tuple(dict.fromkeys(envelope.changes + cleaned.changes)),
            duplicate,
        )
        log.info(
            "output_validated",
            accepted=result.accepted,
            reasons=result.reasons,
            duplicate_of=duplicate,
            echo_score=echo_score,
            echo_threshold=echo_threshold,
            repeat_score=repeat_score,
            repeat_threshold=repeat_threshold,
        )
        return result
