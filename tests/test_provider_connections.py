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


def test_customer_adapter_resolver_requires_all_verified_connections():
    from business_app.provisioning.connections import (
        StoredProviderConnection, load_customer_provider_adapters,
    )

    class Store:
        def get(self, customer_id, provider):
            metadata = {
                "github": {"account": "customer-account"},
                "neon": {"organization_id": "org-customer"},
                "render": {"workspace_id": "workspace-customer"},
            }[provider]
            return (
                {"token": f"{provider}-token"},
                StoredProviderConnection(customer_id, provider, metadata, "2026-11-23T00:00:00+00:00"),
            )

    adapters, owner = load_customer_provider_adapters(Store(), "customer-one")
    assert owner == "customer-account"
    assert adapters["github"].client.token == "github-token"
    assert adapters["neon"].client.token == "neon-token"
    assert adapters["neon"].org_id == "org-customer"
    assert adapters["render"].client.token == "render-token"
    assert adapters["render"].workspace_id == "workspace-customer"
    assert all(getattr(item, "_business_studio_customer_id") == "customer-one" for item in adapters.values())


def test_customer_adapter_resolver_rejects_missing_or_unverified_connections():
    from business_app.provisioning.connections import (
        StoredProviderConnection, load_customer_provider_adapters,
    )

    class MissingStore:
        def get(self, customer_id, provider):
            if provider == "render":
                return None
            return ({"token": "x"}, StoredProviderConnection(customer_id, provider, {"account": "x"}, None))

    with pytest.raises(ConnectionVaultError):
        load_customer_provider_adapters(MissingStore(), "customer-one")


def test_customer_owned_workflow_requires_exact_verified_adapter_scope():
    from business_app.provisioning.workflow import ProvisioningWorkflow

    class Adapter:
        pass

    adapters = [Adapter(), Adapter(), Adapter()]
    for adapter in adapters:
        adapter._business_studio_customer_id = "customer-one"
        adapter._business_studio_verified_connection = True
    workflow = ProvisioningWorkflow(github=adapters[0], neon=adapters[1], render=adapters[2])
    plan = {"ownership_mode": "customer_owned", "business": {"slug": "customer-one"}}
    workflow._require_customer_owned_connections(plan)

    adapters[1]._business_studio_customer_id = "other-customer"
    from business_app.provisioning.providers.http import ProviderError
    with pytest.raises(ProviderError):
        workflow._require_customer_owned_connections(plan)
