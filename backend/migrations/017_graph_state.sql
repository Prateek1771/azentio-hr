-- Phase 3 batch E: the graph is a read model. One row records whether it's being rebuilt (graph-backed views then
-- answer "rebuilding" instead of serving a partial projection) and when the one-time cleanup of retired labels ran.

CREATE TABLE graph_state (
  id          int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  state       text NOT NULL DEFAULT 'ready' CHECK (state IN ('ready', 'rebuilding')),
  reason      text NOT NULL DEFAULT '',
  started_at  timestamptz,
  finished_at timestamptz,
  cleaned_at  timestamptz,                  -- set by `python -m read_model cleanup`; after it, any other label is drift
  report      jsonb NOT NULL DEFAULT '{}'
);
INSERT INTO graph_state (id) VALUES (1);
