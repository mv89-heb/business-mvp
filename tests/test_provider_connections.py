import base64
import pytest

from business_app.provisioning.connections import (
    ConnectionVaultError, CredentialVault, load_encryption_key,
)


def test_vault_round_trip_and_ciphertext_does_not_contain_token():
    token = "ghp_example_super_secret_token_value"
    vault = CredentialVault(b"x" * 32)
    encrypted = vault.encrypt("yonatan-maintenance", "github", {"token": token})
    assert token not in encrypted
    assert vault.decrypt("yonatan-maintenance", "github", encrypted) == {"token": token}


def test_vault_binds_ciphertext_to_customer_and_provider():
    vault = CredentialVault(b"x" * 32)
    encrypted = vault.encrypt("customer-one", "github", {"token": "secret"})
    with pytest.raises(ConnectionVaultError):
        vault.decrypt("customer-two", "github", encrypted)
    with pytest.raises(ConnectionVaultError):
        vault.decrypt("customer-one", "render", encrypted)


def test_vault_rejects_tampering():
    vault = CredentialVault(b"x" * 32)
    encrypted = vault.encrypt("customer-one", "neon", {"token": "secret"})
    changed = ("A" if encrypted[0] != "A" else "B") + encrypted[1:]
    with pytest.raises(ConnectionVaultError):
        vault.decrypt("customer-one", "neon", changed)


def test_encryption_key_requires_exactly_32_bytes():
    with pytest.raises(ConnectionVaultError):
        load_encryption_key("")
    with pytest.raises(ConnectionVaultError):
        load_encryption_key(base64.urlsafe_b64encode(b"short").decode("ascii"))
    key = b"k" * 32
    assert load_encryption_key(base64.urlsafe_b64encode(key).decode("ascii")) == key


def test_vault_rejects_invalid_customer_and_provider_scope():
    vault = CredentialVault(b"x" * 32)
    with pytest.raises(ConnectionVaultError):
        vault.encrypt("../other-customer", "github", {"token": "secret"})
    with pytest.raises(ConnectionVaultError):
        vault.encrypt("customer-one", "unknown", {"token": "secret"})


class FakeCursor:
    def __init__(self, rowcount=1, row=None):
        self.rowcount = rowcount
        self._row = row
    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self, row=None):
        self.statements = []
        self.row = row
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        return FakeCursor(row=self.row)


def test_postgres_store_persists_ciphertext_only_and_filters_metadata(monkeypatch):
    from business_app.provisioning.connections import ProviderConnectionStore

    fake = FakeConnection()
    store = ProviderConnectionStore(
        database_url="postgresql://example.invalid/db",
        vault=CredentialVault(b"z" * 32),
    )
    monkeypatch.setattr(store, "_connect", lambda: fake)
    secret = "neon_api_key_very_secret"
    saved = store.save(
        "customer-one", "neon", {"token": secret},
        {"organization_id": "org-123", "unexpected_secret": secret},
        verified=True,
    )
    insert = next(params for sql, params in fake.statements if "INSERT INTO provisioning_provider_connections" in sql)
    assert secret not in insert[2]
    assert secret not in insert[3]
    assert saved.metadata == {"organization_id": "org-123"}


def test_postgres_store_is_customer_scoped_and_can_revoke(monkeypatch):
    from business_app.provisioning.connections import ProviderConnectionStore

    fake = FakeConnection()
    store = ProviderConnectionStore(
        database_url="postgresql://example.invalid/db",
        vault=CredentialVault(b"z" * 32),
    )
    monkeypatch.setattr(store, "_connect", lambda: fake)
    assert store.delete("customer-one", "github") is True
    delete_sql, params = fake.statements[-1]
    assert "WHERE customer_id = %s AND provider = %s" in delete_sql
    assert params == ("customer-one", "github")
