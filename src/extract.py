"""Article chunks to validated, grounded graph updates in one transaction."""

import asyncio
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError

from src.core.context import ContextBuilder
from src.core.db import Database
from src.core.time_utils import now
from src.core.vectors import cosine, decode_vector, encode_vector
from src.ingest import Source

log = structlog.get_logger("blogai.extract")
ALLOWED_RELS = frozenset(
    {
        "is_a",
        "defends_against",
        "enables",
        "requires",
        "contradicts",
        "example_of",
        "part_of",
    }
)


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def fts_query(text: str) -> str:
    """Escape data as FTS terms; never accept FTS operators from model output."""
    words = re.findall(r"\w+", normalize(text))
    return " OR ".join('"' + word.replace('"', '""') + '"' for word in words)


def grounded(entity: str, text: str) -> bool:
    name = normalize(entity)
    return bool(
        name and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", normalize(text))
    )


class Claim(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    text: str
    src: str
    rel: str
    dst: str


@dataclass(frozen=True)
class Rejection:
    claim: object
    reason: str


def validate_claims(
    raw_claims: list[object], text: str
) -> tuple[list[Claim], list[Rejection]]:
    accepted, rejected = [], []
    for raw in raw_claims:
        try:
            item = Claim.model_validate(raw)
        except ValidationError:
            rejected.append(Rejection(raw, "schema"))
            continue
        reason = None
        if item.rel not in ALLOWED_RELS:
            reason = "rel"
        elif (
            not item.text.strip()
            or min(len(normalize(item.src)), len(normalize(item.dst))) < 2
        ):
            reason = "empty"
        elif normalize(item.src) == normalize(item.dst):
            reason = "self"
        elif not grounded(item.src, text) or not grounded(item.dst, text):
            reason = "ungrounded"
        if reason:
            rejected.append(Rejection(raw, reason))
        else:
            accepted.append(item)
    return accepted, rejected


def norm_hash(src: str, rel: str, dst: str) -> str:
    data = json.dumps(
        [normalize(src), rel, normalize(dst)], ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Chunk:
    text: str
    tokens: list[int]
    start: int
    stop: int


async def chunk_text(text: str, llm) -> list[Chunk]:
    """Use at most 2000 server tokens and exactly 200 overlapping tokens.

    Shrink a boundary if decoding it would split a UTF-8 character. No content
    is dropped: the next window starts 200 tokens before the previous end.
    """
    tokens = await llm.tokenize(text)
    chunks, start = [], 0
    while start < len(tokens):
        stop = min(start + 2000, len(tokens))
        while stop > start:
            try:
                content = await llm.detokenize(tokens[start:stop])
                if "\ufffd" in content and "\ufffd" not in text:
                    raise UnicodeError("Token boundary splits a Unicode character")
                if stop < len(tokens):
                    overlap = await llm.detokenize(tokens[stop - 200 : stop])
                    if "\ufffd" in overlap and "\ufffd" not in text:
                        raise UnicodeError("Overlap splits a Unicode character")
                if len(await llm.tokenize(content)) > 2000:
                    raise UnicodeError("Retokenized chunk exceeds its budget")
                break
            except UnicodeError:
                stop -= 1
        if stop <= start or (stop < len(tokens) and stop - start <= 200):
            raise ValueError("Cannot form a lossless chunk with 200-token overlap")
        chunks.append(Chunk(content, tokens[start:stop], start, stop))
        if stop == len(tokens):
            break
        start = stop - 200
    return chunks


@dataclass(frozen=True)
class ExtractionReport:
    source_id: str
    accepted: int
    rejected: int
    chunks: int
    trace_id: str


class Extractor:
    def __init__(
        self,
        database: Database,
        llm,
        context: ContextBuilder,
        *,
        grammar_dir: Path = Path("grammars"),
        max_output_tokens: int = 2048,
    ):
        if type(max_output_tokens) is not int or max_output_tokens <= 0:
            raise ValueError("A positive generation limit is required")
        self.database, self.llm, self.context = database, llm, context
        self.grammar = (Path(grammar_dir) / "claims.gbnf").read_text(encoding="utf-8")
        self.max_output_tokens = max_output_tokens

    def _snapshot(self, source: Source):
        with self.database.connection() as connection:
            existing = connection.execute(
                "SELECT content_hash FROM sources WHERE id = ?", (source.id,)
            ).fetchone()
            if existing is not None:
                if existing["content_hash"] != source.content_hash:
                    raise ValueError(
                        "Source ID already exists with different or untracked content"
                    )
                return None
            return [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM nodes WHERE suspect = 0 ORDER BY id"
                )
            ]

    async def extract(self, source: Source) -> ExtractionReport:
        trace_id = (
            structlog.contextvars.get_contextvars().get("trace_id") or uuid4().hex
        )
        with structlog.contextvars.bound_contextvars(trace_id=trace_id):
            names = await asyncio.to_thread(self._snapshot, source)
            if names is None:
                log.info("source_deduplicated", source_id=source.id)
                return ExtractionReport(source.id, 0, 0, 0, trace_id)
            chunks = await chunk_text(source.text, self.llm)
            accepted, rejected = [], []
            for chunk in chunks:
                request = await self.context.build_checked(
                    "extract",
                    llm=self.llm,
                    existing_node_names=names,
                    article_chunk=chunk.text,
                )
                output = await self.llm.generate(
                    request, grammar=self.grammar, max_tokens=self.max_output_tokens
                )
                raw = [json.loads(line) for line in output.split("\n") if line.strip()]
                if not raw:
                    raise ValueError("Claims grammar requires at least one claim")
                valid, invalid = validate_claims(raw, chunk.text)
                accepted.extend(valid)
                rejected.extend(invalid)
                for rejection in invalid:
                    log.warning(
                        "claim_rejected",
                        source_id=source.id,
                        chunk_start=chunk.start,
                        reason=rejection.reason,
                        claim=rejection.claim,
                    )
            model = await self.llm.embedding_model() if accepted else None
            vectors = {}
            for item in accepted:
                for entity in (item.src, item.dst):
                    key = normalize(entity)
                    if key not in vectors:
                        vectors[key] = await self.llm.embed(key)
            count, merged_self = await asyncio.to_thread(
                self.database.run_transaction,
                lambda connection: self._store(
                    connection, source, accepted, vectors, model
                ),
            )
            result = ExtractionReport(
                source.id, count, len(rejected) + merged_self, len(chunks), trace_id
            )
            log.info(
                "article_extracted",
                source_id=source.id,
                accepted=result.accepted,
                rejected=result.rejected,
                chunks=result.chunks,
            )
            return result

    def _store(self, connection, source, claims, vectors, model):
        existing = connection.execute(
            "SELECT content_hash FROM sources WHERE id = ?", (source.id,)
        ).fetchone()
        if existing is not None:
            if existing[0] != source.content_hash:
                raise ValueError("Concurrent source content conflict")
            return 0, 0
        connection.execute(
            """INSERT INTO sources(id, path, title, topic, origin_key, url, kind,
               publisher, given_by, trust_prior, published_at,
               ingested_at, content_hash, peer_reviewed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                source.id,
                str(source.path),
                source.title,
                source.topic,
                source.origin_key,
                source.url,
                source.kind,
                source.publisher,
                source.given_by,
                source.trust_prior,
                source.published_at,
                now(),
                source.content_hash,
                int(source.peer_reviewed),
            ),
        )
        connection.execute(
            "INSERT INTO topics(name, status) VALUES (?, 'pending') "
            "ON CONFLICT(name) DO NOTHING",
            (source.topic,),
        )
        if not claims:
            return 0, 0
        nodes = {
            row["id"]: dict(row)
            for row in connection.execute(
                "SELECT n.*, e.embedding, e.model FROM nodes n "
                "LEFT JOIN node_embeddings e ON n.id=e.node_id "
                "WHERE n.suspect=0 ORDER BY n.id"
            )
        }
        for node in nodes.values():
            if node["embedding"] is not None:
                if node["model"] != model:
                    raise ValueError("Embedding model mismatch; reindexing is required")
                node["vector"] = decode_vector(node["embedding"])

        def resolve(name):
            canonical = normalize(name)
            query = fts_query(name)
            candidates = (
                connection.execute(
                    "SELECT id FROM nodes_fts WHERE nodes_fts MATCH ? ORDER BY rank",
                    (query,),
                ).fetchall()
                if query
                else []
            )
            for row in candidates:
                if row[0] in nodes and normalize(nodes[row[0]]["name"]) == canonical:
                    return row[0]
            for node_id, node in nodes.items():
                if normalize(node["name"]) == canonical:
                    return node_id
            vector = vectors[canonical]
            matches = [
                (cosine(vector, node["vector"]), node_id)
                for node_id, node in nodes.items()
                if "vector" in node
            ]
            if matches:
                score, node_id = max(matches, key=lambda item: (item[0], item[1]))
                if score >= 0.85:
                    log.info(
                        "node_merged", name=name, node_id=node_id, similarity=score
                    )
                    return node_id
            slug = re.sub(r"[^\w-]+", "-", canonical).strip("-")
            node_id = slug + "-" + hashlib.sha256(canonical.encode()).hexdigest()[:12]
            nodes[node_id] = {
                "name": name.strip(),
                "vector": vector,
                "summary": None,
                "corrected_by": None,
                "new": True,
            }
            return node_id

        stored, merged_self = 0, 0
        for item in claims:
            src, dst = resolve(item.src), resolve(item.dst)
            if src == dst:
                merged_self += 1
                log.warning(
                    "claim_rejected", claim=item.model_dump(), reason="merged_self"
                )
                continue
            digest = norm_hash(nodes[src]["name"], item.rel, nodes[dst]["name"])
            if connection.execute(
                "SELECT 1 FROM claims WHERE source_id=? AND norm_hash=?",
                (source.id, digest),
            ).fetchone():
                continue
            for node_id in (src, dst):
                node = nodes[node_id]
                if node.pop("new", False):
                    connection.execute(
                        "INSERT INTO nodes(id, name, first_seen) VALUES (?, ?, ?)",
                        (node_id, node["name"], now()),
                    )
                    connection.execute(
                        "INSERT INTO node_embeddings(node_id, embedding, model) "
                        "VALUES (?, ?, ?)",
                        (node_id, encode_vector(node["vector"]), model),
                    )
            edge = connection.execute(
                "INSERT INTO edges(src, rel, dst, source_id) VALUES (?, ?, ?, ?)",
                (src, item.rel, dst, source.id),
            ).lastrowid
            connection.execute(
                "INSERT INTO claims(source_id, text, edge_id, norm_hash) "
                "VALUES (?, ?, ?, ?)",
                (source.id, item.text, edge, digest),
            )
            for node_id in (src, dst):
                node = nodes[node_id]
                statements = (node["summary"] or "").splitlines()
                if not node["corrected_by"] and item.text not in statements:
                    node["summary"] = "\n".join([*statements, item.text])
                    connection.execute(
                        "UPDATE nodes SET summary=? WHERE id=?",
                        (node["summary"], node_id),
                    )
            stored += 1
        return stored, merged_self
