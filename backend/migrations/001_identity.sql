-- Phase 0: users and server-side sessions (ADR 0008).
CREATE TYPE user_role AS ENUM ('admin', 'hr_ops', 'approver', 'manager', 'auditor');

CREATE TABLE users (
  id          bigserial PRIMARY KEY,
  email       text NOT NULL UNIQUE CHECK (email = lower(email)),
  name        text NOT NULL,
  role        user_role NOT NULL,
  pw_hash     text NOT NULL,
  active      boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  last_login  timestamptz
);

-- The cookie carries a random token; only its sha256 is stored, so a DB leak can't be replayed as a session.
CREATE TABLE sessions (
  token_hash  text PRIMARY KEY,
  user_id     bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at  timestamptz NOT NULL DEFAULT now(),
  expires_at  timestamptz NOT NULL,
  revoked_at  timestamptz
);
CREATE INDEX sessions_user ON sessions (user_id);
