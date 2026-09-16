"""Output validation contracts, followed by isolated context contracts in step 7."""

from datetime import datetime
from unittest.mock import AsyncMock

import numpy as np
import pytest

from src.core.time_utils import ALMATY
from src.validator import OutputValidator, PastPost, ValidationContext, clean_output

AT = datetime(2026, 9, 16, 19, tzinfo=ALMATY)
TEXT = "The kettle is finally quiet. I can sit by the window with my tea."


@pytest.fixture
def validator():
    llm = AsyncMock()
    llm.embed.return_value = np.array([1.0, 0.0])
    return OutputValidator(llm, echo_similarity=lambda left, right: 0.0)


def context(**kwargs):
    values = dict(at=AT, daypart="evening", sleep_debt=0, offtop=False, prompt="")
    return ValidationContext(**(values | kwargs))


def test_validator_strips_fences():
    raw = '```markdown\n"<casual>' + TEXT + '</casual>"\n```'
    cleaned = clean_output(raw)
    assert cleaned.text == TEXT and cleaned.changes
    assert clean_output(cleaned.text).text == TEXT
    assert clean_output("«Line one.\nLine two.»").text == "Line one.\nLine two."
    assert (
        clean_output("<|turn>model\n<casual>" + TEXT + "</casual><turn|>").text == TEXT
    )


@pytest.mark.parametrize("artifact", ["汉", "日", "\U00020000", "&#x4e2d;"])
async def test_validator_rejects_cjk(validator, artifact):
    result = await validator.validate(TEXT + artifact, context())
    assert "cjk" in result.reasons and not result.accepted
    validator.llm.embed.assert_not_awaited()


@pytest.mark.parametrize(
    "phrase",
    [
        "У меня месячные.",
        "Это ПМС.",
        "Началась овуляция.",
        "Лютеиновая фаза.",
        "Фолликулярная фаза.",
        "Сегодня критические дни.",
        "У меня эти дни.",
        "It is that time of the month.",
        "My menstrual cycle is affecting me.",
        "Period cramps are keeping me awake.",
        "Гормоны опять шалят.",
    ],
)
async def test_validator_rejects_cycle(validator, phrase):
    result = await validator.validate(TEXT + phrase, context())
    assert "cycle" in result.reasons and not result.accepted


@pytest.mark.parametrize(
    "artifact, reason",
    [
        ("カタカナ", "kana"),
        ("(カタカナ)", "kana"),
        ("{{name}}", "jinja"),
        ("{% for x %}", "jinja"),
        (r"\frac{a}{b}", "latex"),
        ("$$x$$", "latex"),
        ("{missing_name}", "placeholder"),
        ("<script>alert(1)</script>", "markup"),
        ("<b>unclosed", "markup"),
        ("Как языковая модель, я не могу.", "refusal"),
        ("As an AI language model, I cannot do that.", "refusal"),
    ],
)
async def test_validator_rejects_artifacts(validator, artifact, reason):
    result = await validator.validate(TEXT + artifact, context())
    assert reason in result.reasons


async def test_validator_allows_kaomoji_safe_html_and_technical_cycle(validator):
    text = "<b>A cycle in the graph</b> is not evidence of a physiological event. (^_^)"
    result = await validator.validate(text, context())
    assert result.accepted
    assert (await validator.validate(TEXT + " ヽ(カタカナ^_^)ノ", context())).accepted


async def test_validator_rejects_structure_empty_and_truncation(validator):
    assert (
        "structure" in (await validator.validate('{"text":"value"}', context())).reasons
    )
    assert "empty" in (await validator.validate("Short.", context())).reasons
    assert "truncated" in (await validator.validate(TEXT + ",", context())).reasons
    assert (
        "truncated" in (await validator.validate(TEXT, context(complete=False))).reasons
    )


async def test_validator_requires_closed_output_mode_and_length(validator):
    ctx = context(mode="casual", min_chars=40, max_chars=100)
    assert (await validator.validate("<casual>" + TEXT + "</casual>", ctx)).accepted
    for raw in (TEXT, "<casual>" + TEXT, "<result>" + TEXT + "</result>"):
        assert "mode" in (await validator.validate(raw, ctx)).reasons
    assert (
        "length"
        in (await validator.validate("<casual>" + TEXT * 2 + "</casual>", ctx)).reasons
    )


async def test_validator_rejects_prompt_echo_at_strict_threshold(validator):
    validator.echo_similarity = lambda left, right: 0.8001
    assert (
        "prompt_echo"
        in (await validator.validate(TEXT, context(prompt="source"))).reasons
    )
    validator.echo_similarity = lambda left, right: 0.8
    assert (await validator.validate(TEXT, context(prompt="source"))).accepted


async def test_validator_rejects_duplicate_against_only_last_thirty(validator):
    posts = [PastPost(str(index), f"Prior post {index}.") for index in range(31)]
    validator.llm.embed.side_effect = [np.array([1.0, 0.0])] + [
        np.array([0.0, 1.0]) for _ in range(30)
    ]
    assert (await validator.validate(TEXT, context(), recent_posts=posts)).accepted
    assert validator.llm.embed.await_count == 31
    validator.llm.embed.side_effect = [np.array([1.0, 0.0]), np.array([0.9, 0.1])]
    result = await validator.validate(TEXT, context(), recent_posts=posts[:1])
    assert "duplicate" in result.reasons and result.duplicate_of == "0"


async def test_validator_rejects_offtop_graph_terms(validator):
    result = await validator.validate(
        TEXT + " Seccomp is useful.", context(offtop=True, graph_terms=("seccomp",))
    )
    assert "offtop_tech" in result.reasons
    assert (await validator.validate(TEXT + " seccomp", context())).accepted


async def test_validator_checks_present_time_and_sleep_facts(validator):
    assert (
        "time_conflict"
        in (await validator.validate(TEXT + " Доброе утро!", context())).reasons
    )
    assert (await validator.validate(TEXT + " Утром было холодно.", context())).accepted
    assert (
        "sleep_conflict"
        in (
            await validator.validate(TEXT + " Я выспалась.", context(sleep_debt=5))
        ).reasons
    )
    assert (
        await validator.validate(TEXT + " Я не выспалась.", context(sleep_debt=5))
    ).accepted


def test_validation_context_rejects_naive_time():
    with pytest.raises(ValueError, match="aware"):
        context(at=AT.replace(tzinfo=None))
