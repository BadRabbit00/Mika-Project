"""Knowledge extraction, retrieval, and self-quiz behavioral contracts."""

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import numpy as np
import pytest

from src.core.context import ContextBuilder
from src.core.db import Database
from src.core.llm_local import ContextOverflow, LocalLLM
from src.core.vectors import cosine, decode_vector, encode_vector
from src.extract import Extractor, chunk_text, grounded, validate_claims
from src.ingest import Source, read_source
from src.retrieve import RetrievalPolicy, RetrievedNode, Retriever
from src.selfquiz import QuizSettings, SelfQuiz


@pytest.fixture
def database(tmp_path):
    instance = Database(tmp_path / "knowledge.sqlite3")
    instance.initialize()
    return instance


def claim(src="seccomp", rel="defends_against", dst="container escape", **extra):
    return {"text": f"{src} {rel} {dst}.", "src": src, "rel": rel, "dst": dst, **extra}


def source(
    text="seccomp defends against container escape.", source_id="one", origin="one"
):
    return Source(
        id=source_id,
        path=Path(f"{source_id}.md"),
        title=source_id,
        topic="security",
        origin_key=origin,
        text=text,
    )


def fake_llm(outputs=None):
    llm = AsyncMock()
    llm.tokenize.side_effect = lambda text, **_: list(text.encode("utf-8"))
    llm.detokenize.side_effect = lambda tokens: bytes(tokens).decode("utf-8")
    llm.embed.side_effect = lambda text: np.array(
        [1.0, 0.0] if "seccomp" in text else [0.0, 1.0]
    )
    llm.embedding_model.return_value = "fixture-embedding"
    llm.generate.side_effect = outputs or [json.dumps(claim()) + "\n"]
    return llm


def test_claims_validator():
    data = [
        claim(),
        claim(src="invented"),
        claim(src="SECCOMP", dst="seccomp"),
        claim(rel="related_to"),
        claim(src="x"),
        claim(text=""),
        claim(tools=["write_database"]),
    ]
    accepted, rejected = validate_claims(
        data, "seccomp defends against container escape."
    )
    assert len(accepted) == 1
    assert {rejection.reason for rejection in rejected} == {
        "ungrounded",
        "self",
        "rel",
        "empty",
        "schema",
    }
    assert not grounded("cat", "education")
    assert grounded("Container Escape", "A container  escape is possible.")


async def test_chunk_overlap():
    text = "0123456789" * 501
    llm = fake_llm()
    chunks = await chunk_text(text, llm)
    assert [len(chunk.tokens) for chunk in chunks] == [2000, 2000, 1410]
    assert all(
        left.tokens[-200:] == right.tokens[:200]
        for left, right in zip(chunks, chunks[1:], strict=False)
    )
    assert chunks[0].text + "".join(chunk.text[200:] for chunk in chunks[1:]) == text
    assert await chunk_text("", llm) == []


async def test_chunking_does_not_corrupt_utf8():
    text = "я" * 2501
    chunks = await chunk_text(text, fake_llm())
    assert all(len(chunk.tokens) <= 2000 for chunk in chunks)
    assert all("\ufffd" not in chunk.text for chunk in chunks)
    assert chunks[-1].stop == len(text.encode("utf-8"))


async def test_model_output_validated_before_writes(database):
    llm = fake_llm([json.dumps(claim(src="invented")) + "\n"])
    result = await Extractor(database, llm, ContextBuilder(Path("prompts"))).extract(
        source()
    )
    assert result.accepted == 0 and result.rejected == 1
    with database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM nodes").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM claims").fetchone()[0] == 0


async def test_triplet_deduplication(database):
    llm = fake_llm(
        [json.dumps(claim()) + "\n" + json.dumps(claim(src="SECCOMP")) + "\n"]
    )
    extractor = Extractor(database, llm, ContextBuilder(Path("prompts")))
    first = await extractor.extract(source())
    again = await extractor.extract(source())
    assert first.accepted == 1 and again.accepted == 0
    assert llm.generate.await_count == 1
    with database.connection() as connection:
        row = connection.execute("SELECT norm_hash FROM claims").fetchone()
        assert len(row[0]) == hashlib.sha256().digest_size * 2
        assert connection.execute("SELECT count(*) FROM edges").fetchone()[0] == 1


async def test_independent_sources_keep_separate_claims(database):
    llm = fake_llm([json.dumps(claim()) + "\n"] * 2)
    extractor = Extractor(database, llm, ContextBuilder(Path("prompts")))
    await extractor.extract(source())
    await extractor.extract(source(source_id="two", origin="two"))
    with database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM nodes").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM claims").fetchone()[0] == 2
        assert (
            connection.execute(
                "SELECT count(DISTINCT norm_hash) FROM claims"
            ).fetchone()[0]
            == 1
        )


async def test_three_articles_build_one_deduplicated_graph(database):
    claims = [
        claim(),
        claim("Yama", "is_a", "Linux Security Module"),
        claim("seccomp", "requires", "no_new_privs"),
    ]
    articles = [
        "seccomp defends against container escape.",
        "Yama is a Linux Security Module.",
        "Unprivileged seccomp filters require no_new_privs.",
    ]
    names = sorted(
        {item[field].casefold() for item in claims for field in ("src", "dst")}
    )
    vectors = {name: np.eye(len(names))[index] for index, name in enumerate(names)}
    llm = fake_llm([json.dumps(item) + "\n" for item in claims])
    llm.embed.side_effect = lambda text: vectors[text]
    extractor = Extractor(database, llm, ContextBuilder(Path("prompts")))
    reports = [
        await extractor.extract(source(text, source_id=str(index), origin=str(index)))
        for index, text in enumerate(articles)
    ]
    assert [report.accepted for report in reports] == [1, 1, 1]
    with database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM claims").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM nodes").fetchone()[0] == 5


async def test_extraction_failure_rolls_back_entire_article(database):
    llm = fake_llm([json.dumps(claim()) + "\n", "broken json"])
    with pytest.raises(ValueError):
        await Extractor(database, llm, ContextBuilder(Path("prompts"))).extract(
            source("seccomp container escape " * 200)
        )
    with database.connection() as connection:
        for table in ("sources", "nodes", "edges", "claims"):
            assert (
                connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
            )


async def test_jsonl_preserves_unicode_separators_inside_strings(database):
    raw = json.dumps(
        claim(text="seccomp\u2028defends against container escape."), ensure_ascii=False
    )
    report = await Extractor(
        database, fake_llm([raw]), ContextBuilder(Path("prompts"))
    ).extract(source())
    assert report.accepted == 1


async def test_fts_then_embeddings_merge_nodes(database):
    llm = fake_llm(
        [json.dumps(claim()) + "\n", json.dumps(claim(src="seccomp-bpf")) + "\n"]
    )
    extractor = Extractor(database, llm, ContextBuilder(Path("prompts")))
    await extractor.extract(source())
    await extractor.extract(
        source("seccomp-bpf prevents container escape.", source_id="two")
    )
    with database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM nodes").fetchone()[0] == 2
        assert (
            connection.execute("SELECT count(*) FROM node_embeddings").fetchone()[0]
            == 2
        )


async def test_merged_self_claim_does_not_leave_orphan_nodes(database):
    llm = fake_llm()
    llm.embed.side_effect = lambda text: np.array([1.0, 0.0])
    report = await Extractor(database, llm, ContextBuilder(Path("prompts"))).extract(
        source()
    )
    assert report.accepted == 0 and report.rejected == 1
    with database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM nodes").fetchone()[0] == 0


def test_source_frontmatter_date_is_not_invented(tmp_path):
    path = tmp_path / "article.md"
    path.write_text(
        "---\nid: article\ntitle: Article\ntopic: security\n"
        "published_at: 2026-09-16\n---\nseccomp\n"
    )
    result = read_source(path)
    assert result.id == "article"
    assert result.published_at is None
    assert result.text == "seccomp\n"


async def test_tokens_counted_with_tokenize():
    calls = []

    def handle(request):
        calls.append((request.url.port, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"tokens": [4, 8, 15]})

    async with LocalLLM(transport=httpx.MockTransport(handle)) as llm:
        assert await llm.tokenize("Tokenizer fixture") == [4, 8, 15]
        assert await llm.tokenize("Tokenizer fixture") == [4, 8, 15]
    assert len(calls) == 1
    assert calls[0][0:2] == (8080, "/tokenize")
    assert calls[0][2]["add_special"] is False


async def test_token_budget_enforced():
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "Rendered fixture"})
        return httpx.Response(200, json={"tokens": [1] * 6001})

    request = ContextBuilder(Path("prompts")).build(
        "extract", existing_node_names=[], article_chunk="Body"
    )
    async with LocalLLM(transport=httpx.MockTransport(handle)) as llm:
        with pytest.raises(ContextOverflow):
            await llm.generate(request)
    assert "/completion" not in paths


async def test_model_tools_cannot_write():
    sent = []

    def handle(request):
        payload = json.loads(request.content)
        sent.append(payload)
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "Rendered fixture"})
        if request.url.path == "/tokenize":
            tokens = [0, 1, 2] if payload["add_special"] else [1, 2]
            return httpx.Response(200, json={"tokens": tokens})
        return httpx.Response(
            200, json={"content": "{}", "stop": True, "stopped_limit": False}
        )

    request = ContextBuilder(Path("prompts")).build(
        "extract", existing_node_names=[], article_chunk="Body"
    )
    async with LocalLLM(transport=httpx.MockTransport(handle)) as llm:
        await llm.generate(request, grammar=Path("grammars/claims.gbnf").read_text())
    assert all(
        "tools" not in payload and "tool_choice" not in payload for payload in sent
    )
    completion = next(payload for payload in sent if "grammar" in payload)
    assert completion["grammar"] == Path("grammars/claims.gbnf").read_text()
    assert completion["prompt"] == [0, 1, 2]


async def test_embedding_endpoint_is_separate():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [3.0, 4.0]}]}
        )

    async with LocalLLM(transport=httpx.MockTransport(handle)) as llm:
        assert np.allclose(await llm.embed("Node"), [3.0, 4.0])
    assert requests[0].url.port == 8081
    assert requests[0].url.path == "/v1/embeddings"


@pytest.mark.parametrize(
    "termination",
    [{"stopped_limit": True}, {"stop_type": "limit"}, {"truncated": True}],
)
async def test_truncated_generation_is_rejected(termination):
    def handle(request):
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "Rendered fixture"})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1]})
        return httpx.Response(200, json={"content": "{}", **termination})

    request = ContextBuilder(Path("prompts")).build(
        "extract", existing_node_names=[], article_chunk="Body"
    )
    async with LocalLLM(transport=httpx.MockTransport(handle)) as llm:
        with pytest.raises(ValueError, match="truncated"):
            await llm.generate(request)


def test_embedding_storage_rejects_incompatible_vectors():
    vector = np.array([3.0, 4.0])
    blob = encode_vector(vector)
    assert np.array_equal(decode_vector(blob), vector)
    assert cosine(vector, vector) == pytest.approx(1)
    for invalid in ([], [0.0, 0.0], [np.nan, 1], [np.inf, 1], [1e308, 1e308]):
        with pytest.raises(ValueError):
            encode_vector(np.array(invalid))
    with pytest.raises(ValueError):
        cosine(vector, np.array([1.0]))
    with pytest.raises(ValueError):
        decode_vector(blob + b"trailing")


@pytest.fixture
async def knowledge(database):
    await Extractor(database, fake_llm(), ContextBuilder(Path("prompts"))).extract(
        source()
    )
    with database.connection() as connection:
        return [
            RetrievedNode(row["id"], row["name"], row["summary"], ())
            for row in connection.execute("SELECT * FROM nodes ORDER BY id")
        ]


def quiz(database, llm, retrieved):
    retriever = AsyncMock()
    retriever.search.return_value = retrieved
    return SelfQuiz(
        database,
        llm,
        retriever,
        ContextBuilder(Path("prompts")),
        QuizSettings.from_registry(Path("config/settings.yaml")),
    )


async def test_context_isolation_quiz(database, knowledge):
    secret = "Private node summary fixture"
    database.run_transaction(
        lambda connection: connection.execute("UPDATE nodes SET summary=?", (secret,))
    )
    llm = fake_llm(["How are seccomp and container escape connected?"])
    questions = await quiz(database, llm, knowledge).ask("security")
    assert len(questions) == 1
    request = llm.generate.call_args.args[0]
    assert request.profile == "selfquiz_ask" and request.budget == 2000
    for node in knowledge:
        assert node.name in request.user
        assert node.id not in request.user
    assert secret not in request.system + request.user
    assert "defends_against" not in request.system + request.user
    with pytest.raises(ValueError):
        ContextBuilder(Path("prompts")).build(
            "selfquiz_ask",
            topic_node_names=[],
            asked_questions=[],
            n="5",
            retrieved_nodes=knowledge,
        )
    with pytest.raises(TypeError):
        ContextBuilder(Path("prompts")).build(
            "selfquiz_ask",
            topic_node_names=[{"name": "node", "summary": secret}],
            asked_questions=[],
            n="5",
        )


async def test_empty_retriever_no_llm_call(database):
    llm = fake_llm()
    result = await quiz(database, llm, []).answer("Unknown fact?", topic="security")
    assert result.verdict == "no_knowledge"
    assert result.cited == result.retrieved == ()
    assert llm.mock_calls == []
    with database.connection() as connection:
        row = connection.execute("SELECT * FROM questions").fetchone()
        assert row["verdict"] == "no_knowledge" and row["asked_at"].endswith("Z")


@pytest.mark.parametrize("case", ["unknown", "mixed", "unretrieved", "wrong_type"])
async def test_invalid_citations_rejected(database, knowledge, case):
    returned = [knowledge[0]]
    cited = {
        "unknown": ["invented-id"],
        "mixed": [knowledge[0].id, "invented-id"],
        "unretrieved": [knowledge[1].id],
        "wrong_type": knowledge[0].id,
    }[case]
    llm = fake_llm(
        [json.dumps({"answer": "Fixture answer", "cited": cited, "confident": True})]
    )
    result = await quiz(database, llm, returned).answer("Fixture?", topic="security")
    assert result.verdict == "invalid_citation"


async def test_valid_citation_is_persisted_and_context_is_fresh(database, knowledge):
    payload = {
        "answer": "Fixture answer",
        "cited": [knowledge[0].id],
        "confident": True,
    }
    llm = fake_llm([json.dumps(payload), json.dumps(payload)])
    service = quiz(database, llm, knowledge)
    result = await service.answer("First isolated question?", topic="security")
    await service.answer("Second isolated question?", topic="security")
    assert result.verdict == "answered" and result.cited == (knowledge[0].id,)
    request = llm.generate.call_args.args[0]
    assert request.profile == "selfquiz_answer" and request.budget == 3000
    assert "First isolated question?" not in request.user
    with database.connection() as connection:
        row = connection.execute(
            "SELECT * FROM questions WHERE id=?", (result.question_id,)
        ).fetchone()
        assert json.loads(row["cited"]) == [knowledge[0].id]
        assert json.loads(row["retrieved"]) == [node.id for node in knowledge]


async def test_citation_is_checked_again_at_commit(database, knowledge):
    async def generate(*args, **kwargs):
        database.run_transaction(
            lambda connection: connection.execute(
                "UPDATE nodes SET suspect=1 WHERE id=?", (knowledge[0].id,)
            )
        )
        return json.dumps(
            {"answer": "Fixture", "cited": [knowledge[0].id], "confident": True}
        )

    llm = fake_llm()
    llm.generate.side_effect = generate
    result = await quiz(database, llm, knowledge).answer("Fixture?", topic="security")
    assert result.verdict == "invalid_citation"


async def test_hybrid_retrieval_and_topic_isolation(database, knowledge):
    llm = fake_llm()
    llm.embed.side_effect = lambda text: np.array([1.0, 0.0])
    retriever = Retriever(database, llm, RetrievalPolicy(min_similarity=0.8, rrf_k=60))
    semantic = await retriever.search("lexically absent", topic="security")
    assert [node.name for node in semantic] == ["seccomp"]
    hybrid = await retriever.search("container escape", topic="security")
    assert {node.id for node in hybrid} == {node.id for node in knowledge}
    assert any(node.edges for node in hybrid)
    calls = len(llm.mock_calls)
    assert await retriever.search("seccomp", topic="other") == []
    assert len(llm.mock_calls) == calls
    assert await retriever.search('" OR *', topic="security") == semantic


async def test_quiz_round_requires_five_questions_and_three_answers(
    database, knowledge
):
    questions = [f"Fixture question {index}?" for index in range(5)]
    outputs = ["\n".join(questions)] + [
        json.dumps(
            {
                "answer": "Fixture",
                "cited": [knowledge[0].id] if i < 3 else [],
                "confident": i < 3,
            }
        )
        for i in range(5)
    ]
    result = await quiz(database, fake_llm(outputs), knowledge).run("security")
    assert len(result.results) == 5 and result.answered == 3 and result.passed
    short = await quiz(database, fake_llm(["One more?", outputs[1]]), knowledge).run(
        "security"
    )
    assert short.answered == 1 and not short.passed


async def test_completed_question_replay_skips_retrieval_and_model(database, knowledge):
    payload = {"answer": "Fixture", "cited": [knowledge[0].id], "confident": True}
    llm = fake_llm([json.dumps(payload)])
    service = quiz(database, llm, knowledge)
    result = await service.answer("Fixture?", topic="security")
    repeated = await service.answer(
        "Fixture?", topic="security", question_id=result.question_id
    )
    assert repeated == result
    assert llm.generate.await_count == service.retriever.search.await_count == 1


async def test_retrieval_top_six_excludes_suspect_nodes_and_external_edges(
    database, knowledge
):
    def seed(connection):
        for i in range(8):
            node_id = f"extra-{i}"
            connection.execute(
                "INSERT INTO nodes(id, name, summary, suspect) VALUES (?, ?, ?, ?)",
                (node_id, "shared", "fixture", int(i == 0)),
            )
            connection.execute(
                "INSERT INTO edges(src, rel, dst, source_id) "
                "VALUES (?, 'enables', ?, 'one')",
                (knowledge[0].id, node_id),
            )
            connection.execute(
                "INSERT INTO node_embeddings(node_id, embedding, model) "
                "VALUES (?, ?, ?)",
                (node_id, encode_vector(np.array([1.0, 0.0])), "fixture-embedding"),
            )

    database.run_transaction(seed)
    llm = fake_llm()
    llm.embed.side_effect = lambda _: np.array([1.0, 0.0])
    nodes = await Retriever(database, llm, RetrievalPolicy(0.8, 60)).search(
        "shared", topic="security"
    )
    assert len(nodes) == 6 and "extra-0" not in {node.id for node in nodes}
    ids = {node.id for node in nodes}
    assert all(
        edge.src in ids and edge.dst in ids for node in nodes for edge in node.edges
    )


async def test_retrieval_rejects_embedding_model_changes(database, knowledge):
    llm = fake_llm()
    llm.embedding_model.return_value = "different-model"
    with pytest.raises(ValueError, match="model mismatch"):
        await Retriever(database, llm, RetrievalPolicy(0.8, 60)).search(
            "seccomp", topic="security"
        )
    llm.embed.assert_not_called()
