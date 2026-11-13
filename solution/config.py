"""Configuration and constants for the interaction ledger pipeline."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet

IST = timezone(timedelta(hours=5, minutes=30))

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMAS_DIR = REPO_ROOT / "assignment-a-data" / "schemas"
DEFAULT_DB_PATH = Path("ledger.db")

ENV_SECRET_KEY = "AU_SECRET_KEY"
ENV_HMAC_KEY = "AU_HMAC_KEY"
ENV_FERNET_KEY = "AU_FERNET_KEY"


class ConfigurationError(ValueError):
    """Raised when required configuration or security keys are missing or invalid."""


@dataclass(frozen=True)
class Config:
    """Runtime configuration for pipeline commands."""

    db_path: Path
    schemas_dir: Path
    hmac_key: bytes
    fernet_key: bytes


def _derive_keys_from_master(secret_key: str) -> tuple[bytes, bytes]:
    """Deterministically derive independent HMAC and Fernet keys from a master secret."""
    master = secret_key.encode("utf-8")
    hmac_key = hmac.new(master, b"au-pii-hmac-sha256-v1", hashlib.sha256).digest()
    fernet_raw = hmac.new(master, b"au-pii-fernet-aes-v1", hashlib.sha256).digest()
    fernet_key = base64.urlsafe_b64encode(fernet_raw)
    return hmac_key, fernet_key


def _resolve_security_keys(
    secret_key: str | None = None,
    hmac_key: str | bytes | None = None,
    fernet_key: str | bytes | None = None,
) -> tuple[bytes, bytes]:
    """Resolve persistent HMAC and Fernet keys from arguments or environment."""
    raw_hmac = hmac_key if hmac_key is not None else os.environ.get(ENV_HMAC_KEY)
    raw_fernet = fernet_key if fernet_key is not None else os.environ.get(ENV_FERNET_KEY)

    hmac_present = bool(raw_hmac.strip() if isinstance(raw_hmac, str) else raw_hmac)
    fernet_present = bool(
        raw_fernet.strip() if isinstance(raw_fernet, str) else raw_fernet
    )

    if hmac_present and fernet_present:
        hmac_bytes = raw_hmac.encode("utf-8") if isinstance(raw_hmac, str) else raw_hmac
        fernet_bytes = (
            raw_fernet.strip().encode("utf-8")
            if isinstance(raw_fernet, str)
            else raw_fernet
        )
        try:
            Fernet(fernet_bytes)
        except Exception as exc:
            raise ConfigurationError(
                f"Invalid {ENV_FERNET_KEY}: must be a 32-byte url-safe base64 Fernet key."
            ) from exc
        return hmac_bytes, fernet_bytes

    if hmac_present != fernet_present:
        raise ConfigurationError(
            f"Incomplete key configuration: both {ENV_HMAC_KEY} and {ENV_FERNET_KEY} "
            f"must be set together, or set {ENV_SECRET_KEY}."
        )

    raw_secret = secret_key if secret_key is not None else os.environ.get(ENV_SECRET_KEY)
    if raw_secret is not None and raw_secret.strip():
        return _derive_keys_from_master(raw_secret)

    raise ConfigurationError(
        f"Missing required PII security keys. Set {ENV_SECRET_KEY} or both "
        f"{ENV_HMAC_KEY} and {ENV_FERNET_KEY} in the environment."
    )


def load_config(
    db_path: str | Path | None = None,
    schemas_dir: str | Path | None = None,
    secret_key: str | None = None,
    hmac_key: str | bytes | None = None,
    fernet_key: str | bytes | None = None,
) -> Config:
    """Build runtime configuration from arguments and environment variables."""
    resolved_db = Path(db_path or os.environ.get("AU_DB_PATH") or DEFAULT_DB_PATH)
    resolved_schemas = Path(
        schemas_dir or os.environ.get("AU_SCHEMAS_DIR") or DEFAULT_SCHEMAS_DIR
    )
    resolved_hmac, resolved_fernet = _resolve_security_keys(
        secret_key=secret_key,
        hmac_key=hmac_key,
        fernet_key=fernet_key,
    )
    return Config(
        db_path=resolved_db,
        schemas_dir=resolved_schemas,
        hmac_key=resolved_hmac,
        fernet_key=resolved_fernet,
    )
