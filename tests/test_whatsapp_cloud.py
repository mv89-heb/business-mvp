import hashlib
import hmac

from business_app.integrations.whatsapp_cloud import (
    extract_inbound_messages,
    normalize_phone,
    verify_signature,
    verify_webhook_challenge,
)


def test_meta_signature_is_verified_with_sha256():
    body = b'{"object":"whatsapp_business_account"}'
    secret = "test-app-secret"
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(body, "sha256=" + digest, secret)
    assert not verify_signature(body, "sha256=" + digest, "wrong-secret")
    assert not verify_signature(body, digest, secret)


def test_webhook_challenge_requires_exact_verify_token():
    assert verify_webhook_challenge("subscribe", "token", "token", "12345") == "12345"
    assert verify_webhook_challenge("subscribe", "wrong", "token", "12345") is None
    assert verify_webhook_challenge("unsubscribe", "token", "token", "12345") is None


def test_normalize_israeli_whatsapp_phone():
    assert normalize_phone("+972 50-123-4567") == "0501234567"
    assert normalize_phone("050-123-4567") == "0501234567"


def test_extract_inbound_text_message_and_redact_personal_details():
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "contacts": [{"wa_id": "972501234567", "profile": {"name": "ישראל"}}],
                    "messages": [{
                        "id": "wamid.test-1",
                        "from": "972501234567",
                        "timestamp": "1760000000",
                        "type": "text",
                        "text": {"body": "אפשר הצעת מחיר? חייגו 050-123-4567 או me@example.com"},
                    }],
                },
            }],
        }],
    }
    message = list(extract_inbound_messages(payload))[0]
    assert message["message_id"] == "wamid.test-1"
    assert message["phone"] == "0501234567"
    assert message["name"] == "ישראל"
    assert "050-123-4567" in message["body"]
    assert "050-123-4567" not in message["ai_text"]
    assert "me@example.com" not in message["ai_text"]


def test_extract_ignores_status_events_and_malformed_messages():
    payload = {"entry": [{"changes": [{"value": {"statuses": [{"id": "x"}], "messages": [{"type": "text"}]}}]}]}
    assert list(extract_inbound_messages(payload)) == []
