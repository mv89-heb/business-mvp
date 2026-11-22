import os
import pytest
from app import create_app, db


@pytest.fixture()
def client(tmp_path):
    os.environ["ADMIN_PASSWORD"] = "test-admin-password"
    app = create_app({
        "TESTING": True,
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
    assert "דקים" in html
    assert "פרגולות" in html
    assert "חיפויי עץ" in html
    assert "תיקונים וחידוש" in html


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
