-- Proposed learner and scheduler storage, not a production migration.
-- Dry runs and tests install this contract only in isolated databases.
CREATE TABLE learner_state (
    id TEXT PRIMARY KEY CHECK (id='learner'),
    state_json TEXT NOT NULL CHECK (json_valid(state_json)),
    paused_until TEXT CHECK (paused_until IS NULL OR is_utc_timestamp(paused_until)),
    generation_after TEXT CHECK (generation_after IS NULL OR is_utc_timestamp(generation_after))
);
CREATE TABLE learning_events (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    at TEXT NOT NULL CHECK (is_utc_timestamp(at)),
    event_json TEXT NOT NULL CHECK (json_valid(event_json)),
    state_json TEXT NOT NULL CHECK (json_valid(state_json))
);
CREATE TABLE learning_actions (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES learning_events(id),
    predecessor TEXT REFERENCES learning_actions(id),
    action_json TEXT NOT NULL CHECK (json_valid(action_json)),
    status TEXT NOT NULL CHECK (status IN ('pending','running','waiting','completed','failed','uncertain')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts>=0),
    due_at TEXT NOT NULL CHECK (is_utc_timestamp(due_at)),
    completed_at TEXT CHECK (completed_at IS NULL OR is_utc_timestamp(completed_at)),
    result_event TEXT CHECK (result_event IS NULL OR json_valid(result_event)),
    error TEXT
);
CREATE INDEX learning_actions_due ON learning_actions(status,due_at);
CREATE TABLE activity_reservations (
    action_id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    at TEXT NOT NULL CHECK (is_utc_timestamp(at))
);
