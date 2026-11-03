-- Phase 3 batch A2: uploaded policies move from graph Policy nodes to Postgres (the uploads are authoritative).
-- Columns are exactly the old graph properties. PolicyChunk stays in the graph as a retrieval index rebuilt from here.

CREATE TABLE policies (
  source text PRIMARY KEY,
  title  text NOT NULL,
  text   text NOT NULL
);
