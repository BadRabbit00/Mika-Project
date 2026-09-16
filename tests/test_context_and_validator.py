"""Output validation contracts, followed by isolated context contracts in step 7."""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import numpy as np
import pytest

from src.core.context import ContextBuilder, ContextIsolationError
from src.core.db import Database
from src.core.llm_local import ContextOverflow, LocalLLM
from src.core.mood import MoodModel
from src.core.pad import Mood
from src.core.schedule import SleepWindow
from src.core.time_utils import ALMATY
from src.core.world import World
from src.validator import OutputValidator, PastPost, ValidationContext, clean_output
from src.writer import Writer

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


@pytest.fixture
def writing_db(tmp_path):
    database = Database(tmp_path / "writing.sqlite3")
    database.initialize()
    with database.connection() as connection:
        connection.execute(
            "INSERT INTO sources(id, path, title, topic) "
            "VALUES ('s', 'fixture.md', 'Fixture article', 'security')"
        )
        for node_id, name in (("a", "seccomp"), ("b", "container escape")):
            connection.execute(
                "INSERT INTO nodes(id, name, summary) VALUES (?, ?, ?)",
                (node_id, name, "GRAPH_SECRET"),
            )
        connection.execute(
            "INSERT INTO edges(src, dst, rel, source_id) "
            "VALUES ('a', 'b', 'defends_against', 's')"
        )
        for index in range(16):
            connection.execute(
                "INSERT INTO narrative(at, kind, gist, topic) VALUES (?, ?, ?, ?)",
                (
                    AT - timedelta(hours=index + 1),
                    "summary",
                    f"technical-{index}",
                    "security",
                ),
            )
            connection.execute(
                "INSERT INTO narrative(at, kind, gist) VALUES (?, 'offtop', ?)",
                (AT - timedelta(hours=index + 1), f"life-{index}"),
            )
        connection.execute(
            "INSERT INTO threads(opened_at, kind, text, topic, status, priority) "
            "VALUES (?, 'confusion', 'TECH_THREAD', 'security', 'open', 2)",
            (AT,),
        )
        connection.execute(
            "INSERT INTO threads(opened_at, kind, text, status, priority) "
            "VALUES (?, 'life', 'LIFE_THREAD', 'open', 1)",
            (AT,),
        )
        connection.execute(
            "INSERT INTO people_facts(person_id, fact, at) "
            "VALUES ('someone', 'PRIVATE_FACT', ?)",
            (AT,),
        )
    return database


@pytest.fixture
def day():
    world = World.from_config(Path("config"))
    return world.day_context(
        AT,
        sleep=SleepWindow(AT.replace(hour=1), AT.replace(hour=9)),
        sleep_debt=0,
        location=next(iter(world.locations)),
        road_roll=0.5,
    )


@pytest.fixture
def builder(writing_db):
    return ContextBuilder(
        Path("prompts"),
        database=writing_db,
        mood_model=MoodModel.from_config(Path("config"), epoch=AT),
        offtop_persona="nontechnical_sections",
    )


def writing_blocks(day, **values):
    return dict(day=day, mood=Mood(0.1, 0.2, -0.1), wake_reason="alarm") | values


def test_context_isolation_offtop(builder, day):
    request = builder.build(
        "write_offtop", kind="offtop", **writing_blocks(day, offtop_event=TEXT)
    )
    combined = request.system + request.user
    assert "life-0" in combined and "life-4" in combined and '"life-5"' not in combined
    assert "LIFE_THREAD" in combined
    assert all(
        secret not in combined
        for secret in (
            "GRAPH_SECRET",
            "technical-0",
            "TECH_THREAD",
            "PRIVATE_FACT",
            "seccomp",
        )
    )
    with pytest.raises(ContextIsolationError):
        builder.build(
            "write_offtop",
            kind="offtop",
            **writing_blocks(day, offtop_event="A new seccomp article"),
        )


def test_unchanged_shared_persona_cannot_bypass_offtop_isolation(writing_db, day):
    builder = ContextBuilder(
        Path("prompts"),
        database=writing_db,
        mood_model=MoodModel.from_config(Path("config"), epoch=AT),
    )
    with pytest.raises(ContextIsolationError):
        builder.build(
            "write_offtop", kind="offtop", **writing_blocks(day, offtop_event=TEXT)
        )


def test_context_technical_memory_is_bounded_and_excludes_life(builder, day):
    request = builder.build(
        "write_tech", kind="summary", **writing_blocks(day, topic="security")
    )
    combined = request.system + request.user
    assert "GRAPH_SECRET" in request.user and "TECH_THREAD" in request.user
    assert "technical-11" in combined and "technical-12" not in combined
    assert all(
        secret not in combined for secret in ("life-0", "LIFE_THREAD", "PRIVATE_FACT")
    )
    assert all(phase not in combined for phase in ("menstrual", "ovulatory", "luteal"))


def test_curator_text_uses_user_role(builder, day):
    request = builder.build(
        "write_tech",
        kind="correction",
        **writing_blocks(
            day,
            issue="CURATOR_ISSUE",
            correct="CURATOR_CORRECTION",
            wrong_post_gist="OLD_GIST",
        ),
    )
    assert (
        "CURATOR_CORRECTION" in request.user
        and "CURATOR_CORRECTION" not in request.system
    )
    assert "CURATOR_ISSUE" in request.user and "CURATOR_ISSUE" not in request.system


def test_people_facts_excluded_from_posts(builder, day):
    with pytest.raises(ValueError):
        builder.build(
            "write_offtop",
            kind="offtop",
            **writing_blocks(day, offtop_event=TEXT, people_facts="PRIVATE_FACT"),
        )


def test_context_rejects_graph_vocabulary_and_physiology(builder, writing_db, day):
    with writing_db.connection() as connection:
        connection.execute(
            "INSERT INTO nodes(id, name) VALUES ('c', 'NovelDomainTerm')"
        )
    for event in ("NovelDomainTerm", "Лютеиновая фаза"):
        with pytest.raises(ContextIsolationError):
            builder.build(
                "write_offtop", kind="offtop", **writing_blocks(day, offtop_event=event)
            )


def test_context_keeps_substituted_braces_as_data(builder, day):
    request = builder.build(
        "write_tech",
        kind="correction",
        **writing_blocks(
            day, issue="{persona}", correct="{subgraph}", wrong_post_gist="OLD_GIST"
        ),
    )
    assert "{persona}" in request.user and "{subgraph}" in request.user
    assert "GRAPH_SECRET" not in request.user


def test_technical_variants_can_scope_threads_to_the_current_topic(builder, day):
    request = builder.build(
        "write_tech",
        kind="struggle",
        **writing_blocks(
            day, confusion="Unresolved question", articles_read=2, topic="security"
        ),
    )
    assert "TECH_THREAD" in request.user and "LIFE_THREAD" not in request.user


def test_context_does_not_restore_excluded_or_stale_memory(builder, writing_db, day):
    with writing_db.connection() as connection:
        connection.execute("UPDATE narrative SET excluded=1 WHERE gist='technical-0'")
        connection.execute(
            "UPDATE threads SET opened_at=? WHERE kind='confusion'",
            (AT - timedelta(days=6),),
        )
    request = builder.build(
        "write_tech", kind="summary", **writing_blocks(day, topic="security")
    )
    assert "technical-0" not in request.user and "TECH_THREAD" not in request.user


def test_unread_article_context_has_no_graph_contents(builder, day):
    request = builder.build(
        "write_tech",
        kind="found",
        **writing_blocks(
            day,
            article_title="Article",
            article_source="Publisher",
            article_kind="paper",
            given_by="curator",
            topic="security",
        ),
    )
    assert "GRAPH_SECRET" not in request.system + request.user


async def test_token_budget_enforced(builder, day):
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "server rendered fixture"})
        return httpx.Response(200, json={"tokens": [1] * 3001})

    async with LocalLLM(transport=httpx.MockTransport(handle)) as llm:
        with pytest.raises(ContextOverflow):
            await builder.build_checked(
                "write_offtop",
                llm=llm,
                kind="offtop",
                **writing_blocks(day, offtop_event=TEXT),
            )
    assert paths == ["/apply-template", "/tokenize"]


def writer_llm(outputs):
    llm = AsyncMock()
    llm.prompt_tokens.return_value = [1, 2, 3]
    llm.tokenize.return_value = [4, 5]
    llm.generate.side_effect = outputs
    llm.embed.return_value = np.array([1.0, 0.0])
    return llm


async def test_writer_validates_before_saving_and_keeps_attempts_stateless(
    builder, writing_db, day
):
    valid = TEXT * 4
    llm = writer_llm(
        ["<casual>" + "汉" * 220 + "</casual>", "<casual>" + valid + "</casual>"]
    )
    writer = Writer(
        writing_db, llm, builder, OutputValidator(llm, echo_similarity=lambda a, b: 0)
    )
    result = await writer.generate("offtop", **writing_blocks(day, offtop_event=TEXT))
    assert result.status == "draft" and result.text == valid and result.attempts == 2
    assert all(
        "汉" not in call.args[0].system + call.args[0].user
        for call in llm.generate.call_args_list
    )
    with writing_db.connection() as connection:
        assert (
            connection.execute(
                "SELECT text FROM posts WHERE id=?", (result.id,)
            ).fetchone()[0]
            == valid
        )
        rows = connection.execute(
            "SELECT status, error FROM runs ORDER BY rowid"
        ).fetchall()
        assert (
            len(rows) == 2
            and "cjk" in rows[0]["error"]
            and rows[-1]["status"] == "validated"
        )
        assert connection.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0
        assert (
            connection.execute("SELECT count(*) FROM life_journal").fetchone()[0] == 0
        )


async def test_writer_kills_after_three_invalid_outputs(builder, writing_db, day):
    llm = writer_llm(["malformed"] * 3)
    writer = Writer(
        writing_db, llm, builder, OutputValidator(llm, echo_similarity=lambda a, b: 0)
    )
    result = await writer.generate("offtop", **writing_blocks(day, offtop_event=TEXT))
    assert result.status == "killed" and result.text is None and result.attempts == 3
    with writing_db.connection() as connection:
        row = connection.execute(
            "SELECT state, text FROM posts WHERE id=?", (result.id,)
        ).fetchone()
        assert row["state"] == "killed" and row["text"] is None


async def test_writer_propagates_transport_failure_to_durable_runner(
    builder, writing_db, day
):
    llm = writer_llm([httpx.ConnectError("offline")])
    writer = Writer(
        writing_db, llm, builder, OutputValidator(llm, echo_similarity=lambda a, b: 0)
    )
    with pytest.raises(httpx.ConnectError):
        await writer.generate("offtop", **writing_blocks(day, offtop_event=TEXT))
    llm.generate.assert_awaited_once()
    with writing_db.connection() as connection:
        assert connection.execute("SELECT count(*) FROM posts").fetchone()[0] == 0


async def test_writer_preserves_incoming_trace(builder, writing_db, day):
    import json

    import structlog

    llm = writer_llm(["<casual>" + TEXT * 4 + "</casual>"])
    writer = Writer(
        writing_db, llm, builder, OutputValidator(llm, echo_similarity=lambda a, b: 0)
    )
    with structlog.contextvars.bound_contextvars(trace_id="root-chain"):
        result = await writer.generate(
            "offtop", **writing_blocks(day, offtop_event=TEXT)
        )
    with writing_db.connection() as connection:
        row = connection.execute(
            "SELECT params_json FROM runs "
            "WHERE json_extract(params_json, '$.post_id')=?",
            (result.id,),
        ).fetchone()
        assert json.loads(row[0])["trace_id"] == "root-chain"


async def test_writer_respects_sleep_blackout(builder, writing_db):
    world = World.from_config(Path("config"))
    night = world.day_context(
        AT.replace(hour=3),
        sleep=SleepWindow(AT.replace(hour=1), AT.replace(hour=9)),
        sleep_debt=0,
        location=next(iter(world.locations)),
        road_roll=0.5,
    )
    llm = writer_llm([])
    writer = Writer(
        writing_db, llm, builder, OutputValidator(llm, echo_similarity=lambda a, b: 0)
    )
    result = await writer.generate("offtop", **writing_blocks(night, offtop_event=TEXT))
    assert result.status == "blocked" and result.attempts == 0
    llm.generate.assert_not_awaited()
