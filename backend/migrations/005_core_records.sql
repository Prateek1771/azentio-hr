-- Phase 1: employees and checklist items move to Postgres (ADR 0005). The graph keeps a projection,
-- updated in place by records.project() so document edges (HAS_DOC, SUBMITTED, MISMATCH) stay attached.
CREATE TABLE employees (
  id              text PRIMARY KEY,
  name            text NOT NULL,
  email           text NOT NULL,
  role            text NOT NULL DEFAULT '',
  grade           text NOT NULL DEFAULT '',
  department      text NOT NULL DEFAULT '',
  location        text NOT NULL DEFAULT '',
  manager_email   text NOT NULL DEFAULT '',
  joining_date    date NOT NULL,
  date_of_birth   date,
  worker_type     text NOT NULL DEFAULT 'employee',
  token           text NOT NULL UNIQUE,
  thread_id       text NOT NULL DEFAULT '',
  doc_scope       text[] NOT NULL DEFAULT '{}',
  status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  extra           jsonb NOT NULL DEFAULT '{}',
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX employees_email_lower ON employees (lower(email));
CREATE INDEX employees_joining ON employees (joining_date);
CREATE INDEX employees_manager ON employees (lower(manager_email));

CREATE TABLE items (
  id                     text PRIMARY KEY,
  employee_id            text NOT NULL REFERENCES employees(id),
  key                    text NOT NULL,
  name                   text NOT NULL,
  kind                   text NOT NULL CHECK (kind IN ('document', 'task', 'ack')),
  category               text NOT NULL,
  status                 text NOT NULL,
  due                    date,
  reason                 text NOT NULL DEFAULT '',
  policy_ref             text NOT NULL DEFAULT '',
  auto                   text NOT NULL DEFAULT '',
  depends                text[] NOT NULL DEFAULT '{}',
  owner_name             text NOT NULL DEFAULT '',
  owner_email            text NOT NULL DEFAULT '',
  rule_id                text NOT NULL DEFAULT '',
  rule_version_id        bigint,
  superseded_by_version  bigint,
  cancelled_by_version   bigint,
  extra                  jsonb NOT NULL DEFAULT '{}',
  updated_at             timestamptz NOT NULL DEFAULT now(),
  UNIQUE (employee_id, key)
);
CREATE INDEX items_employee ON items (employee_id);
CREATE INDEX items_status_due ON items (status, due);

-- Which store each record type is read from, and the parity report that allowed the switch (shared/bridge.py).
CREATE TABLE bridge_state (
  record_type  text PRIMARY KEY,
  source       text NOT NULL CHECK (source IN ('graph', 'postgres')),
  report       jsonb NOT NULL DEFAULT '{}',
  switched_at  timestamptz NOT NULL DEFAULT now()
);
