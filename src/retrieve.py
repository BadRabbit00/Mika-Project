"""Topic-scoped FTS5 and cosine retrieval with an explicit fusion policy."""

import asyncio
import math
from dataclasses import asdict, dataclass

import structlog

from src.core.db import Database
from src.core.settings import SettingsRegistry
from src.core.vectors import cosine, decode_vector
from src.extract import fts_query

log = structlog.get_logger("blogai.retrieve")


@dataclass(frozen=True)
class RetrievalPolicy:
    """Validated cutoff and equal-weight reciprocal-rank-fusion constant."""

    min_similarity: float
    rrf_k: float

    def __post_init__(self):
        for value in (self.min_similarity, self.rrf_k):
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("Retrieval parameters must be finite numbers")
        if not -1 <= self.min_similarity <= 1 or self.rrf_k < 0:
            raise ValueError("Invalid retrieval cutoff or rank-fusion constant")


@dataclass(frozen=True)
class RetrievedEdge:
    id: int
    src: str
    rel: str
    dst: str


@dataclass(frozen=True)
class RetrievedNode:
    id: str
    name: str
    summary: str | None
    edges: tuple[RetrievedEdge, ...]


class Retriever:
    def __init__(
        self,
        database: Database,
        llm,
        policy: RetrievalPolicy | None = None,
        *,
        settings=None,
    ):
        self.database, self.llm, self._policy = database, llm, policy
        self.settings = settings or SettingsRegistry.from_file("config/settings.yaml")

    @property
    def policy(self):
        return self._policy or RetrievalPolicy(
            self.settings.get("retrieval.min_similarity"),
            self.settings.get("retrieval.rrf_k"),
        )

    @property
    def top_k(self):
        return self.settings.get("retrieval.top_k")

    def _snapshot(self, query: str, topic: str):
        with self.database.connection() as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT n.id, n.name, n.summary, v.embedding, v.model FROM nodes n "
                "LEFT JOIN node_embeddings v ON v.node_id=n.id "
                "WHERE n.suspect=0 AND EXISTS ("
                "SELECT 1 FROM edges e JOIN sources s ON s.id=e.source_id "
                "WHERE s.topic=? AND (e.src=n.id OR e.dst=n.id)) ORDER BY n.id",
                (topic,),
            ).fetchall()
            nodes = {row["id"]: dict(row) for row in rows}
            lexical = (
                [
                    row[0]
                    for row in connection.execute(
                        "SELECT id FROM nodes_fts WHERE nodes_fts MATCH ? "
                        "ORDER BY rank, id",
                        (query,),
                    )
                    if row[0] in nodes
                ]
                if query
                else []
            )
            edges = [
                RetrievedEdge(**dict(row))
                for row in connection.execute(
                    "SELECT e.id, e.src, e.rel, e.dst FROM edges e "
                    "JOIN sources s ON s.id=e.source_id WHERE s.topic=? ORDER BY e.id",
                    (topic,),
                )
            ]
            connection.execute("COMMIT")
            return nodes, lexical, edges

    async def search(
        self, query: str, *, topic: str, threshold: float | None = None
    ) -> list[RetrievedNode]:
        policy, top_k = self.policy, self.top_k
        cutoff = policy.min_similarity if threshold is None else threshold
        if (
            type(cutoff) not in (int, float)
            or not math.isfinite(cutoff)
            or not -1 <= cutoff <= 1
        ):
            raise ValueError("Invalid retrieval cutoff")
        if not query.strip() or not topic.strip():
            return []
        nodes, lexical, edges = await asyncio.to_thread(
            self._snapshot, fts_query(query), topic
        )
        if not nodes:
            log.info("retrieval_empty", topic=topic, query=query)
            return []
        embedded = {
            key: node for key, node in nodes.items() if node["embedding"] is not None
        }
        similarities = {}
        if embedded:
            model = await self.llm.embedding_model()
            if any(node["model"] != model for node in embedded.values()):
                raise ValueError("Embedding model mismatch; reindexing is required")
            vector = await self.llm.embed(query)
            similarities = {
                key: cosine(vector, decode_vector(node["embedding"]))
                for key, node in embedded.items()
            }
        semantic = sorted(
            (key for key, score in similarities.items() if score >= cutoff),
            key=lambda key: (-similarities[key], key),
        )
        scores: dict[str, float] = {}
        for ranking in (lexical, semantic):
            for rank, key in enumerate(ranking, start=1):
                scores[key] = scores.get(key, 0.0) + 1.0 / (policy.rrf_k + rank)
        selected = sorted(scores, key=lambda key: (-scores[key], key))[:top_k]
        selected_ids = set(selected)
        induced = [
            edge
            for edge in edges
            if edge.src in selected_ids and edge.dst in selected_ids
        ]
        log.info(
            "nodes_retrieved",
            topic=topic,
            query=query,
            policy=asdict(policy),
            node_ids=selected,
            lexical=lexical,
            similarities=similarities,
            scores=scores,
        )
        return [
            RetrievedNode(
                key,
                nodes[key]["name"],
                nodes[key]["summary"],
                tuple(edge for edge in induced if key in (edge.src, edge.dst)),
            )
            for key in selected
        ]
