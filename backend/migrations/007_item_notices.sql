-- Phase 1: one "new requirements" notice per joiner per published version.
CREATE TABLE item_notices (
  employee_id  text NOT NULL REFERENCES employees(id),
  version_id   bigint NOT NULL REFERENCES rule_set_versions(id),
  sent_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (employee_id, version_id)
);
