"""Separate source reliability from independent corroboration of graph claims."""

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ruamel.yaml import YAML


@dataclass(frozen=True)
class TrustResult:
    reliability: float | None
    independent: int
    consensus: float
    score: float | None


class Trust:
    def __init__(self, database, config):
        self.database, self.config = database, config

    @classmethod
    def from_config(cls, database, path=Path("config/reliability.yaml")):
        return cls(database, YAML(typ="safe").load(Path(path).read_text()))

    def reliability(self, source, *, at: date) -> float | None:
        if type(at) is not date:
            raise ValueError("Reliability requires a calendar date")
        if source["trust_prior"] is not None:
            return source["trust_prior"]
        value = self.config["base_by_kind"].get(source["kind"])
        if value is None:
            return None
        value += self.config["peer_reviewed_bonus"] * source["peer_reviewed"]
        value += self.config["known_publisher_bonus"] * (
            source["publisher"] in self.config["known_publishers"]
        )
        if source["published_at"]:
            published = date.fromisoformat(source["published_at"])
            years = max(
                0,
                at.year
                - published.year
                - ((at.month, at.day) < (published.month, published.day)),
            )
            value = max(
                self.config["age_floor"],
                value + years * self.config["age_penalty_per_year"],
            )
        lower, upper = self.config["clamp"]
        return max(lower, min(upper, value))

    def claim(self, digest: str, *, source_id: str, at: date) -> TrustResult:
        with self.database.connection() as c:
            c.execute("BEGIN")
            source = c.execute(
                "SELECT * FROM sources WHERE id=?", (source_id,)
            ).fetchone()
            if source is None:
                raise ValueError("Unknown source")
            independent = c.execute(
                "SELECT count(DISTINCT s.origin_key) FROM claims c "
                "JOIN sources s ON s.id=c.source_id WHERE c.norm_hash=?",
                (digest,),
            ).fetchone()[0]
            source_count = c.execute(
                "SELECT count(*) FROM sources WHERE topic=?", (source["topic"],)
            ).fetchone()[0]
            c.execute("COMMIT")
        reliability = self.reliability(source, at=at)
        consensus = 1 - math.exp(-0.7 * independent)
        score = (
            None
            if source_count < 2 or reliability is None
            else (0.6 * reliability + 0.4 * consensus)
        )
        return TrustResult(reliability, independent, consensus, score)
