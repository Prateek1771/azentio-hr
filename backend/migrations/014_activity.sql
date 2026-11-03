-- Phase 3 batch B: activity, guardrail hits and assistant eval runs move from graph nodes to Postgres.
-- Columns are exactly the old graph properties. ts stays epoch seconds (double precision) as the graph had it, so the
-- API shapes and types don't change. ponytail: timestamptz is a later cleanup once no reader expects floats.

CREATE TABLE events (
  id          text PRIMARY KEY,
  employee_id text NOT NULL DEFAULT '',   -- '' = a system event; no FK, events outlive archived joiners
  kind        text NOT NULL,
  message     text NOT NULL,
  ts          double precision NOT NULL
);
CREATE INDEX events_employee_ts ON events (employee_id, ts DESC);
CREATE INDEX events_ts ON events (ts DESC);

CREATE TABLE guardrail_events (
  id      text PRIMARY KEY,
  ts      double precision NOT NULL,
  surface text NOT NULL,
  rule    text NOT NULL,
  action  text NOT NULL
);
CREATE INDEX guardrail_events_ts ON guardrail_events (ts DESC);

CREATE TABLE eval_runs (
  id       text PRIMARY KEY,
  ts       double precision NOT NULL,
  status   text NOT NULL,
  judge    text NOT NULL DEFAULT '',
  model    text NOT NULL DEFAULT '',
  summary  jsonb NOT NULL DEFAULT '{}',
  done     int NOT NULL DEFAULT 0,
  total    int NOT NULL DEFAULT 0,
  finished double precision,
  error    text
);

CREATE TABLE eval_results (
  id                text PRIMARY KEY,
  run_id            text NOT NULL REFERENCES eval_runs (id),
  n                 int NOT NULL,
  question          text NOT NULL,
  reference         text NOT NULL DEFAULT '',
  answer            text NOT NULL DEFAULT '',
  ms                int,
  guardrail_ok      boolean,
  answered          boolean,
  faithfulness      double precision,
  context_precision double precision,
  context_recall    double precision,
  sources           jsonb NOT NULL DEFAULT '[]',
  UNIQUE (run_id, n)
);
