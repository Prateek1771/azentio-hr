-- Phase 1: requirement rules are governed, immutable rule-set versions (ADR 0009).
CREATE TABLE rule_set_versions (
  id                  bigserial PRIMARY KEY,
  number              int NOT NULL UNIQUE,
  state               text NOT NULL CHECK (state IN ('draft', 'pending_approval', 'approved', 'active', 'superseded', 'rejected')),
  based_on            bigint REFERENCES rule_set_versions(id),
  rollback_of         bigint REFERENCES rule_set_versions(id),
  title               text NOT NULL,
  reason              text NOT NULL DEFAULT '',
  config              jsonb NOT NULL,                      -- {doc_types, locations, departments, roles}
  required_approvals  int NOT NULL DEFAULT 1 CHECK (required_approvals IN (1, 2)),
  preview             jsonb,                               -- impact summary stored at submit time
  author_id           bigint REFERENCES users(id),         -- NULL = system (v1 created from the legacy graph rules)
  created_at          timestamptz NOT NULL DEFAULT now(),
  submitted_at        timestamptz,
  published_at        timestamptz
);
CREATE UNIQUE INDEX one_active_rule_set ON rule_set_versions ((true)) WHERE state = 'active';
CREATE UNIQUE INDEX one_open_draft ON rule_set_versions ((true)) WHERE state IN ('draft', 'pending_approval', 'approved');

CREATE TABLE rules (
  version_id   bigint NOT NULL REFERENCES rule_set_versions(id),
  id           text NOT NULL,
  ord          int NOT NULL,
  item         text NOT NULL,
  name         text NOT NULL DEFAULT '',
  kind         text NOT NULL CHECK (kind IN ('document', 'task', 'ack')),
  category     text NOT NULL,
  match        jsonb NOT NULL DEFAULT '{}',
  offset_days  int NOT NULL DEFAULT 0,
  depends_on   text[] NOT NULL DEFAULT '{}',
  description  text NOT NULL,
  policy_ref   text NOT NULL DEFAULT '',
  auto         text NOT NULL DEFAULT '',
  compliance   boolean NOT NULL DEFAULT false,
  PRIMARY KEY (version_id, id)
);

-- Only an open draft's rules can change; every other version is frozen.
CREATE FUNCTION rules_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (SELECT state FROM rule_set_versions WHERE id = COALESCE(NEW.version_id, OLD.version_id)) <> 'draft' THEN
    RAISE EXCEPTION 'rule set version % is frozen', COALESCE(NEW.version_id, OLD.version_id);
  END IF;
  RETURN COALESCE(NEW, OLD);
END $$;
CREATE TRIGGER rules_frozen BEFORE INSERT OR UPDATE OR DELETE ON rules
  FOR EACH ROW EXECUTE FUNCTION rules_frozen();

-- Nobody approves their own request, enforced by the database as well as the service.
CREATE FUNCTION approvals_not_author() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.approver_id = (SELECT requested_by FROM approval_requests WHERE id = NEW.request_id) THEN
    RAISE EXCEPTION 'an author cannot approve their own request';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER approvals_not_author BEFORE INSERT OR UPDATE ON approvals
  FOR EACH ROW EXECUTE FUNCTION approvals_not_author();
