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

def test_connection_check_is_developer_only():
    client = make_client()
    response = client.post("/connections/check")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_connection_check_reports_missing_credentials_without_exposing_secrets(monkeypatch):
    for key in ("GITHUB_TOKEN", "NEON_API_KEY", "RENDER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    client = make_client()
    client.post("/login", data={"password": "developer-test-password"})
    response = client.post("/connections/check")
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "GITHUB" in page and "NEON" in page and "RENDER" in page
    assert "credential is not configured" in page
    assert "developer-test-password" not in page


def test_execution_route_requires_developer_login():
    client = make_client()
    response = client.post("/execute", data={})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_execution_route_requires_explicit_zero_cost_confirmation(monkeypatch):
    import business_app.provisioning_web as provisioning_web

    class NeverRunWorkflow:
        def __init__(self):
            raise AssertionError("workflow must not run without zero-cost confirmation")

    monkeypatch.setattr(provisioning_web, "ProvisioningWorkflow", NeverRunWorkflow)
    client = make_client()
    client.post("/login", data={"password": "developer-test-password"})
    response = client.post("/execute", data={
        "business_name": "עסק בדיקה",
        "slug": "test-business",
        "category": "services",
        "modules": ["lead_capture", "lead_management"],
        "approval": "CREATE",
        "admin_password": "a-very-long-test-password",
    })
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "עלות Neon תהיה אפס" in page
    assert "a-very-long-test-password" not in page


def test_execution_route_runs_workflow_only_after_explicit_approval(monkeypatch):
    import business_app.provisioning_web as provisioning_web

    captured = {}

    class FakeWorkflow:
        def run(self, plan, **kwargs):
            captured["plan"] = plan
            captured.update(kwargs)
            return {
                "workflow_id": "plan-123",
                "status": "completed",
                "resources": {"github": {"repository": "developer/test-business"}},
            }

    monkeypatch.setattr(provisioning_web, "ProvisioningWorkflow", FakeWorkflow)
    monkeypatch.setenv("GITHUB_OWNER", "developer")
    client = make_client()
    client.post("/login", data={"password": "developer-test-password"})
    response = client.post("/execute", data={
        "business_name": "עסק בדיקה",
        "slug": "test-business",
        "category": "services",
        "modules": ["lead_capture", "lead_management"],
        "approval": "CREATE",
        "confirm_zero_cost": "yes",
        "admin_password": "a-very-long-test-password",
    })
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Provisioning Result" in page
    assert captured["approval"] == "CREATE"
    assert captured["github_owner"] == "developer"
    assert captured["neon_free_tier_verified"] is True
    assert captured["admin_password"] == "a-very-long-test-password"
    assert captured["plan"]["business"]["slug"] == "test-business"
    assert "a-very-long-test-password" not in page
