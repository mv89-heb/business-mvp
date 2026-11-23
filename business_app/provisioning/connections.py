"""Encrypted, customer-scoped provider credential storage.

The vault deliberately stores only authenticated ciphertext in PostgreSQL. The
encryption key must be supplied by the deployment secret manager and is never
stored alongside the ciphertext.
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PROVIDERS = frozenset({"github", "neon", "render"})
CUSTOMER_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
KEY_ENV = "CUSTOMER_CREDENTIALS_ENCRYPTION_KEY"


class ConnectionVaultError(RuntimeError):
    """Safe-to-display connection vault error; never contains secret values."""


def _validate_scope(customer_id: str, provider: str) -> tuple[str, str]:
    if not isinstance(customer_id, str) or not CUSTOMER_ID_RE.fullmatch(customer_id):
        raise ConnectionVaultError("Invalid customer connection scope")
    if provider not in PROVIDERS:
        raise ConnectionVaultError("Unsupported provider connection")
    return customer_id, provider


def load_encryption_key(value: str | None = None) -> bytes:
    """Load a 32-byte URL-safe-base64 key; fail closed when not configured."""
    encoded = value if value is not None else os.getenv(KEY_ENV, "")
    if not isinstance(encoded, str) or not encoded.strip():
        raise ConnectionVaultError(f"{KEY_ENV} is required before provider credentials can be stored")
    try:
        key = base64.urlsafe_b64decode(encoded.strip().encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        raise ConnectionVaultError(f"{KEY_ENV} must be a URL-safe base64 encoded 32-byte key") from None
    if len(key) != 32:
        raise ConnectionVaultError(f"{KEY_ENV} must decode to exactly 32 bytes")
    return key


@dataclass(frozen=True)
class StoredProviderConnection:
    customer_id: str
    provider: str
    metadata: dict[str, Any]
    verified_at: str | None = None


class CredentialVault:
    """AES-256-GCM envelope with customer/provider-bound associated data."""

    def __init__(self, key: bytes | None = None):
        self._key = key if key is not None else load_encryption_key()
        if not isinstance(self._key, bytes) or len(self._key) != 32:
            raise ConnectionVaultError("Credential encryption key must contain exactly 32 bytes")

    @staticmethod
    def _aad(customer_id: str, provider: str) -> bytes:
        customer_id, provider = _validate_scope(customer_id, provider)
        return f"business-studio:provider-credential:v1:{customer_id}:{provider}".encode("utf-8")

    def encrypt(self, customer_id: str, provider: str, credentials: dict[str, str]) -> str:
        aad = self._aad(customer_id, provider)
        if not isinstance(credentials, dict) or not credentials:
            raise ConnectionVaultError("Provider credentials are required")
        if any(not isinstance(k, str) or not isinstance(v, str) or not v.strip() for k, v in credentials.items()):
            raise ConnectionVaultError("Provider credentials must be non-empty text values")
        nonce = secrets.token_bytes(12)
        plaintext = json.dumps(credentials, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ciphertext = AESGCM(self._key).encrypt(nonce, plaintext, aad)
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, customer_id: str, provider: str, ciphertext: str) -> dict[str, str]:
        aad = self._aad(customer_id, provider)
        try:
            raw = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
            if len(raw) < 29:
                raise ValueError
            plaintext = AESGCM(self._key).decrypt(raw[:12], raw[12:], aad)
            result = json.loads(plaintext.decode("utf-8"))
        except Exception:
            raise ConnectionVaultError("Stored provider credential could not be authenticated") from None
        if not isinstance(result, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in result.items()):
            raise ConnectionVaultError("Stored provider credential has an invalid format")
        return result


class ProviderConnectionStore:
    """PostgreSQL store for encrypted credentials and non-secret account metadata."""

    def __init__(self, *, database_url: str | None = None, vault: CredentialVault | None = None):
        self.database_url = (database_url or os.getenv("DATABASE_URL", "")).strip()
        if not self.database_url:
            raise ConnectionVaultError("DATABASE_URL is required for durable provider connections")
        self.vault = vault or CredentialVault()

    def _connect(self):
        import psycopg
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return psycopg.connect(url, connect_timeout=8)

    @staticmethod
    def _ensure_schema(connection):
        connection.execute("""
            CREATE TABLE IF NOT EXISTS provisioning_provider_connections (
                customer_id VARCHAR(63) NOT NULL,
                provider VARCHAR(16) NOT NULL,
                encrypted_credentials TEXT NOT NULL,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                verified_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (customer_id, provider),
                CHECK (provider IN ('github', 'neon', 'render'))
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS ix_provisioning_provider_connections_customer
            ON provisioning_provider_connections (customer_id)
        """)

    def save(self, customer_id: str, provider: str, credentials: dict[str, str],
             metadata: dict[str, Any], *, verified: bool = False) -> StoredProviderConnection:
        customer_id, provider = _validate_scope(customer_id, provider)
        if not isinstance(metadata, dict):
            raise ConnectionVaultError("Provider account metadata must be an object")
        # Metadata is intentionally limited to non-secret identifiers and labels.
        allowed = {"account", "account_id", "organization_id", "workspace_id", "scopes"}
        safe_metadata = {k: v for k, v in metadata.items() if k in allowed}
        if any(not isinstance(v, (str, int, list)) for v in safe_metadata.values()):
            raise ConnectionVaultError("Provider account metadata contains unsupported values")
        encrypted = self.vault.encrypt(customer_id, provider, credentials)
        import json
        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.execute("""
                INSERT INTO provisioning_provider_connections
                    (customer_id, provider, encrypted_credentials, metadata, verified_at, updated_at)
                VALUES (%s, %s, %s, %s::jsonb, CASE WHEN %s THEN NOW() ELSE NULL END, NOW())
                ON CONFLICT (customer_id, provider) DO UPDATE SET
                    encrypted_credentials = EXCLUDED.encrypted_credentials,
                    metadata = EXCLUDED.metadata,
                    verified_at = EXCLUDED.verified_at,
                    updated_at = NOW()
            """, (customer_id, provider, encrypted, json.dumps(safe_metadata), bool(verified)))
        return StoredProviderConnection(customer_id, provider, safe_metadata, None)

    def get(self, customer_id: str, provider: str) -> tuple[dict[str, str], StoredProviderConnection] | None:
        customer_id, provider = _validate_scope(customer_id, provider)
        with self._connect() as connection:
            self._ensure_schema(connection)
            row = connection.execute("""
                SELECT encrypted_credentials, metadata, verified_at
                FROM provisioning_provider_connections
                WHERE customer_id = %s AND provider = %s
            """, (customer_id, provider)).fetchone()
        if not row:
            return None
        metadata = row[1] if isinstance(row[1], dict) else json.loads(row[1] or "{}")
        verified_at = row[2].isoformat() if row[2] else None
        credentials = self.vault.decrypt(customer_id, provider, row[0])
        return credentials, StoredProviderConnection(customer_id, provider, metadata, verified_at)

    def delete(self, customer_id: str, provider: str) -> bool:
        customer_id, provider = _validate_scope(customer_id, provider)
        with self._connect() as connection:
            self._ensure_schema(connection)
            cursor = connection.execute("""
                DELETE FROM provisioning_provider_connections
                WHERE customer_id = %s AND provider = %s
            """, (customer_id, provider))
            return cursor.rowcount > 0
