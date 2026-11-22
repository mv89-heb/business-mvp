import hashlib
import hmac
import json

import pytest

from business_app.config import load_business_config
from app import create_app, db


@pytest.fixture()
def integration_client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-password")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "test-meta-secret")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "test-verify-token")
    monkeypatch.setenv("AUTOMATION_WEBHOOK_SECRET", "test-automation-secret")
    config = load_business_config()
    config["integrations"]["whatsapp_cloud_api"]["enabled"] = True
    app = create_app({
        "TESTING": True,
        "BUSINESS_CONFIG": config,
        "SECRET_KEY": "test-secret",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'integrations.db'}",
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
    })
    with app.test_client() as client:
        yield client, app
    with app.app_context():
        db.drop_all()


def _signed_payload(payload, secret="test-meta-secret"):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return body, "sha256=" + signature


def _whatsapp_payload(message_id="wamid.001"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "field": "messages",
                "value": {
                    "contacts": [{
                        "wa_id": "972501234567",
                        "profile": {"name": "ישראל ישראלי"},
                    }],
                    "messages": [{
                        "id": message_id,
                        "from": "972501234567",
                        "timestamp": "1760000000",
                        "type": "text",
                        "text": {"body": "מבקש הצעת מחיר לפרגולה"},
                    }],
                },
            }],
        }],
    }


def test_whatsapp_webhook_verification_requires_token(integration_client):
    client, _ = integration_client
    accepted = client.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=test-verify-token&hub.challenge=abc123")
    rejected = client.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=abc123")
    assert accepted.status_code == 200
    assert accepted.get_data(as_text=True) == "abc123"
    assert rejected.status_code == 403


def test_whatsapp_inbound_creates_lead_message_and_outbox_once(integration_client):
    client, app = integration_client
    body, signature = _signed_payload(_whatsapp_payload())
    headers = {"X-Hub-Signature-256": signature, "Content-Type": "application/json"}
    first = client.post("/webhooks/whatsapp", data=body, headers=headers)
    duplicate = client.post("/webhooks/whatsapp", data=body, headers=headers)
    assert first.status_code == 200
    assert first.get_json()["processed"] == 1
    assert duplicate.status_code == 200
    assert duplicate.get_json()["processed"] == 0
    with app.app_context():
        from app import Lead, LeadMetadata, LeadMessage, IntegrationOutbox
        lead = db.session.execute(db.select(Lead)).scalar_one()
        metadata = db.session.get(LeadMetadata, lead.id)
        messages = db.session.execute(db.select(LeadMessage)).scalars().all()
        outbox = db.session.execute(db.select(IntegrationOutbox)).scalars().all()
        assert lead.phone == "0501234567"
        assert metadata.source == "whatsapp"
        assert len(messages) == 1
        assert len(outbox) == 2
        assert all("phone" not in event.payload for event in outbox)


def test_whatsapp_webhook_rejects_invalid_signature(integration_client):
    client, _ = integration_client
    body, _ = _signed_payload(_whatsapp_payload())
    response = client.post("/webhooks/whatsapp", data=body, headers={"X-Hub-Signature-256": "sha256=invalid"})
    assert response.status_code == 403


def test_automation_outbox_requires_bearer_and_supports_ack(integration_client):
    client, app = integration_client
    response = client.post("/leads", data={
        "name": "לקוח בדיקה",
        "phone": "050-7654321",
        "service": "פרגולה",
        "details": "פרגולה בחצר",
        "contact_consent": "on",
    })
    assert response.status_code == 302
    assert client.get("/api/integrations/outbox").status_code == 401
    headers = {"Authorization": "Bearer test-automation-secret"}
    pending = client.get("/api/integrations/outbox", headers=headers)
    assert pending.status_code == 200
    events = pending.get_json()["events"]
    assert len(events) == 1
    event_id = events[0]["event_id"]
    ack = client.post(f"/api/integrations/outbox/{event_id}/ack", headers=headers)
    assert ack.status_code == 200
    assert client.get("/api/integrations/outbox", headers=headers).get_json()["events"] == []


def test_automation_enrichment_callback_updates_metadata(integration_client):
    client, app = integration_client
    client.post("/leads", data={
        "name": "לקוח AI",
        "phone": "050-7654322",
        "service": "דק",
        "contact_consent": "on",
    })
    with app.app_context():
        from app import Lead
        lead_id = db.session.execute(db.select(Lead.id)).scalar_one()
    response = client.post(
        f"/api/integrations/leads/{lead_id}/enrichment",
        headers={"Authorization": "Bearer test-automation-secret"},
        json={"summary": "מעוניין בדק לחצר", "category": "דקים", "priority": "high", "tags": ["חצר", "דחוף"]},
    )
    assert response.status_code == 200
    client.post("/admin/login", data={"password": "test-admin-password"})
    dashboard = client.get("/admin")
    assert "מעוניין בדק לחצר" in dashboard.get_data(as_text=True)
    assert "מקור: website" in dashboard.get_data(as_text=True)
