-- Phase 3 batch A1: owners and email templates move from graph nodes to Postgres.
-- Columns are exactly the old graph properties, so the API shapes don't change.
-- Owner stays in the graph as a projection (records.project_owners); EmailTemplate leaves the graph.

CREATE TABLE owners (
  email      text PRIMARY KEY CHECK (email = lower(email)),
  name       text NOT NULL,
  department text NOT NULL DEFAULT '',
  type       text NOT NULL
);

CREATE TABLE email_templates (
  id      text PRIMARY KEY,
  name    text NOT NULL,
  subject text NOT NULL,
  body    text NOT NULL
);
