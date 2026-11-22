from business_app.provisioning_web import create_provisioning_app


def make_client():
    app = create_provisioning_app({
        "TESTING": True,
        "SECRET_KEY": "test-session-secret-that-is-at-least-32-chars",
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




def test_preflight_route_requires_developer_login():
    client = make_client()
    response = client.post("/preflight")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_preflight_route_runs_read_only_and_unlocks_execution(monkeypatch):
    import business_app.provisioning_web as provisioning_web

    class FakeWorkflow:
        def preflight(self, plan, *, github_owner):
            assert github_owner == "developer"
            return {
                "mode": "read_only_preflight",
                "read_only": True,
                "writes_performed": False,
            }

    monkeypatch.setattr(provisioning_web, "ProvisioningWorkflow", FakeWorkflow)
    monkeypatch.setenv("GITHUB_OWNER", "developer")
    client = make_client()
    client.post("/login", data={"password": "developer-test-password"})
    response = client.post("/preflight", data={
        "business_name": "עסק בדיקה",
        "slug": "test-business",
        "category": "services",
        "modules": ["lead_capture", "lead_management"],
    })
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "PREFLIGHT" in page
    assert '"writes_performed": false' in page
    with client.session_transaction() as session:
        assert session["preflight_fingerprint"]


def test_execution_route_requires_developer_login():
    client = make_client()
    response = client.post("/execute", data={})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")



def test_execution_route_cannot_bypass_preflight_or_reuse_it_for_changed_plan(monkeypatch):
    import business_app.provisioning_web as provisioning_web

    class NeverRunWorkflow:
        def __init__(self):
            raise AssertionError("execution must not start without a matching preflight")

    monkeypatch.setattr(provisioning_web, "ProvisioningWorkflow", NeverRunWorkflow)
    monkeypatch.setenv("GITHUB_OWNER", "developer")
    client = make_client()
    client.post("/login", data={"password": "developer-test-password"})
    base_data = {
        "business_name": "עסק בדיקה",
        "slug": "test-business",
        "category": "services",
        "modules": ["lead_capture", "lead_management"],
        "approval": "CREATE",
        "confirm_zero_cost": "yes",
        "admin_password": "a-very-long-test-password",
    }
    response = client.post("/execute", data=base_data)
    assert "Preflight מוצלח" in response.get_data(as_text=True)

    from business_app.provisioning_web import _plan_fingerprint
    from business_app.provisioning import build_provisioning_plan
    original = build_provisioning_plan(
        business_name="עסק בדיקה", slug="test-business", category="services",
        modules=["lead_capture", "lead_management"],
    )
    with client.session_transaction() as session:
        session["preflight_fingerprint"] = _plan_fingerprint(original)

    changed = dict(base_data)
    changed["business_name"] = "שם עסק שונה"
    response = client.post("/execute", data=changed)
    assert "Preflight מוצלח" in response.get_data(as_text=True)

    changed = dict(base_data)
    changed["modules"] = ["lead_capture"]
    response = client.post("/execute", data=changed)
    assert "Preflight מוצלח" in response.get_data(as_text=True)


def test_execution_route_requires_explicit_zero_cost_confirmation(monkeypatch):
    import business_app.provisioning_web as provisioning_web

    class NeverRunWorkflow:
        def __init__(self):
            raise AssertionError("workflow must not run without zero-cost confirmation")

    monkeypatch.setattr(provisioning_web, "ProvisioningWorkflow", NeverRunWorkflow)
    client = make_client()
    client.post("/login", data={"password": "developer-test-password"})
    from business_app.provisioning import build_provisioning_plan
    plan = build_provisioning_plan(
        business_name="עסק בדיקה", slug="test-business", category="services",
        modules=["lead_capture", "lead_management"],
    )
    with client.session_transaction() as session:
        from business_app.provisioning_web import _plan_fingerprint
        session["preflight_fingerprint"] = _plan_fingerprint(plan)
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
    from business_app.provisioning import build_provisioning_plan
    plan = build_provisioning_plan(
        business_name="עסק בדיקה", slug="test-business", category="services",
        modules=["lead_capture", "lead_management"],
    )
    from business_app.provisioning_web import _plan_fingerprint
    with client.session_transaction() as session:
        session["preflight_fingerprint"] = _plan_fingerprint(plan)
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
    assert "PROVISIONING RESULT" in page
    assert captured["approval"] == "CREATE"
    assert captured["github_owner"] == "developer"
    assert captured["neon_free_tier_verified"] is True
    assert captured["admin_password"] == "a-very-long-test-password"
    assert captured["plan"]["business"]["slug"] == "test-business"
    assert "a-very-long-test-password" not in page

    replay = client.post("/execute", data={
        "business_name": "עסק בדיקה",
        "slug": "test-business",
        "category": "services",
        "modules": ["lead_capture", "lead_management"],
        "approval": "CREATE",
        "confirm_zero_cost": "yes",
        "admin_password": "a-very-long-test-password",
    })
    assert "Preflight מוצלח" in replay.get_data(as_text=True)



def test_web_wizard_rejects_non_loopback_clients():
    client = make_client()
    response = client.get("/login", environ_base={"REMOTE_ADDR": "192.0.2.25"})
    assert response.status_code == 404


def test_web_wizard_rejects_non_loopback_host():
    client = make_client()
    response = client.get("/login", base_url="http://attacker.example")
    assert response.status_code == 404


def test_web_wizard_rate_limits_failed_login_attempts():
    client = make_client()
    responses = [
        client.post("/login", data={"password": "wrong"})
        for _ in range(6)
    ]
    assert [response.status_code for response in responses[:5]] == [200] * 5
    assert responses[5].status_code == 429


def test_web_wizard_requires_strong_startup_secrets():
    import pytest

    with pytest.raises(RuntimeError, match="at least 32 characters"):
        create_provisioning_app({
            "SECRET_KEY": "too-short",
            "PROVISIONING_PASSWORD": "developer-test-password",
        })
    with pytest.raises(RuntimeError, match="at least 16 characters"):
        create_provisioning_app({
            "SECRET_KEY": "test-session-secret-that-is-at-least-32-chars",
            "PROVISIONING_PASSWORD": "short",
        })


def test_web_wizard_session_cookie_is_http_only_and_strict():
    client = make_client()
    response = client.post("/login", data={"password": "developer-test-password"})
    cookie = response.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert "Secure" not in cookie  # Localhost HTTP is required by the local-only tool.


def test_totp_matches_rfc6238_six_digit_vector():
    from business_app.provisioning_web import _totp_code, _verify_totp

    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert _totp_code(secret, timestamp=59) == "287082"
    assert _verify_totp(secret, "287082", timestamp=59)
    assert not _verify_totp(secret, "000000", timestamp=59)


def test_hosted_mode_requires_mfa_and_exact_host(monkeypatch):
    import pytest

    monkeypatch.setenv("PROVISIONING_DEPLOYMENT_MODE", "hosted")
    with pytest.raises(RuntimeError, match="PROVISIONING_TOTP_SECRET"):
        create_provisioning_app({
            "TESTING": True,
            "SECRET_KEY": "hosted-session-secret-that-is-longer-than-32-characters",
            "PROVISIONING_PASSWORD": "hosted-developer-password-with-more-than-24",
            "PROVISIONING_ALLOWED_HOST": "studio.example.test",
        })


def test_hosted_mode_requires_https_and_exact_hostname(monkeypatch):
    monkeypatch.setenv("PROVISIONING_DEPLOYMENT_MODE", "hosted")
    app = create_provisioning_app({
        "TESTING": True,
        "SECRET_KEY": "hosted-session-secret-that-is-longer-than-32-characters",
        "PROVISIONING_PASSWORD": "hosted-developer-password-with-more-than-24",
        "PROVISIONING_TOTP_SECRET": "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
        "PROVISIONING_ALLOWED_HOST": "studio.example.test",
        "WTF_CSRF_ENABLED": False,
    })
    client = app.test_client()
    assert client.get("/login", base_url="http://studio.example.test").status_code == 400
    assert client.get("/login", base_url="https://attacker.example.test").status_code == 404
    assert client.get("/health", base_url="https://studio.example.test").status_code == 200
    login_page = client.get("/login", base_url="https://studio.example.test")
    assert login_page.status_code == 200
    assert "קוד אימות" in login_page.get_data(as_text=True)


def test_hosted_login_requires_password_and_valid_totp(monkeypatch):
    import business_app.provisioning_web as provisioning_web

    monkeypatch.setenv("PROVISIONING_DEPLOYMENT_MODE", "hosted")
    monkeypatch.setattr(provisioning_web.time, "time", lambda: 59)
    app = create_provisioning_app({
        "TESTING": True,
        "SECRET_KEY": "hosted-session-secret-that-is-longer-than-32-characters",
        "PROVISIONING_PASSWORD": "hosted-developer-password-with-more-than-24",
        "PROVISIONING_TOTP_SECRET": "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
        "PROVISIONING_ALLOWED_HOST": "studio.example.test",
        "WTF_CSRF_ENABLED": False,
    })
    client = app.test_client()
    bad = client.post("/login", base_url="https://studio.example.test", data={
        "password": "hosted-developer-password-with-more-than-24",
        "otp": "000000",
    })
    assert bad.status_code == 200
    assert "פרטי ההתחברות" in bad.get_data(as_text=True)

    good = client.post("/login", base_url="https://studio.example.test", data={
        "password": "hosted-developer-password-with-more-than-24",
        "otp": "287082",
    })
    assert good.status_code == 302
    cookie = good.headers.get("Set-Cookie", "")
    assert "__Host-devstudio=" in cookie
    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert client.get("/wizard", base_url="https://studio.example.test").status_code == 200
