from business_app.provisioning_web import create_provisioning_app


def make_client():
    app = create_provisioning_app({
        "TESTING": True,
        "SECRET_KEY": "test-session-secret",
        "PROVISIONING_PASSWORD": "developer-test-password",
        "WTF_CSRF_ENABLED": False,
    })
    return app.test_client()


def test_web_wizard_requires_developer_login():
    client = make_client()
    response = client.get("/wizard")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_web_wizard_rejects_invalid_password():
    client = make_client()
    response = client.post("/login", data={"password": "wrong"})
    assert response.status_code == 200
    assert "סיסמה שגויה" in response.get_data(as_text=True)


def test_web_wizard_generates_plan_after_login_without_creating_resources():
    client = make_client()
    login = client.post("/login", data={"password": "developer-test-password"})
    assert login.status_code == 302

    response = client.post("/wizard", data={
        "business_name": "עסק בדיקה",
        "slug": "test-business",
        "category": "services",
        "modules": ["lead_capture", "lead_management"],
    })
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '"mode": "dry_run"' in page
    assert "No provider API was called" in page
    assert "test-business" in page


def test_web_wizard_rejects_unimplemented_modules():
    client = make_client()
    client.post("/login", data={"password": "developer-test-password"})
    response = client.post("/wizard", data={
        "business_name": "עסק בדיקה",
        "slug": "test-business",
        "category": "services",
        "modules": ["inventory"],
    })
    assert "not implemented" in response.get_data(as_text=True)


def test_logout_removes_developer_session():
    client = make_client()
    client.post("/login", data={"password": "developer-test-password"})
    response = client.post("/logout")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    assert client.get("/wizard").status_code == 302
