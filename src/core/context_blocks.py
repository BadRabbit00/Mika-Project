"""Whitelisted read-only memory blocks; no model instructions live here."""

from datetime import timedelta
from functools import wraps

from src.core.time_utils import from_utc_iso, to_utc_iso

BLOCKS = {}


def block(name):
    def register(function):
        @wraps(function)
        def resolved(ctx):
            return ctx[name] if name in ctx else function(ctx)

        BLOCKS[name] = resolved
        return resolved

    return register


def register_value_blocks(names):
    for name in names:
        if name not in BLOCKS:

            def read(ctx, key=name):
                return ctx[key]

            BLOCKS[name] = read


def _rows(ctx, sql, parameters):
    if ctx["connection"] is None:
        return []
    return [dict(row) for row in ctx["connection"].execute(sql, parameters)]


@block("narrative_recent")
def narrative_recent(ctx):
    return _narrative(ctx, False, 12)


@block("narrative_offtop")
def narrative_offtop(ctx):
    return _narrative(ctx, True, 5)


def _narrative(ctx, offtop, limit):
    return _rows(
        ctx,
        "SELECT n.kind, n.gist, n.at FROM narrative n WHERE n.excluded=0 "
        "AND n.at<=? AND ((?=1 AND n.kind IN ('offtop','daily','situation')) OR "
        "(?=0 AND n.kind IN ('found','impression','struggle',"
        "'insight','summary','correction'))) "
        "AND NOT EXISTS (SELECT 1 FROM invalidated i WHERE i.post_id=n.post_id) "
        "ORDER BY n.at DESC, n.id DESC LIMIT ?",
        (to_utc_iso(ctx["day"].at), offtop, offtop, limit),
    )


@block("open_threads")
def open_threads(ctx):
    day, offtop = ctx["day"], ctx["offtop"]
    return _rows(
        ctx,
        "SELECT kind, text, topic FROM threads WHERE status='open' AND opened_at<=? "
        "AND opened_at>=? AND ((?=1 AND kind='life') OR "
        "(?=0 AND kind!='life' AND topic=?)) ORDER BY priority DESC, opened_at, id",
        (
            to_utc_iso(day.at),
            to_utc_iso(day.at - timedelta(days=5)),
            offtop,
            offtop,
            ctx.get("topic"),
        ),
    )


@block("recent_slots")
def recent_slots(ctx):
    rows = _rows(
        ctx,
        "SELECT slot, at FROM life_journal WHERE at<=? "
        "ORDER BY at DESC, id DESC LIMIT 4",
        (to_utc_iso(ctx["day"].at),),
    )
    return [
        {"slot": row["slot"], "at": from_utc_iso(row["at"]).isoformat()} for row in rows
    ]


@block("subgraph")
def subgraph(ctx):
    nodes = _rows(
        ctx,
        "SELECT n.id, n.name, n.summary FROM nodes n WHERE n.suspect=0 AND EXISTS "
        "(SELECT 1 FROM edges e JOIN sources s ON s.id=e.source_id WHERE s.topic=? "
        "AND (e.src=n.id OR e.dst=n.id)) ORDER BY n.id LIMIT 12",
        (ctx.get("topic"),),
    )
    ids = {node["id"] for node in nodes}
    edges = _rows(
        ctx,
        "SELECT e.id, e.src, e.rel, e.dst FROM edges e "
        "JOIN sources s ON s.id=e.source_id "
        "WHERE s.topic=? ORDER BY e.id",
        (ctx.get("topic"),),
    )
    for node in nodes:
        node["edges"] = [
            edge
            for edge in edges
            if edge["src"] in ids
            and edge["dst"] in ids
            and node["id"] in (edge["src"], edge["dst"])
        ]
    return nodes


def writing_memory(database, *, day, offtop, topic, include_graph):
    """Use one SQLite snapshot for every memory block in a request."""
    names = (
        ("narrative_offtop", "recent_slots", "open_threads")
        if offtop
        else ("narrative_recent", "open_threads")
    )
    if include_graph:
        names += ("subgraph",)
    if database is None:
        return {name: [] for name in names}
    with database.connection() as connection:
        connection.execute("BEGIN")
        ctx = dict(connection=connection, day=day, offtop=offtop, topic=topic)
        result = {name: BLOCKS[name](ctx) for name in names}
        connection.execute("COMMIT")
        return result
