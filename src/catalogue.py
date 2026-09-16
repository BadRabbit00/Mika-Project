"""Load the supplied topic map and explicitly available Markdown sources."""

import json
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML

from src.ingest import read_source


@dataclass(frozen=True)
class Catalogue:
    start: str
    topics: dict
    sources: dict

    @classmethod
    def load(cls, directory):
        directory = Path(directory)
        data = YAML(typ="safe").load((directory / "topics.yaml").read_text())
        topics = {row["name"]: row for row in data["topics"]}
        if len(topics) != len(data["topics"]) or data["start"] not in topics:
            raise ValueError("Topic names and the starting topic must be unambiguous")
        if data["allocation"] != {
            "on_pass": {"core": 2, "adjacent": 4, "switch_topic": True},
            "on_fail": {"core": 4, "adjacent": 2, "switch_topic": False},
        }:
            raise ValueError("The catalogue must preserve the approved allocation")
        sources = {}
        for topic in topics.values():
            if set(topic["adjacent"]) - topics.keys():
                raise ValueError("Unknown adjacent topic")
            for identity in topic["articles"]:
                if Path(identity).name != identity:
                    raise ValueError("Invalid article identity")
                path = directory / (identity + ".md")
                if not path.exists():
                    continue
                source = read_source(path)
                if (
                    source.id != identity
                    or source.topic != topic["name"]
                    or not source.origin_key
                ):
                    raise ValueError("Article metadata conflicts with the catalogue")
                if identity in sources:
                    raise ValueError("An article appears in more than one topic")
                sources[identity] = source
        missing = sorted(set(topics[data["start"]]["articles"]) - sources.keys())
        if missing:
            # TODO(FIRST-TOPIC-ARTICLES): supply the six source files manually.
            raise ValueError("Missing first-topic articles: " + ", ".join(missing))
        return cls(data["start"], topics, sources)

    def install(self, database):
        database.run_transaction(
            lambda c: c.executemany(
                "INSERT INTO topics(name,status,adjacent) VALUES (?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET adjacent=excluded.adjacent",
                (
                    (row["name"], row["status"], json.dumps(row["adjacent"]))
                    for row in self.topics.values()
                ),
            )
        )
