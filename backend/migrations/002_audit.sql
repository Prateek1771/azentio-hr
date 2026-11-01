-- Phase 0: append-only, hash-chained audit log.
CREATE TABLE audit_log (
  seq          bigserial PRIMARY KEY,
  ts           timestamptz NOT NULL DEFAULT now(),
  actor        text NOT NULL,
  actor_role   text NOT NULL DEFAULT '',
  action       text NOT NULL,
  entity_type  text NOT NULL,
  entity_id    text NOT NULL DEFAULT '',
  summary      text NOT NULL,
  metadata     jsonb NOT NULL DEFAULT '{}',
  reason       text NOT NULL DEFAULT '',
  prev_hash    text NOT NULL,
  hash         text NOT NULL UNIQUE
);
CREATE INDEX audit_entity ON audit_log (entity_type, entity_id);
CREATE INDEX audit_action ON audit_log (action);

-- Refuse edits at the database, not just in app code. The hash chain still catches anyone who drops this trigger.
CREATE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS
$$ BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$;
CREATE TRIGGER audit_log_no_update BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
