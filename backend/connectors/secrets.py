"""Connector secrets at rest (ADR 0013): Fernet with `SECRETS_KEY`, plus `SECRETS_KEY_PREVIOUS` during a rotation.

Only `connectors/store.py` calls this. A stored secret is a Fernet token plus the fingerprint of the key that made it
(`key_id`: the first 8 hex characters of sha256 of the key). Rotation: deploy with the new key and the old one as
previous, run `python -m connectors.rotate_key`, then drop the previous key.
"""
import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

_log = logging.getLogger("azentio.secrets")
# Dev only: a fixed, public key so a fresh checkout works without setup. Never used when ENV=production.
DEV_KEY = base64.urlsafe_b64encode(hashlib.sha256(b"azentio-onboard dev secrets key, not for production").digest()).decode()
_warned = False


class SecretUnavailable(RuntimeError):
    """The secret is stored but none of the configured keys can decrypt it."""


class KeyConfigError(SystemExit):
    pass


def _production() -> bool:
    return os.getenv("ENV", "development").lower() == "production"


def key_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:8]


def keys() -> list[str]:
    """Configured keys, current first. Dev with no key gets DEV_KEY (warned once); production gets [] instead."""
    global _warned
    out = [k.strip() for k in (os.getenv("SECRETS_KEY", ""), os.getenv("SECRETS_KEY_PREVIOUS", "")) if k.strip()]
    if not os.getenv("SECRETS_KEY", "").strip() and not _production():  # production: no derived key, ever
        if not _warned:
            _log.warning("SECRETS_KEY is not set: connector secrets use the built-in dev key (never in production)")
            _warned = True
        out.insert(0, DEV_KEY)
    for k in out:
        try:
            Fernet(k)
        except (ValueError, TypeError):
            raise KeyConfigError(f"secrets: a configured key (fingerprint {key_id(k)}) is not a valid Fernet key; "
                                 "generate one with `python -c \"from cryptography.fernet import Fernet; "
                                 "print(Fernet.generate_key().decode())\"`") from None
    return out


def encrypt(plain: str) -> tuple[str, str]:
    """(token, key_id) with the current key. Raises KeyConfigError in production without SECRETS_KEY."""
    if _production() and not os.getenv("SECRETS_KEY", "").strip():
        raise KeyConfigError("connector secrets need SECRETS_KEY in production")
    k = keys()[0]
    return Fernet(k).encrypt(plain.encode()).decode(), key_id(k)


def decrypt(token: str) -> str:
    ks = keys()
    if not ks:
        raise SecretUnavailable("secret can't be decrypted: SECRETS_KEY is not set")
    try:
        return MultiFernet([Fernet(k) for k in ks]).decrypt(token.encode()).decode()
    except InvalidToken:
        raise SecretUnavailable("secret can't be decrypted with the current key") from None


def hint(plain: str) -> str:
    return f"••••{plain[-4:]}" if len(plain) >= 8 else ("••••" if plain else "")


def check_at_startup() -> None:
    """Refuse to start when stored secrets can't be read: production without a key, or no key matching the stored
    fingerprints. Runs after migrations and the batch C cutover."""
    from shared import db
    stored = sorted({r["secret_key_id"] for r in db.fetch(
        "SELECT DISTINCT secret_key_id FROM connector_instances WHERE secret_enc <> ''")})
    if not stored:
        return
    if _production() and not os.getenv("SECRETS_KEY", "").strip():
        raise KeyConfigError("bootstrap: connector secrets are stored; set SECRETS_KEY")
    configured = [key_id(k) for k in keys()]
    unreadable = [s for s in stored if s not in configured]
    if unreadable:
        raise KeyConfigError(f"bootstrap: connector secrets were encrypted with key {', '.join(unreadable)}, but the "
                             f"configured keys are {', '.join(configured) or 'none'}. Set SECRETS_KEY (or "
                             "SECRETS_KEY_PREVIOUS) to the key that encrypted them.")
