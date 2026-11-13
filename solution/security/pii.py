"""PII protection utilities: HMAC-SHA256 phone matching, Fernet encryption, and payload scrubbing."""
from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable
from typing import Any

from cryptography.fernet import Fernet

from solution.config import Config

_BASELINE_PII_KEYS: frozenset[str] = frozenset({"phone", "customer_name"})


class PIIProtector:
    """Handles deterministic hashing, authenticated encryption, masking, and payload scrubbing."""

    def __init__(self, hmac_key: bytes, fernet_key: bytes) -> None:
        if not hmac_key:
            raise ValueError("hmac_key must not be empty")
        self._hmac_key = hmac_key
        self._fernet = Fernet(fernet_key)

    @classmethod
    def from_config(cls, config: Config) -> PIIProtector:
        """Instantiate PIIProtector from a validated Config."""
        return cls(hmac_key=config.hmac_key, fernet_key=config.fernet_key)

    def hash_phone(self, canonical_phone: str) -> str:
        """Compute deterministic HMAC-SHA256 hex digest for a canonical phone ('+91XXXXXXXXXX')."""
        if not canonical_phone:
            raise ValueError("canonical_phone must not be empty")
        return hmac.new(
            self._hmac_key,
            canonical_phone.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def encrypt_value(self, plaintext: str) -> str:
        """Encrypt a string using authenticated Fernet encryption."""
        token = self._fernet.encrypt(plaintext.encode("utf-8"))
        return token.decode("utf-8")

    def decrypt_value(self, token: str) -> str:
        """Decrypt a Fernet token back to plaintext."""
        plaintext = self._fernet.decrypt(token.encode("utf-8"))
        return plaintext.decode("utf-8")

    def encrypt_name(self, customer_name: str) -> str:
        """Encrypt a customer name for at-rest storage."""
        return self.encrypt_value(customer_name)

    def decrypt_name(self, token: str) -> str:
        """Decrypt an encrypted customer name."""
        return self.decrypt_value(token)

    def encrypt_last4(self, canonical_phone: str) -> str:
        """Extract the last 4 digits of a canonical phone and encrypt them with Fernet."""
        if len(canonical_phone) < 4 or not canonical_phone[-4:].isdigit():
            raise ValueError("canonical_phone must end with 4 digits")
        return self.encrypt_value(canonical_phone[-4:])

    def decrypt_last4(self, token: str) -> str:
        """Decrypt encrypted last-4 digits."""
        return self.decrypt_value(token)

    def mask_phone_from_encrypted_last4(self, token: str) -> str:
        """Decrypt last-4 digits and format the masked phone '+91******XXXX'."""
        last4 = self.decrypt_last4(token)
        return self.format_masked_phone(last4)

    @staticmethod
    def format_masked_phone(last4: str) -> str:
        """Format masked phone number for reports."""
        if len(last4) != 4 or not last4.isdigit():
            raise ValueError("last4 must be exactly 4 digits")
        return f"+91******{last4}"

    @staticmethod
    def sanitize_event_payload(
        payload: dict[str, Any] | None,
        pii_fields: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Exclude schema-defined PII fields recursively from an event payload dict."""
        if not payload:
            return {}
        blocked = {f.strip().lower() for f in pii_fields if f and f.strip()} | _BASELINE_PII_KEYS

        def _scrub(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {
                    k: _scrub(v)
                    for k, v in obj.items()
                    if str(k).strip().lower() not in blocked
                }
            if isinstance(obj, list):
                return [_scrub(item) for item in obj]
            return obj

        return _scrub(payload)
