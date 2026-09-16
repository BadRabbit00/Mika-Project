"""Explicit invalidation and narrative exclusion without deleting public evidence."""

import structlog

from src.core.time_utils import now

log = structlog.get_logger("blogai.defects")
CATEGORIES = frozenset({"fact", "voice", "format", "repeat", "context"})


class SQLiteLineageStore:
    """Consume explicit provenance; never infer derivation from topic similarity."""

    def link(self, connection, post_id, *, node_ids=(), thread_ids=()):
        """Call inside the transaction that creates the derived records."""
        connection.executemany(
            "INSERT OR IGNORE INTO post_nodes(post_id,node_id) VALUES (?,?)",
            ((post_id, key) for key in node_ids),
        )
        connection.executemany(
            "INSERT OR IGNORE INTO post_threads(post_id,thread_id) VALUES (?,?)",
            ((post_id, key) for key in thread_ids),
        )

    def invalidate(self, connection, post_id, *, trace_id):
        connection.execute(
            "UPDATE threads SET status='stale' WHERE id IN "
            "(SELECT thread_id FROM post_threads WHERE post_id=?)",
            (post_id,),
        )
        connection.execute(
            "UPDATE nodes SET suspect=1 WHERE id IN "
            "(SELECT node_id FROM post_nodes WHERE post_id=?)",
            (post_id,),
        )
        connection.execute(
            "INSERT INTO curator_review(kind,subject,reason,opened_at) "
            "SELECT 'suspect_node',node_id,?,? FROM post_nodes WHERE post_id=?",
            (f"Invalidated post {post_id}; trace {trace_id}", now(), post_id),
        )
        log.info("defect_lineage_invalidated", post_id=post_id, trace_id=trace_id)


class Defects:
    def __init__(self, database, *, lineage=None):
        self.database = database
        self.lineage = lineage if lineage is not None else SQLiteLineageStore()

    def invalidate(self, post_id, *, category, reason, trace_id):
        category = {
            "факт": "fact",
            "голос": "voice",
            "формат": "format",
            "повтор": "repeat",
            "контекст": "context",
        }.get(category, category)
        if category not in CATEGORIES or not reason.strip() or not trace_id:
            raise ValueError("A category, reason, and trace are required")

        def save(connection):
            post = connection.execute(
                "SELECT * FROM posts WHERE id=?", (post_id,)
            ).fetchone()
            if post is None:
                raise ValueError("Unknown post")
            existing = connection.execute(
                "SELECT * FROM invalidated WHERE post_id=?", (post_id,)
            ).fetchone()
            if existing:
                if existing["category"] != category or existing["reason"] != reason:
                    raise ValueError("A different defect is already recorded")
                return dict(existing)
            connection.execute(
                "INSERT INTO invalidated(post_id, at, reason, category, trace_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (post_id, now(), reason, category, trace_id),
            )
            connection.execute(
                "UPDATE narrative SET excluded=1 WHERE post_id=?", (post_id,)
            )
            self.lineage.invalidate(connection, post_id, trace_id=trace_id)
            return dict(
                connection.execute(
                    "SELECT * FROM invalidated WHERE post_id=?", (post_id,)
                ).fetchone()
            )

        with structlog.contextvars.bound_contextvars(trace_id=trace_id):
            return self.database.run_transaction(save)

    def list(self, *, since=None):
        with self.database.connection() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM invalidated WHERE (? IS NULL OR at>=?) "
                    "ORDER BY at DESC",
                    (since, since),
                )
            ]
