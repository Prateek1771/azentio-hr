-- Phase 2: documents move from the graph to Postgres, and every decision on one is an append-only, attributable row.
-- "The AI perceives; policy decides" (ADR 0001, ADR 0012): tiers and thresholds live in the rule-set version config.

CREATE TABLE documents (
  id                  text PRIMARY KEY,                      -- keeps graph ids (doc_...)
  employee_id         text NOT NULL REFERENCES employees(id),
  item_id             text REFERENCES items(id),             -- replaces (:Item)-[:HAS_DOC]->(:Document); NULL = not on the checklist
  filename            text NOT NULL,
  path                text NOT NULL,
  mime                text NOT NULL,
  size                int NOT NULL DEFAULT 0,
  sha256              text,                                  -- NULL when the file was already missing at backfill
  source              text NOT NULL,                         -- portal | email | integration
  status              text NOT NULL CHECK (status IN ('Submitted','Under Review','Accepted','Correction Requested','Rejected')),
  doc_type            text NOT NULL DEFAULT '',
  tier                text CHECK (tier IN ('low','high')),
  confidence          real NOT NULL DEFAULT 0,
  extracted           jsonb NOT NULL DEFAULT '{}',
  issues              jsonb NOT NULL DEFAULT '[]',
  checks              jsonb NOT NULL DEFAULT '[]',
  model               text,
  prompt_version      text,
  policy_version_id   bigint REFERENCES rule_set_versions(id),
  current_decision_id bigint,                                -- the one active decision (FK added below)
  received_at         timestamptz NOT NULL DEFAULT now(),
  decided_at          timestamptz,
  extra               jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX documents_employee ON documents (employee_id, received_at DESC);
CREATE INDEX documents_item ON documents (item_id, received_at DESC);
CREATE INDEX documents_status ON documents (status);

CREATE TABLE document_mismatches (                           -- replaces (:Document)-[:MISMATCH]->(:Employee)
  document_id text NOT NULL REFERENCES documents(id),
  field       text NOT NULL,
  expected    text NOT NULL,
  found       text NOT NULL,
  PRIMARY KEY (document_id, field)
);

CREATE TABLE doc_decisions (
  id                bigserial PRIMARY KEY,
  document_id       text NOT NULL REFERENCES documents(id),
  kind              text NOT NULL CHECK (kind IN ('auto_accept','auto_correction','routed_review','human_accept',
                                                  'human_reject','human_correction','human_override',
                                                  'spot_check_pass','spot_check_fail','backfill')),
  decided_by        bigint REFERENCES users(id),             -- NULL = system
  reason_code       text NOT NULL,
  reason_text       text NOT NULL DEFAULT '',
  status_after      text NOT NULL,
  model             text,
  prompt_version    text,
  confidence        real,
  tier              text,
  threshold         real,
  policy_version_id bigint REFERENCES rule_set_versions(id),
  checks            jsonb NOT NULL DEFAULT '[]',
  extracted         jsonb NOT NULL DEFAULT '{}',
  corrected_fields  jsonb NOT NULL DEFAULT '{}',
  work_item_id      bigint REFERENCES work_queue(id),
  idempotency_key   text UNIQUE,                             -- a retried job or a double-clicked button can't add a second row
  at                timestamptz NOT NULL DEFAULT now(),
  CHECK (kind NOT LIKE 'human_%' OR (decided_by IS NOT NULL AND length(reason_text) >= 3)),
  CHECK (kind NOT LIKE 'spot_check_%' OR (decided_by IS NOT NULL AND length(reason_text) >= 3))
);
CREATE INDEX doc_decisions_doc ON doc_decisions (document_id, at);
ALTER TABLE documents ADD CONSTRAINT documents_current_decision
  FOREIGN KEY (current_decision_id) REFERENCES doc_decisions(id);

-- Append-only, like audit_log.
CREATE FUNCTION doc_decisions_append_only() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'doc_decisions is append-only';
END $$ LANGUAGE plpgsql;
CREATE TRIGGER doc_decisions_no_update BEFORE UPDATE OR DELETE ON doc_decisions
  FOR EACH ROW EXECUTE FUNCTION doc_decisions_append_only();

-- Hard floor: a high-risk document can never be accepted automatically, whatever the app code does.
CREATE FUNCTION no_auto_accept_high() RETURNS trigger AS $$
BEGIN
  IF NEW.kind = 'auto_accept' AND NEW.tier = 'high' THEN
    RAISE EXCEPTION 'high-risk document % cannot be auto-accepted', NEW.document_id;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER no_auto_accept_high BEFORE INSERT ON doc_decisions FOR EACH ROW EXECUTE FUNCTION no_auto_accept_high();

-- Thresholds and tiers now live in rule-set config: like its rules, a version's config is frozen once it leaves draft.
CREATE FUNCTION rule_set_config_frozen() RETURNS trigger AS $$
BEGIN
  IF OLD.state <> 'draft' AND NEW.config IS DISTINCT FROM OLD.config THEN
    RAISE EXCEPTION 'rule-set version % is %, its config can no longer change', OLD.number, OLD.state;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER rule_set_config_frozen BEFORE UPDATE ON rule_set_versions
  FOR EACH ROW EXECUTE FUNCTION rule_set_config_frozen();
