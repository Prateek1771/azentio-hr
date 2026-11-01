-- Phase 0: durable job queue (ADR 0007).
CREATE TYPE job_state AS ENUM ('queued', 'running', 'done', 'dead');

CREATE TABLE jobs (
  id               bigserial PRIMARY KEY,
  kind             text NOT NULL,
  payload          jsonb NOT NULL DEFAULT '{}',
  idempotency_key  text UNIQUE,
  state            job_state NOT NULL DEFAULT 'queued',
  attempts         int NOT NULL DEFAULT 0,
  max_attempts     int NOT NULL DEFAULT 5,
  run_at           timestamptz NOT NULL DEFAULT now(),
  locked_by        text,
  locked_at        timestamptz,
  last_error       text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  finished_at      timestamptz
);
CREATE INDEX jobs_ready ON jobs (run_at) WHERE state = 'queued';
CREATE INDEX jobs_running ON jobs (locked_at) WHERE state = 'running';

CREATE TABLE worker_heartbeat (
  worker   text PRIMARY KEY,
  seen_at  timestamptz NOT NULL
);
