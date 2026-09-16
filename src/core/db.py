"""SQLite schema, explicit transactions, bounded retries, and outbox storage.

All connections install UTC validation and a rejecting datetime adapter.
Database timestamp columns return strings: parse them with from_utc_iso.
Transaction callbacks must only perform database work because they may replay.
"""

import json
import math
import sqlite3
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog

from src.core.time_utils import from_utc_iso, now, to_utc_iso

log = structlog.get_logger("blogai.db")


def _utc(column: str) -> str:
    return f"{column} TEXT CHECK ({column} IS NULL OR is_utc_timestamp({column}) = 1)"


def _pad(column: str) -> str:
    return (
        f"{column} REAL CHECK ({column} IS NULL OR "
        f"({column} BETWEEN -1.0 AND 1.0 AND {column} = round({column}, 4)))"
    )


# Migration 1 preserves the documented tables, including sections 17 and 25.
# Append migrations; never renumber or rewrite an applied migration.
MIGRATIONS: tuple[tuple[str, ...], ...] = (
    (
        f"""CREATE TABLE sources (
            id TEXT PRIMARY KEY NOT NULL, path TEXT NOT NULL, title TEXT NOT NULL,
            url TEXT, origin_key TEXT, publisher TEXT, kind TEXT,
            {_utc("published_at")}, trust_prior REAL, given_by TEXT, topic TEXT,
            {_utc("ingested_at")}
        )""",
        f"""CREATE TABLE nodes (
            id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, kind TEXT,
            summary TEXT, confidence REAL DEFAULT 0.5,
            {_utc("first_seen")}, corrected_by TEXT
        )""",
        """CREATE TABLE edges (
            id INTEGER PRIMARY KEY, src TEXT REFERENCES nodes(id),
            rel TEXT NOT NULL CHECK (rel IN (
                'is_a', 'defends_against', 'enables', 'requires',
                'contradicts', 'example_of', 'part_of'
            )), dst TEXT REFERENCES nodes(id),
            source_id TEXT REFERENCES sources(id), confidence REAL DEFAULT 0.5
        )""",
        """CREATE TABLE claims (
            id INTEGER PRIMARY KEY, source_id TEXT REFERENCES sources(id),
            text TEXT NOT NULL, edge_id INTEGER REFERENCES edges(id), norm_hash TEXT
        )""",
        f"""CREATE TABLE questions (
            id INTEGER PRIMARY KEY, topic TEXT, text TEXT, {_utc("asked_at")},
            retrieved TEXT, answer TEXT, cited TEXT, verdict TEXT
        )""",
        f"""CREATE TABLE exams (
            id INTEGER PRIMARY KEY, topic TEXT, question TEXT, expected TEXT,
            key_facts TEXT, answer TEXT, verdict TEXT, comment TEXT, {_utc("at")}
        )""",
        """CREATE TABLE topics (
            name TEXT PRIMARY KEY NOT NULL, status TEXT, adjacent TEXT
        )""",
        f"""CREATE TABLE posts (
            id TEXT PRIMARY KEY NOT NULL, kind TEXT, state TEXT, text TEXT,
            {_utc("published_at")}, tg_message_id INT
        )""",
        "CREATE VIRTUAL TABLE nodes_fts USING fts5(id, name, summary)",
        # Migration 3 identifies the model; core/vectors.py defines the NPY codec.
        """CREATE TABLE node_embeddings (
            node_id TEXT PRIMARY KEY NOT NULL REFERENCES nodes(id),
            embedding BLOB NOT NULL CHECK (typeof(embedding) = 'blob')
        )""",
        f"""CREATE TABLE life_journal (
            id INTEGER PRIMARY KEY, {_utc("at")}, slot TEXT, entity TEXT, text TEXT,
            continues INTEGER REFERENCES life_journal(id)
        )""",
        f"""CREATE TABLE life_state (
            key TEXT PRIMARY KEY NOT NULL, value TEXT, {_utc("updated_at")}
        )""",
        f"""CREATE TABLE curator_log (
            id INTEGER PRIMARY KEY, {_utc("at")}, kind TEXT, subject TEXT,
            claim TEXT, reasoning TEXT, exam_id INTEGER REFERENCES exams(id)
        )""",
        # The historical primary key is split into call and trace IDs in version 6.
        f"""CREATE TABLE runs (
            trace_id TEXT PRIMARY KEY NOT NULL, {_utc("at")}, actor TEXT,
            profile TEXT, model TEXT, params_json TEXT, system TEXT, user TEXT,
            thought TEXT, output TEXT, tokens_in INT, tokens_out INT,
            duration_ms INT, cost_usd REAL, status TEXT, error TEXT
        )""",
        "CREATE INDEX runs_at ON runs(at)",
        f"""CREATE TABLE narrative (
            id INTEGER PRIMARY KEY, {_utc("at")}, kind TEXT, gist TEXT, topic TEXT,
            mood TEXT, trace_id TEXT, post_id TEXT REFERENCES posts(id)
        )""",
        f"""CREATE TABLE threads (
            id INTEGER PRIMARY KEY, {_utc("opened_at")}, kind TEXT, text TEXT,
            topic TEXT, status TEXT, closed_by TEXT REFERENCES posts(id), priority INT
        )""",
        f"""CREATE TABLE dialog (
            id INTEGER PRIMARY KEY, chat_id INT, topic_id INT, {_utc("at")},
            role TEXT, text TEXT, mode TEXT, cited TEXT, trace_id TEXT
        )""",
        f"""CREATE TABLE people_facts (
            id INTEGER PRIMARY KEY, person_id TEXT, fact TEXT, kind TEXT,
            source TEXT, {_utc("at")}, confirmed BOOLEAN DEFAULT 0
                CHECK (confirmed IN (0, 1))
        )""",
        f"""CREATE TABLE invalidated (
            post_id TEXT PRIMARY KEY NOT NULL REFERENCES posts(id), {_utc("at")},
            reason TEXT, category TEXT, author_note TEXT, fix_commit TEXT,
            pinned_id INT, trace_id TEXT
        )""",
        f"""CREATE TABLE mood (
            {_utc("at")} PRIMARY KEY NOT NULL,
            {_pad("p")}, {_pad("a")}, {_pad("d")},
            {_pad("baseline_p")}, {_pad("baseline_a")}, {_pad("baseline_d")},
            octant TEXT, last_event TEXT, trigger_id TEXT, sleep_debt REAL
        )""",
        f"""CREATE TABLE mood_queue (
            id INTEGER PRIMARY KEY, {_utc("fire_at")}, event_id TEXT,
            delta_json TEXT, trigger_id TEXT
        )""",
        f"""CREATE TABLE npc (
            id TEXT PRIMARY KEY NOT NULL, closeness REAL, {_utc("last_contact")},
            state TEXT, {_utc("state_until")}, arc TEXT REFERENCES arcs(id)
        )""",
        f"""CREATE TABLE arcs (
            id TEXT PRIMARY KEY NOT NULL, kind TEXT, title TEXT, stage INT,
            stages INT, {_utc("started")}, {_utc("deadline")},
            state TEXT, mood_bias TEXT
        )""",
        f"""CREATE TABLE sessions (
            id TEXT PRIMARY KEY NOT NULL, channel TEXT,
            {_utc("opened_at")}, {_utc("closed_at")}, turns INT, tokens_used INT,
            summary TEXT, mood_start TEXT, mood_end TEXT
        )""",
        f"""CREATE TABLE session_turns (
            id INTEGER PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
            idx INT, role TEXT, text TEXT, mode TEXT, cited TEXT,
            {_utc("at")}, trace_id TEXT
        )""",
        f"""CREATE TABLE outbox (
            id INTEGER PRIMARY KEY, idem_key TEXT NOT NULL UNIQUE,
            channel TEXT, payload TEXT, attempts INT NOT NULL DEFAULT 0
                CHECK (typeof(attempts) = 'integer' AND attempts >= 0),
            {_utc("next_try_at")}, {_utc("sent_at")}, tg_message_id INT
        )""",
    ),
    (
        "ALTER TABLE sources ADD COLUMN complexity INT",
        "ALTER TABLE sources ADD COLUMN complexity_why TEXT",
        "ALTER TABLE sources ADD COLUMN new_terms TEXT",
        "ALTER TABLE narrative ADD COLUMN excluded BOOLEAN NOT NULL DEFAULT 0 "
        "CHECK (excluded IN (0, 1))",
        "ALTER TABLE nodes ADD COLUMN suspect BOOLEAN NOT NULL DEFAULT 0 "
        "CHECK (suspect IN (0, 1))",
        """CREATE TRIGGER nodes_fts_insert AFTER INSERT ON nodes BEGIN
            INSERT INTO nodes_fts(id, name, summary)
            VALUES (NEW.id, NEW.name, NEW.summary);
        END""",
        """CREATE TRIGGER nodes_fts_update AFTER UPDATE ON nodes BEGIN
            DELETE FROM nodes_fts WHERE id = OLD.id;
            INSERT INTO nodes_fts(id, name, summary)
            VALUES (NEW.id, NEW.name, NEW.summary);
        END""",
        """CREATE TRIGGER nodes_fts_delete AFTER DELETE ON nodes BEGIN
            DELETE FROM nodes_fts WHERE id = OLD.id;
        END""",
        "DELETE FROM nodes_fts",
        "INSERT INTO nodes_fts(id, name, summary) SELECT id, name, summary FROM nodes",
        """CREATE TRIGGER mood_no_update BEFORE UPDATE ON mood BEGIN
            SELECT RAISE(ABORT, 'Mood history is append-only');
        END""",
        "CREATE INDEX edges_src ON edges(src)",
        "CREATE INDEX edges_dst ON edges(dst)",
        "CREATE INDEX edges_source ON edges(source_id)",
        "CREATE INDEX claims_norm_hash ON claims(norm_hash)",
        "CREATE INDEX claims_source ON claims(source_id)",
        "CREATE INDEX narrative_at ON narrative(at)",
        "CREATE INDEX threads_status_priority ON threads(status, priority)",
        "CREATE INDEX dialog_chat_topic_at ON dialog(chat_id, topic_id, at)",
        "CREATE INDEX session_turns_session_idx ON session_turns(session_id, idx)",
        "CREATE INDEX outbox_pending ON outbox(next_try_at) WHERE sent_at IS NULL",
        "CREATE INDEX mood_queue_fire_at ON mood_queue(fire_at)",
    ),
    (
        "ALTER TABLE sources ADD COLUMN content_hash TEXT",
        "ALTER TABLE node_embeddings ADD COLUMN model TEXT",
        "CREATE UNIQUE INDEX claims_source_hash ON claims(source_id, norm_hash)",
    ),
    (
        """CREATE TRIGGER mood_sleep_debt_validate BEFORE INSERT ON mood
        WHEN NEW.sleep_debt IS NOT NULL AND (
            typeof(NEW.sleep_debt) NOT IN ('real', 'integer') OR
            NEW.sleep_debt < 0 OR NEW.sleep_debt >= 1e999 OR
            NEW.sleep_debt != round(NEW.sleep_debt, 4))
        BEGIN SELECT RAISE(ABORT, 'Invalid or unrounded sleep debt'); END""",
    ),
    (
        f"""CREATE TABLE settings_overrides (
            key TEXT PRIMARY KEY NOT NULL,
            value_json TEXT NOT NULL CHECK (json_valid(value_json)),
            previous_json TEXT NOT NULL CHECK (json_valid(previous_json)),
            {_utc("updated_at")} NOT NULL, trace_id TEXT NOT NULL
        )""",
        """CREATE TABLE post_nodes (
            post_id TEXT NOT NULL REFERENCES posts(id),
            node_id TEXT NOT NULL REFERENCES nodes(id),
            PRIMARY KEY (post_id, node_id)
        )""",
        """CREATE TABLE post_threads (
            post_id TEXT NOT NULL REFERENCES posts(id),
            thread_id INTEGER NOT NULL REFERENCES threads(id),
            PRIMARY KEY (post_id, thread_id)
        )""",
    ),
    (
        "ALTER TABLE runs RENAME COLUMN trace_id TO call_id",
        "ALTER TABLE runs ADD COLUMN trace_id TEXT",
        """UPDATE runs SET trace_id = CASE WHEN json_valid(params_json)
            THEN COALESCE(json_extract(params_json, '$.trace_id'), call_id)
            ELSE call_id END""",
        "CREATE INDEX runs_trace ON runs(trace_id)",
    ),
    (
        f"""CREATE TABLE learner_state (
            id TEXT PRIMARY KEY CHECK (id='learner'),
            state_json TEXT NOT NULL CHECK (json_valid(state_json)),
            {_utc("paused_until")}, {_utc("generation_after")}
        )""",
        f"""CREATE TABLE learning_events (
            id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, {_utc("at")} NOT NULL,
            event_json TEXT NOT NULL CHECK (json_valid(event_json)),
            state_json TEXT NOT NULL CHECK (json_valid(state_json))
        )""",
        f"""CREATE TABLE learning_actions (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES learning_events(id),
            predecessor TEXT REFERENCES learning_actions(id),
            action_json TEXT NOT NULL CHECK (json_valid(action_json)),
            status TEXT NOT NULL CHECK (status IN
                ('pending','running','waiting','completed','failed','uncertain')),
            attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts>=0),
            {_utc("due_at")} NOT NULL, {_utc("completed_at")},
            result_event TEXT CHECK (result_event IS NULL OR json_valid(result_event)),
            error TEXT
        )""",
        "CREATE INDEX learning_actions_due ON learning_actions(status,due_at)",
        f"""CREATE TABLE activity_reservations (
            action_id TEXT PRIMARY KEY, session_key TEXT NOT NULL,
            {_utc("at")} NOT NULL
        )""",
    ),
    (
        f"""CREATE TABLE curator_review (
            id INTEGER PRIMARY KEY, kind TEXT NOT NULL
                CHECK (kind IN ('suspect_node','correction','trust')),
            subject TEXT NOT NULL, reason TEXT NOT NULL,
            {_utc("opened_at")} NOT NULL, {_utc("resolved_at")},
            verdict TEXT, exam_id TEXT
        )""",
        f"""CREATE TABLE diary_comments (
            id INTEGER PRIMARY KEY, tg_message_id INTEGER NOT NULL,
            post_id TEXT REFERENCES posts(id), author_id INTEGER NOT NULL,
            text TEXT NOT NULL, {_utc("at")} NOT NULL,
            surfaced INTEGER NOT NULL DEFAULT 0 CHECK (surfaced IN (0,1))
        )""",
        f"""CREATE TABLE sleep_log (
            night TEXT PRIMARY KEY NOT NULL CHECK (is_calendar_date(night)),
            {_utc("planned_bedtime")} NOT NULL,
            {_utc("actual_bedtime")} NOT NULL, {_utc("wake_at")} NOT NULL,
            wake_reason TEXT NOT NULL,
            hours REAL NOT NULL CHECK (hours>=0 AND hours<1e999
                AND hours=round(hours,4)),
            debt_after REAL NOT NULL CHECK (debt_after>=0 AND debt_after<1e999
                AND debt_after=round(debt_after,4)),
            debt_applied INTEGER NOT NULL DEFAULT 0 CHECK (debt_applied IN (0,1))
        )""",
    ),
    (
        # Preserve old instants for audit; their original calendar dates are unknown.
        "ALTER TABLE sources RENAME COLUMN published_at TO legacy_published_at",
        "ALTER TABLE sources ADD COLUMN published_at TEXT "
        "CHECK (published_at IS NULL OR is_calendar_date(published_at))",
        "ALTER TABLE sources ADD COLUMN peer_reviewed INTEGER NOT NULL DEFAULT 0 "
        "CHECK (peer_reviewed IN (0,1))",
        f"""CREATE TABLE exam_runs (
            id TEXT PRIMARY KEY NOT NULL, topic TEXT NOT NULL,
            {_utc("at")} NOT NULL, verdict TEXT, trace_id TEXT NOT NULL
        )""",
        "ALTER TABLE exams ADD COLUMN exam_run_id TEXT REFERENCES exam_runs(id)",
        "ALTER TABLE threads ADD COLUMN channel TEXT NOT NULL DEFAULT 'public' "
        "CHECK (channel IN ('public','dm'))",
        "ALTER TABLE posts ADD COLUMN context_snapshot TEXT "
        "CHECK (context_snapshot IS NULL OR json_valid(context_snapshot))",
        *tuple(
            f"""CREATE TRIGGER nodes_correction_{operation.split()[0].lower()}
                BEFORE {operation} ON nodes
                WHEN NEW.corrected_by IS NOT NULL AND (
                    instr(NEW.corrected_by, ':')<1 OR
                    substr(NEW.corrected_by, 1, instr(NEW.corrected_by, ':')-1)
                        NOT IN ('exam','curator','human') OR
                    length(substr(NEW.corrected_by, instr(NEW.corrected_by, ':')+1))=0)
                BEGIN SELECT RAISE(ABORT, 'Invalid correction identity'); END"""
            for operation in ("INSERT", "UPDATE OF corrected_by")
        ),
    ),
)
SCHEMA_VERSION = len(MIGRATIONS)


def _is_utc_timestamp(value: Any) -> int:
    try:
        from_utc_iso(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return 1


def _is_calendar_date(value: Any) -> int:
    try:
        return int(
            isinstance(value, str) and date.fromisoformat(value).isoformat() == value
        )
    except ValueError:
        return 0


# Python's default sqlite timestamp adapter accepts naive values. Replace it
# explicitly; do not enable the default converter that drops timezone offsets.
sqlite3.register_adapter(datetime, to_utc_iso)
sqlite3.register_adapter(date, date.isoformat)


def _is_locked(error: sqlite3.OperationalError) -> bool:
    code = getattr(error, "sqlite_errorcode", None)
    if code is not None:
        return code & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    return str(error).lower() in {"database is locked", "database table is locked"}


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _install_audit_triggers(connection: sqlite3.Connection) -> None:
    """Record row diffs on this connection, excluding FTS implementation tables.

    Diffs describe attempted writes and share the transaction outcome's ID.
    TEMP triggers leave the on-disk schema independent of the logging callback.
    """

    def row_change(table, operation, before, after):
        log.debug(
            "db_row_change",
            table=table,
            operation=operation,
            before=None if before is None else json.loads(before),
            after=None if after is None else json.loads(after),
            outcome="attempted",
        )

    connection.create_function("log_row_change", 4, row_change)
    tables = connection.execute("PRAGMA table_list").fetchall()
    for table in tables:
        name = table["name"]
        if (
            table["schema"] != "main"
            or table["type"] != "table"
            or name.startswith("sqlite_")
        ):
            continue
        columns = connection.execute(
            f"PRAGMA table_info({_identifier(name)})"
        ).fetchall()

        def row_json(prefix: str, columns: list[sqlite3.Row]) -> str:
            entries = []
            for column in columns:
                field = f"{prefix}.{_identifier(column['name'])}"
                value = (
                    f"CASE WHEN typeof({field}) = 'blob' "
                    f"THEN json_object('blob_hex', hex({field})) ELSE {field} END"
                )
                entries.extend((_literal(column["name"]), value))
            return "json_object(" + ", ".join(entries) + ")"

        for operation in ("insert", "update", "delete"):
            before = "NULL" if operation == "insert" else row_json("OLD", columns)
            after = "NULL" if operation == "delete" else row_json("NEW", columns)
            trigger = _identifier(f"audit_{name}_{operation}")
            connection.execute(
                f"CREATE TEMP TRIGGER {trigger} AFTER {operation.upper()} "
                f"ON main.{_identifier(name)} BEGIN "
                f"SELECT log_row_change({_literal(name)}, {_literal(operation)}, "
                f"{before}, {after}); END"
            )


class Database:
    """Open one WAL connection per operation; share paths, never connections."""

    def __init__(self, path: str | Path):
        if str(path) in ("", ":memory:"):
            raise ValueError("A file-backed SQLite path is required for WAL")
        self.path = Path(path)

    def _retry[T](self, operation: Callable[[], T]) -> T:
        for retry in range(6):
            try:
                return operation()
            except sqlite3.OperationalError as error:
                if not _is_locked(error):
                    raise
                if retry == 5:
                    log.exception("db_lock_retries_exhausted", database=str(self.path))
                    raise
                log.warning("db_locked_retry", retry=retry + 1, delay_seconds=0.2)
                time.sleep(0.2)
        raise AssertionError("Unreachable retry state")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Open, configure, and close a connection; never implicitly commit."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=0, autocommit=True)
        try:
            connection.row_factory = sqlite3.Row
            connection.create_function(
                "is_utc_timestamp", 1, _is_utc_timestamp, deterministic=True
            )
            connection.create_function(
                "is_calendar_date", 1, _is_calendar_date, deterministic=True
            )
            mode = self._retry(
                lambda: connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            )
            if mode != "wal":
                raise RuntimeError(f"SQLite did not enable WAL: {mode}")
            connection.execute("PRAGMA foreign_keys=ON")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise RuntimeError("SQLite did not enable foreign keys")
            _install_audit_triggers(connection)
            log.debug(
                "db_connection_opened", database=str(self.path), journal_mode=mode
            )
            yield connection
        finally:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
                log.warning("db_unfinished_transaction_rolled_back")
            connection.close()

    @contextmanager
    def _transaction(
        self, connection: sqlite3.Connection, *, retry_boundaries: bool
    ) -> Iterator[sqlite3.Connection]:
        if connection.in_transaction:
            raise RuntimeError("Nested transactions are not supported")

        def execute(statement: str) -> None:
            if retry_boundaries:
                self._retry(lambda: connection.execute(statement))
            else:
                connection.execute(statement)

        with structlog.contextvars.bound_contextvars(transaction_id=uuid4().hex):
            execute("BEGIN IMMEDIATE")
            log.debug("db_transaction_started")
            try:
                yield connection
                if not connection.in_transaction:
                    raise RuntimeError(
                        "The operation ended its transaction prematurely"
                    )
                execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                log.exception("db_transaction_rolled_back")
                raise
            else:
                log.debug("db_transaction_committed")

    @contextmanager
    def transaction(
        self, connection: sqlite3.Connection
    ) -> Iterator[sqlite3.Connection]:
        """Retry BEGIN/COMMIT locks; a with-body cannot be replayed automatically.

        Body exceptions roll back and propagate. Use run_transaction for whole
        transaction retries. Do not commit, roll back, or nest inside the body.
        """
        with self._transaction(connection, retry_boundaries=True):
            yield connection

    def run_transaction[T](self, operation: Callable[[sqlite3.Connection], T]) -> T:
        """Replay database-only work after rollback, with five 200 ms retries."""
        with self.connection() as connection:

            def attempt() -> T:
                with self._transaction(connection, retry_boundaries=False):
                    return operation(connection)

            return self._retry(attempt)

    def initialize(self) -> int:
        """Create or upgrade the database to the current schema version."""
        return self.migrate()

    def migrate(self, *, target_version: int | None = None) -> int:
        """Apply ordered migrations and user_version in one atomic transaction."""
        target = len(MIGRATIONS) if target_version is None else target_version
        if (
            isinstance(target, bool)
            or not isinstance(target, int)
            or not 1 <= target <= len(MIGRATIONS)
        ):
            raise ValueError("Unsupported target schema version")

        def upgrade(connection: sqlite3.Connection) -> int:
            current = connection.execute("PRAGMA user_version").fetchone()[0]
            if current > len(MIGRATIONS):
                raise ValueError("Database schema is newer than this application")
            if current > target:
                raise ValueError("Schema downgrade is not supported")
            if (
                current == 0
                and connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
                ).fetchone()
            ):
                raise ValueError("Cannot adopt an unversioned existing schema")
            for version in range(current + 1, target + 1):
                for statement in MIGRATIONS[version - 1]:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {version}")
                log.info("db_migration_applied", version=version)
            return target

        version = self.run_transaction(upgrade)
        log.info("db_initialized", database=str(self.path), schema_version=version)
        return version


def _require_transaction(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        raise RuntimeError("An active transaction is required")


def make_idem_key(post_id: str, channel: str) -> str:
    """Encode the pair without delimiter collisions or lossy hashing."""
    for value in (post_id, channel):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("post_id and channel must be nonempty strings")
    return json.dumps([post_id, channel], ensure_ascii=False, separators=(",", ":"))


def enqueue_outbox(
    connection: sqlite3.Connection,
    *,
    post_id: str,
    channel: str,
    payload: Mapping[str, Any],
    next_try_at: datetime | None = None,
) -> int:
    """Insert intent in the caller's transaction; preserve an existing row.

    Reusing a key with a changed payload is an error. Duplicate enqueues leave
    retry and delivery metadata untouched. No remote delivery occurs here.
    TODO(OUTBOX-DELIVERY): resolve uncertain remote sends in delivery step 8.
    """
    _require_transaction(connection)
    key = make_idem_key(post_id, channel)
    if not isinstance(payload, Mapping):
        raise TypeError("Outbox payload must be a JSON object")
    serialized = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    at = to_utc_iso(now() if next_try_at is None else next_try_at)
    inserted = connection.execute(
        """INSERT INTO outbox(idem_key, channel, payload, next_try_at)
           VALUES (?, ?, ?, ?) ON CONFLICT(idem_key) DO NOTHING RETURNING id""",
        (key, channel, serialized, at),
    ).fetchone()
    if inserted is not None:
        log.info(
            "outbox_enqueued", outbox_id=inserted[0], idem_key=key, channel=channel
        )
        return inserted[0]
    existing = connection.execute(
        "SELECT id, channel, payload FROM outbox WHERE idem_key = ?", (key,)
    ).fetchone()
    if existing["payload"] != serialized or existing["channel"] != channel:
        raise ValueError("Idempotency key already has a different payload or channel")
    log.info("outbox_deduplicated", outbox_id=existing["id"], idem_key=key)
    return existing["id"]


def insert_mood(
    connection: sqlite3.Connection,
    *,
    at: datetime,
    p: float,
    a: float,
    d: float,
    baseline_p: float,
    baseline_a: float,
    baseline_d: float,
    octant: str | None = None,
    last_event: str | None = None,
    trigger_id: str | None = None,
    sleep_debt: float | None = None,
) -> None:
    """Append a validated snapshot with all numeric state rounded to four places."""
    _require_transaction(connection)
    values = (p, a, d, baseline_p, baseline_a, baseline_d)
    if any(
        isinstance(value, bool) or not math.isfinite(value) or not -1 <= value <= 1
        for value in values
    ):
        raise ValueError("PAD values must be finite numbers between -1 and 1")
    rounded = tuple(round(value, 4) for value in values)
    if sleep_debt is not None:
        if (
            isinstance(sleep_debt, bool)
            or not math.isfinite(sleep_debt)
            or sleep_debt < 0
        ):
            raise ValueError("Sleep debt must be finite and nonnegative")
        sleep_debt = round(sleep_debt, 4)
    timestamp = to_utc_iso(at)
    connection.execute(
        """INSERT INTO mood(at, p, a, d, baseline_p, baseline_a, baseline_d,
                           octant, last_event, trigger_id, sleep_debt)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (timestamp, *rounded, octant, last_event, trigger_id, sleep_debt),
    )
    log.info(
        "mood_snapshot_stored",
        snapshot_at=timestamp,
        p=rounded[0],
        a=rounded[1],
        d=rounded[2],
    )
