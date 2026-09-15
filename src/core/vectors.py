"""Self-describing NumPy vectors; no pickle or inferred dimensions."""

from io import BytesIO

import numpy as np


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
