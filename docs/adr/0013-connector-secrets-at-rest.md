# ADR 0013: Encrypt connector secrets at rest with an env key, fail fast without it

- **Status:** accepted
- **Date:** 2026-11-02
- **Deciders:** engineering (Phase 3 review)

## Context
Connectors (Slack, Jira, ServiceNow, Google Workspace and the HR Portal) keep a tenant and either a password or an API token. Until Phase 3 batch C, the secret sat in plaintext on the graph's `ConnectorInstance` node. The API never returned it, but anyone with a graph dump or a Redis connection could read it. Batch C moves connectors to Postgres, and the Phase 3 review asked for the secrets to be encrypted on the way. It set three conditions:
- startup fails fast when the key is missing or wrong
- rotation is proven by a test
- a lost key never crashes the app

## Decision
1. **Fernet with a key from the environment.**
   - `connector_instances.secret_enc` holds a Fernet token. `secret_key_id` holds the first 8 hex characters of sha256 of the key that made it. `secret_hint` holds `••••` plus the last 4 characters, computed at save time.
   - `SECRETS_KEY` is the current key. `SECRETS_KEY_PREVIOUS` is optional and only used during a rotation. Both are read through `MultiFernet`.
   - `cryptography` was already installed as a transitive dependency; it is now a direct one.
   - `connectors/secrets.py` does the cryptography, and `connectors/store.py` is the only caller.
2. **Decrypt only where the secret is used.**
   - `store.connection()` returns `secret_set` and `secret_hint` and never decrypts, so listing connectors and showing the connection form need no key.
   - `store.secret()` decrypts for the one call that needs the secret, the connection test. When no configured key can read it, it raises `SecretUnavailable`, which the API turns into a 422 ("secret can't be decrypted with the current key"). Records and every other page keep working.
3. **Fail fast at startup** (`secrets.check_at_startup`, run after the migrations and the batch C cutover):
   - If secrets are stored, `ENV=production` and `SECRETS_KEY` is unset, startup stops with "connector secrets are stored; set SECRETS_KEY".
   - If a stored `secret_key_id` matches none of the configured keys, startup stops and names both the stored and the configured fingerprints.
   - A malformed key gives a clear message and the command that generates a valid one, at startup, even before any secret is stored.
   - Inside a request, a key problem is an HTTP error (503 on save, 422 on test), never `SystemExit`: `KeyConfigError` is a `RuntimeError`, and only `check_at_startup` turns it into an exit.
   - Development without a key uses a fixed, public dev key and logs a warning. Production never uses it.
4. **Rotation:**
   1. Deploy with `SECRETS_KEY=<new>` and `SECRETS_KEY_PREVIOUS=<old>`.
   2. Run `uv run python -m connectors.rotate_key`. It re-encrypts every secret with the new key in one transaction and updates `secret_key_id`.
   3. Remove `SECRETS_KEY_PREVIOUS`. From then on the old key can't decrypt anything; `test_phase3` asserts this.
5. **The migration encrypts.**
   - The batch C cutover encrypts the graph's plaintext secrets as it backfills.
   - Parity compares a sha256 of the plaintext, so no secret appears in the migration report or in a checksum.
   - Once the batch is clean, the cutover removes the plaintext `secret` property from the graph's `ConnectorInstance` nodes (`LabelSpec.after_cutover`), and drift is measured from that point. The graph then holds no credential.
   - `graph_migration rollback C` decrypts back to plaintext, because the previous release reads it that way. Rolling back therefore needs the key.

## Consequences
- **The key is now an operational secret.** Losing it means re-entering every connector's credentials; the records themselves are unaffected. Keep it in the deployment's secret store, apart from database backups.
- **A database dump alone no longer exposes credentials.** A dump together with the key still does. ponytail: an env key, not a KMS. Move to KMS envelope encryption when there are real tenants or a compliance requirement.
- **CI uses a throwaway key** (`SECRETS_KEY` in `ci.yml`), since CI stores no real secrets.
