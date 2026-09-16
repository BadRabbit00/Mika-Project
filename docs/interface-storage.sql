-- Approved reference schema. Installed automatically by src/core/db.py.
-- Preserve deployed migration versions; do not run this file on an upgraded database.

CREATE TABLE settings_overrides (
    key TEXT PRIMARY KEY NOT NULL,
    value_json TEXT NOT NULL CHECK (json_valid(value_json)),
    previous_json TEXT NOT NULL CHECK (json_valid(previous_json)),
    updated_at TEXT NOT NULL CHECK (is_utc_timestamp(updated_at) = 1),
    trace_id TEXT NOT NULL
);

CREATE TABLE post_nodes (
    post_id TEXT NOT NULL REFERENCES posts(id),
    node_id TEXT NOT NULL REFERENCES nodes(id),
    PRIMARY KEY (post_id, node_id)
);

CREATE TABLE post_threads (
    post_id TEXT NOT NULL REFERENCES posts(id),
    thread_id INTEGER NOT NULL REFERENCES threads(id),
    PRIMARY KEY (post_id, thread_id)
);

-- Runs migration: retain call identity separately from the shared trace.
ALTER TABLE runs RENAME COLUMN trace_id TO call_id;
ALTER TABLE runs ADD COLUMN trace_id TEXT;
UPDATE runs SET trace_id = CASE
    WHEN json_valid(params_json)
    THEN COALESCE(json_extract(params_json, '$.trace_id'), call_id)
    ELSE call_id END;
CREATE INDEX runs_trace ON runs(trace_id);
