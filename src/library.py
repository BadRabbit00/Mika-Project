"""Validate Telegram Markdown uploads before atomically admitting library files."""

import os
import re
import tempfile
from dataclasses import replace
from pathlib import Path

import structlog

from src.ingest import read_source

log = structlog.get_logger("blogai.library")


class LibraryInbox:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def accept(self, filename: str, content: bytes, *, trace_id: str):
        if (
            not filename
            or Path(filename).name != filename
            or "\\" in filename
            or not filename.lower().endswith(".md")
        ):
            raise ValueError("Invalid Markdown filename")
        text = content.decode("utf-8")
        self.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".md", dir=self.directory, encoding="utf-8"
        ) as handle:
            handle.write(text)
            handle.flush()
            source = read_source(Path(handle.name))
            if not source.origin_key or not source.origin_key.strip():
                raise ValueError("Library uploads require origin_key")
            if re.fullmatch(r"[\w][\w.-]*", source.id) is None:
                raise ValueError("Source ID cannot be used as a library filename")
            target = self.directory / f"{source.id}.md"
            try:
                os.link(handle.name, target)
            except FileExistsError:
                if target.is_symlink() or target.read_bytes() != content:
                    raise ValueError(
                        "Article content changed under an existing ID"
                    ) from None
        result = replace(source, path=target)
        log.info(
            "library_article_accepted",
            trace_id=trace_id,
            source_id=source.id,
            topic=source.topic,
            path=str(target),
        )
        return result
