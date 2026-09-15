"""Read supplied Markdown articles without changing source files."""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import structlog
from ruamel.yaml import YAML

from src.core.time_utils import from_utc_iso, require_aware


@dataclass(frozen=True)
class Source:
    id: str
    path: Path
    title: str
    topic: str
    text: str
    origin_key: str | None = None
    url: str | None = None
    kind: str | None = None
    publisher: str | None = None
    given_by: str | None = None
    trust_prior: float | None = None
    published_at: datetime | None = None

    def __post_init__(self):
        for value in (self.id, self.title, self.topic, self.text):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Source ID, title, topic, and text must be nonempty")
        if self.published_at is not None:
            require_aware(self.published_at)
        if self.trust_prior is not None and not 0 <= self.trust_prior <= 1:
            raise ValueError("Source trust_prior must be between zero and one")

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def read_source(path: Path) -> Source:
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("Article must begin with YAML frontmatter")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("Unclosed YAML frontmatter")
    metadata = YAML(typ="safe").load("".join(lines[1:end]))
    if not isinstance(metadata, dict):
        raise ValueError("Frontmatter must be an object")
    published = metadata.get("published_at")
    if isinstance(published, datetime):
        published = require_aware(published)
    elif isinstance(published, str) and "T" in published:
        published = from_utc_iso(published)
    elif published is not None:
        # TODO(SOURCE-DATE): do not invent an instant for date-only metadata.
        structlog.get_logger("blogai.ingest").warning(
            "source_date_without_time", path=str(path), published_at=str(published)
        )
        published = None
    return Source(
        id=metadata.get("id"),
        path=path,
        title=metadata.get("title"),
        topic=metadata.get("topic"),
        text="".join(lines[end + 1 :]),
        published_at=published,
        **{
            key: metadata[key]
            for key in (
                "origin_key",
                "url",
                "kind",
                "publisher",
                "given_by",
                "trust_prior",
            )
            if key in metadata
        },
    )
