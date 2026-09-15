"""Knowledge extraction contracts; quiz contracts are added after step 2 passes."""

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
    llm = fake_llm([json.dumps(claim()) + "\n"] * 3)
    extractor = Extractor(database, llm, ContextBuilder(Path("prompts")))
    reports = [
        await extractor.extract(source(source_id=str(index), origin=str(index)))
        for index in range(3)
    ]
    assert [report.accepted for report in reports] == [1, 1, 1]
    with database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM claims").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM nodes").fetchone()[0] == 2


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
