-- Phase 3 batch D: integrations (setup state, per-candidate progress, runs, the run log, outbound and agent messages)
-- move from graph nodes to Postgres. Columns are the old graph properties; times stay epoch seconds (double precision).
-- document_results, generated_values and access_results stay text holding JSON, exactly as the graph stored them.

CREATE TABLE integrations (
  id          text PRIMARY KEY,
  name        text NOT NULL,
  status      text NOT NULL,
  created     double precision NOT NULL,
  next_run_at double precision NOT NULL DEFAULT 0,  -- 0 = not scheduled
  config      jsonb NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX integrations_name ON integrations (lower(name));

CREATE TABLE integration_candidates (
  id                 text PRIMARY KEY,
  integration_id     text NOT NULL REFERENCES integrations(id),
  record_id          text NOT NULL,              -- the source connector record (no FK: it may be re-imported)
  candidate_ref      text,
  name               text,
  email              text,
  stage              text NOT NULL,
  document_results   text,                       -- JSON, as the graph stored it
  document_attempts  int,
  joiner_id          text,
  last_error         text,
  generated_values   text,                       -- JSON
  employee_record_id text,
  access_results     text,                       -- JSON
  decided_at         double precision,
  rejection_reason   text,
  created            double precision NOT NULL,
  updated            double precision NOT NULL,
  UNIQUE (integration_id, record_id)             -- enrolment is idempotent, also under two concurrent runs
);
CREATE INDEX integration_candidates_joiner ON integration_candidates (joiner_id);

CREATE TABLE workflow_runs (
  id               text PRIMARY KEY,
  integration_id   text NOT NULL,
  integration_name text NOT NULL,
  trigger          text NOT NULL,
  status           text NOT NULL,
  started          double precision NOT NULL,
  finished         double precision NOT NULL DEFAULT 0,
  counts           jsonb NOT NULL DEFAULT '{}',
  details          jsonb NOT NULL DEFAULT '[]'
);
CREATE INDEX workflow_runs_started ON workflow_runs (started DESC);
-- one running run per integration, enforced by the database
CREATE UNIQUE INDEX workflow_runs_one_running ON workflow_runs (integration_id) WHERE status = 'running';

CREATE TABLE integration_log_records (
  id                  text PRIMARY KEY,
  integration_id      text NOT NULL,
  run_id              text NOT NULL,
  candidate_record_id text,
  candidate_ref       text,
  candidate_name      text,
  outcome             text,
  stage_before        text,
  stage_after         text,
  actions             jsonb NOT NULL DEFAULT '[]',  -- already filtered by the excluded sections when written
  mapping             jsonb NOT NULL DEFAULT '[]',  -- excluded fields' values are never written
  error               text,
  ts                  double precision NOT NULL
);
CREATE INDEX integration_log_records_run ON integration_log_records (run_id);

CREATE TABLE outbound_messages (
  id             text PRIMARY KEY,
  integration_id text NOT NULL,
  item_id        text,
  kind           text NOT NULL,
  recipient      text NOT NULL,                 -- the graph's `to` (an SQL keyword); the API still says `to`
  subject        text,
  content        text,
  attempt        int,
  delivered_by   text,
  provider_ref   text,
  candidate      text,
  ts             double precision NOT NULL
);
CREATE INDEX outbound_messages_integration ON outbound_messages (integration_id, ts DESC);

CREATE TABLE agent_messages (
  id             text PRIMARY KEY,
  integration_id text NOT NULL,
  role           text NOT NULL,
  content        text NOT NULL,
  ts             double precision NOT NULL
);
CREATE INDEX agent_messages_integration ON agent_messages (integration_id, ts);
