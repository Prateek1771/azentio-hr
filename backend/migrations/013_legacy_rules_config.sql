-- Phase 3 batch A3: the graph's legacy Rule and Config nodes retire. Rule-set versions (migration 006) already own the
-- rules and the engine config; these archive tables hold the graph's last copy so `graph_migration rollback A3` can
-- restore it for the previous release. Nothing reads them at runtime; PR E drops them with the graph cleanup.

CREATE TABLE legacy_rules (
  id    text PRIMARY KEY,
  props jsonb NOT NULL
);

CREATE TABLE legacy_config (
  key   text PRIMARY KEY,
  value text NOT NULL
);
