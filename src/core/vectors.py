"""Self-describing NumPy vectors; no pickle or inferred dimensions."""

import asyncio
from io import BytesIO

import numpy as np
import structlog


def validate_vector(value: np.ndarray) -> np.ndarray:
    vector = np.asarray(value, dtype=np.float64)
    if vector.ndim != 1 or not vector.size or not np.isfinite(vector).all():
        raise ValueError("Expected a finite nonzero vector")
    with np.errstate(over="ignore", under="ignore"):
        magnitude = np.linalg.norm(vector)
    if not np.isfinite(magnitude) or magnitude == 0:
        raise ValueError("Expected a finite nonzero vector norm")
    return vector


def encode_vector(value: np.ndarray) -> bytes:
    stream = BytesIO()
    np.save(stream, validate_vector(value), allow_pickle=False)
    return stream.getvalue()


def decode_vector(blob: bytes) -> np.ndarray:
    stream = BytesIO(blob)
    value = np.load(stream, allow_pickle=False)
    if stream.read():
        raise ValueError("Unexpected bytes after the embedding")
    return validate_vector(value)


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    left, right = validate_vector(left), validate_vector(right)
    if left.shape != right.shape:
        raise ValueError("Embedding dimensions do not match")
    return float(np.dot(left / np.linalg.norm(left), right / np.linalg.norm(right)))


async def reindex(database, llm) -> int:
    """Build a complete replacement off-transaction, then compare and swap it."""

    def snapshot():
        with database.connection() as c:
            return [
                tuple(row) for row in c.execute("SELECT id,name FROM nodes ORDER BY id")
            ]

    before = await asyncio.to_thread(snapshot)
    model = await llm.embedding_model()
    vectors = [
        (node_id, encode_vector(await llm.embed(name)), model)
        for node_id, name in before
    ]

    def commit(c):
        current = [
            tuple(row) for row in c.execute("SELECT id,name FROM nodes ORDER BY id")
        ]
        if current != before:
            raise ValueError("Graph changed during reindex; retry the operation")
        c.execute("DELETE FROM node_embeddings")
        c.executemany(
            "INSERT INTO node_embeddings(node_id,embedding,model) VALUES (?,?,?)",
            vectors,
        )
        return len(vectors)

    count = await asyncio.to_thread(database.run_transaction, commit)
    structlog.get_logger("blogai.vectors").info(
        "graph_reindexed", nodes=count, model=model
    )
    return count
