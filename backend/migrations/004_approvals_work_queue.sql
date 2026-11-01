-- Phase 0: approval records (used from Phase 1) and the single exceptions inbox.
CREATE TABLE approval_requests (
  id            bigserial PRIMARY KEY,
  subject_type  text NOT NULL,
  subject_id    text NOT NULL,
  requested_by  bigint NOT NULL REFERENCES users(id),
  required      int NOT NULL DEFAULT 1 CHECK (required BETWEEN 1 AND 3),
  status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'withdrawn')),
  reason        text NOT NULL DEFAULT '',
  created_at    timestamptz NOT NULL DEFAULT now(),
  decided_at    timestamptz
);
CREATE INDEX approval_requests_subject ON approval_requests (subject_type, subject_id);

CREATE TABLE approvals (
  request_id   bigint NOT NULL REFERENCES approval_requests(id),
  approver_id  bigint NOT NULL REFERENCES users(id),
  decision     text NOT NULL CHECK (decision IN ('approve', 'reject')),
  reason       text NOT NULL DEFAULT '',
  ts           timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (request_id, approver_id)
);

CREATE TABLE work_queue (
  id             bigserial PRIMARY KEY,
  kind           text NOT NULL,
  entity_type    text NOT NULL,
  entity_id      text NOT NULL,
  reason         text NOT NULL,
  detail         jsonb NOT NULL DEFAULT '{}',
  assignee_role  user_role NOT NULL,
  assignee_id    bigint REFERENCES users(id),
  status         text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'resolved', 'dismissed')),
  sla_due        timestamptz,
  resolution     text,
  resolved_by    bigint REFERENCES users(id),
  created_at     timestamptz NOT NULL DEFAULT now(),
  resolved_at    timestamptz,
  dedupe_key     text UNIQUE
);
CREATE INDEX work_queue_open ON work_queue (status, assignee_role);
