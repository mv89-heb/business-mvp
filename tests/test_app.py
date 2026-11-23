import os
import pytest
from business_app.config import load_business_config
from werkzeug.security import generate_password_hash
from app import create_app, db


@pytest.fixture()
def client(tmp_path):
    os.environ["ADMIN_PASSWORD"] = "test-admin-password"
    business_config = load_business_config()
    business_config["modules"]["estimate_calculator"] = True
    business_config["integrations"]["google_sheets_webhook"]["enabled"] = True
    app = create_app({
        "TESTING": True,
        "BUSINESS_CONFIG": business_config,
        "SECRET_KEY": "test-secret",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
    })
    with app.test_client() as client:
        yield client
    with app.app_context():
        db.drop_all()


def test_homepage_is_hebrew_rtl(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'lang="he" dir="rtl"' in html
    from business_app.config import load_business_config
    config = load_business_config()
    for service in config["content"]["services"]:
        assert service["name"] in html


def test_estimate_without_approved_rates_is_not_invented(client):
    response = client.post("/estimate", data={"area": "25"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["available"] is False
    assert payload["minimum"] is None


def test_estimate_rejects_invalid_area(client):
    response = client.post("/estimate", data={"area": "-4"})
    assert response.status_code == 400


def test_lead_is_saved_and_thanks_page_shown(client):
    response = client.post("/leads", data={
        "name": "ישראל ישראלי",
        "phone": "050-1234567",
        "service": "פרגולה",
        "area": "20",
        "details": "פרגולה",
        "contact_consent": "on",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert "הפרטים התקבלו" in response.get_data(as_text=True)
    assert "מספר הפנייה שלכם" in response.get_data(as_text=True)
    assert "#1" in response.get_data(as_text=True)
    with client.application.app_context():
        from app import Lead
        lead = db.session.execute(db.select(Lead)).scalar_one()
        assert lead.name == "ישראל ישראלי"
        assert lead.phone == "050-1234567"
        assert lead.service == "פרגולה"


def test_lead_requires_contact_consent(client):
    response = client.post("/leads", data={
        "name": "ישראל ישראלי",
        "phone": "050-1234567",
        "service": "בניה",
    }, follow_redirects=True)
    assert "יש לאשר יצירת קשר" in response.get_data(as_text=True)
    with client.application.app_context():
        from app import Lead
        assert db.session.execute(db.select(Lead)).scalars().all() == []


def test_admin_requires_login(client):
    response = client.get("/admin")
    assert response.status_code == 302
    assert "/admin/login" in response.location


def test_admin_login_and_dashboard(client):
    response = client.post("/admin/login", data={"password": "test-admin-password"})
    assert response.status_code == 302
    response = client.get("/admin")
    assert response.status_code == 200
    assert "פניות לקוחות" in response.get_data(as_text=True)


def test_admin_login_accepts_hash_and_prefers_it_over_plaintext(monkeypatch, client):
    monkeypatch.setenv("ADMIN_PASSWORD", "legacy-plaintext-password")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", generate_password_hash("hashed-admin-password"))

    accepted = client.post("/admin/login", data={"password": "hashed-admin-password"})
    assert accepted.status_code == 302
    assert accepted.location.endswith("/admin")

    with client.application.test_client() as fresh_client:
        rejected = fresh_client.post("/admin/login", data={"password": "legacy-plaintext-password"})
        assert rejected.status_code == 200
        assert "פרטי ההתחברות אינם נכונים" in rejected.get_data(as_text=True)


def test_health_check_is_exempt_from_global_rate_limits(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'health.db'}",
        "RATELIMIT_ENABLED": True,
    })
    with app.test_client() as test_client:
        responses = [test_client.get("/health") for _ in range(55)]
    assert all(response.status_code == 200 for response in responses)


def test_admin_search_filters_leads(client):
    from app import Lead
    with client.application.app_context():
        db.session.add_all([
            Lead(name="ישראל כהן", phone="050-1111111", service="פרגולה",
                 city="בית שמש", details="פרגולה בחצר", status="חדש"),
            Lead(name="דוד לוי", phone="050-2222222", service="דק",
                 city="ירושלים", details="דק במרפסת", status="בטיפול"),
        ])
        db.session.commit()
    client.post("/admin/login", data={"password": "test-admin-password"})
    response = client.get("/admin?q=פרגולה")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "ישראל כהן" in html
    assert "דוד לוי" not in html
    assert "ייצוא תוצאות" in html


def test_admin_csv_export_requires_login_and_exports_filtered_data(client):
    from app import Lead
    denied = client.get("/admin/export.csv")
    assert denied.status_code == 302
    with client.application.app_context():
        db.session.add(Lead(name="לקוח לבדיקה", phone="050-1234567",
                            service="דק", status="חדש"))
        db.session.commit()
    client.post("/admin/login", data={"password": "test-admin-password"})
    response = client.get("/admin/export.csv?status=חדש")
    assert response.status_code == 200
    assert "text/csv" in response.content_type
    assert "לקוח לבדיקה" in response.get_data(as_text=True)
    assert "050-1234567" in response.get_data(as_text=True)


def test_lead_webhook_is_optional_and_sends_saved_lead(monkeypatch, client):
    import json
    from unittest.mock import MagicMock
    import app as app_module

    captured = {}
    response = MagicMock()
    response.status = 202
    response.read.return_value = b'{"ok": true}'
    response.__enter__.return_value = response

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return response

    monkeypatch.setenv("LEAD_WEBHOOK_URL", "https://automation.example.test/lead")
    monkeypatch.setenv("LEAD_WEBHOOK_SECRET", "test-shared-secret")
    monkeypatch.setattr(app_module.urllib.request, "urlopen", fake_urlopen)
    response = client.post("/leads", data={
        "name": "לקוח webhook",
        "phone": "050-1234567",
        "service": "דק",
        "contact_consent": "on",
    })
    assert response.status_code == 302
    assert response.location.startswith("/thanks?lead_id=")
    assert captured["url"] == "https://automation.example.test/lead"
    assert captured["timeout"] == 8
    assert captured["body"]["event"] == "lead.created"
    assert captured["body"]["token"] == "test-shared-secret"
    assert captured["body"]["lead"]["name"] == "לקוח webhook"
    assert captured["body"]["lead"]["service"] == "דק"


def test_lead_is_saved_even_when_webhook_fails(monkeypatch, client):
    import app as app_module
    monkeypatch.setenv("LEAD_WEBHOOK_URL", "https://automation.example.test/lead")
    monkeypatch.setenv("LEAD_WEBHOOK_SECRET", "test-shared-secret")

    def failed_urlopen(request, timeout):
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr(app_module.urllib.request, "urlopen", failed_urlopen)
    response = client.post("/leads", data={
        "name": "לקוח ללא webhook",
        "phone": "050-1234567",
        "service": "פרגולה",
        "contact_consent": "on",
    })
    assert response.status_code == 302
    with client.application.app_context():
        from app import Lead
        saved = db.session.execute(
            db.select(Lead).where(Lead.name == "לקוח ללא webhook")
        ).scalar_one()
        assert saved.service == "פרגולה"



def test_webhook_is_not_called_without_shared_secret(monkeypatch, client):
    import app as app_module
    monkeypatch.setenv("LEAD_WEBHOOK_URL", "https://automation.example.test/lead")
    monkeypatch.delenv("LEAD_WEBHOOK_SECRET", raising=False)

    def unexpected_urlopen(request, timeout):
        raise AssertionError("Webhook must not be called without a shared secret")

    monkeypatch.setattr(app_module.urllib.request, "urlopen", unexpected_urlopen)
    response = client.post("/leads", data={
        "name": "לקוח ללא סוד",
        "phone": "050-1234567",
        "service": "דק",
        "contact_consent": "on",
    })
    assert response.status_code == 302

def test_production_requires_explicit_secret_and_database(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="SECRET_KEY must be configured"):
        create_app()

    monkeypatch.setenv("SECRET_KEY", "production-test-secret")
    with pytest.raises(RuntimeError, match="DATABASE_URL must be configured"):
        create_app()

    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "production-test-secret",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'production-test.db'}",
    })
    assert app.config["SESSION_COOKIE_SECURE"] is True
    with app.app_context():
        db.drop_all()



def _remote_address_test_app(tmp_path, trust_proxy_headers):
    from flask import jsonify, request

    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'proxy.db'}",
        "RATELIMIT_ENABLED": False,
        "TRUST_PROXY_HEADERS": trust_proxy_headers,
    })
    app.add_url_rule(
        "/_test/remote-address",
        "test_remote_address",
        lambda: jsonify(remote_addr=request.remote_addr),
    )
    return app


def test_forwarded_ip_is_ignored_when_proxy_trust_is_disabled(tmp_path):
    app = _remote_address_test_app(tmp_path, False)
    with app.test_client() as client:
        response = client.get(
            "/_test/remote-address",
            headers={"X-Forwarded-For": "198.51.100.24"},
            environ_overrides={"REMOTE_ADDR": "10.0.0.8"},
        )
    assert response.get_json()["remote_addr"] == "10.0.0.8"
    with app.app_context():
        db.drop_all()


def test_render_proxy_fix_uses_original_client_ip_from_single_trusted_hop(tmp_path):
    app = _remote_address_test_app(tmp_path, True)
    with app.test_client() as client:
        response = client.get(
            "/_test/remote-address",
            headers={"X-Forwarded-For": "198.51.100.24"},
            environ_overrides={"REMOTE_ADDR": "10.0.0.8"},
        )
    assert response.get_json()["remote_addr"] == "198.51.100.24"
    with app.app_context():
        db.drop_all()
