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
