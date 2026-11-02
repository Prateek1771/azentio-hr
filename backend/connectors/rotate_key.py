"""Re-encrypt every stored connector secret with the current SECRETS_KEY (ADR 0013).

Rotation: deploy with SECRETS_KEY=<new> and SECRETS_KEY_PREVIOUS=<old>, run `python -m connectors.rotate_key`, then
remove SECRETS_KEY_PREVIOUS. Everything happens in one transaction: either every secret moves to the new key or none does.
"""
from pathlib import Path

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    from shared import db
    from connectors import secrets, store
    db.init()
    n = store.rotate()
    print(f"re-encrypted {n} connector secret(s) with key {secrets.key_id(secrets.keys()[0])}")
