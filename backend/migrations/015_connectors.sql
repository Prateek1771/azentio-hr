-- Phase 3 batch C: the connector catalog, its instances, connector records and their links to employees move from the
-- graph to Postgres. Times stay epoch seconds (double precision) as the graph had them, so API shapes don't change.
-- Connection secrets are stored only encrypted (Fernet, ADR 0013): secret_enc + the key fingerprint + a display hint.

CREATE TABLE connectors (
  key         text PRIMARY KEY,
  name        text NOT NULL,
  role        text NOT NULL,                 -- source | target | app
  record_kind text NOT NULL,
  demo        boolean NOT NULL DEFAULT false,
  description text NOT NULL DEFAULT ''
);

CREATE TABLE connector_instances (
  id            text PRIMARY KEY,
  key           text NOT NULL REFERENCES connectors(key),
  name          text NOT NULL,
  created       double precision NOT NULL,
  connection    jsonb NOT NULL DEFAULT '{}',  -- tenant, auth_type, username, configured_at, last_test; never the secret
  secret_enc    text NOT NULL DEFAULT '',     -- Fernet token; '' = no secret saved
  secret_key_id text NOT NULL DEFAULT '',     -- first 8 hex of sha256(key) that encrypted it
  secret_hint   text NOT NULL DEFAULT ''      -- '••••' + last 4, computed at save so listing never decrypts
);

CREATE TABLE connector_records (
  id          text PRIMARY KEY,
  instance_id text NOT NULL REFERENCES connector_instances(id),
  kind        text NOT NULL,
  data        jsonb NOT NULL DEFAULT '{}',   -- the record's field values (graph property `values`)
  created     double precision NOT NULL,
  updated     double precision NOT NULL
);
CREATE INDEX connector_records_instance ON connector_records (instance_id, created DESC);

CREATE TABLE employee_record_links (
  employee_id text NOT NULL,                  -- no FK: links outlive an archived joiner, as the graph edges did
  record_id   text NOT NULL REFERENCES connector_records(id) ON DELETE CASCADE,
  type        text NOT NULL CHECK (type IN ('synced_to', 'from_candidate')),
  PRIMARY KEY (employee_id, record_id, type)
);
CREATE INDEX employee_record_links_record ON employee_record_links (record_id);

CREATE TABLE custom_fields (
  id          text PRIMARY KEY,               -- instance_id || ':' || key (the graph had no id)
  instance_id text NOT NULL REFERENCES connector_instances(id),
  key         text NOT NULL,
  label       text NOT NULL,
  type        text NOT NULL,
  required    boolean NOT NULL DEFAULT false,
  created     double precision NOT NULL,
  UNIQUE (instance_id, key)
);

CREATE TABLE import_runs (
  id          text PRIMARY KEY,
  instance_id text NOT NULL,
  filename    text NOT NULL,
  total       int NOT NULL,
  imported    int NOT NULL,
  errors      jsonb NOT NULL DEFAULT '[]',
  ts          double precision NOT NULL
);
CREATE INDEX import_runs_instance ON import_runs (instance_id, ts DESC);

-- The dead IntegrationRun nodes (connectors/sync.run had no callers), archived for `graph_migration rollback C` only.
CREATE TABLE legacy_sync_runs (
  id    text PRIMARY KEY,
  props jsonb NOT NULL
);
