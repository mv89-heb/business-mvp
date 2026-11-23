import json
import re

from business_app.provisioning_web import create_provisioning_app


def make_client(audit_path=None):
    config = {
        "TESTING": True,
        "SECRET_KEY": "test-session-secret-that-is-at-least-32-chars",
        "PROVISIONING_PASSWORD": "developer-test-password",
        "WTF_CSRF_ENABLED": False,
    }
    if audit_path is not None:
        config["PROVISIONING_AUDIT_PATH"] = str(audit_path)
    app = create_provisioning_app(config)
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



def test_connection_check_preserves_wizard_values_and_returns_to_infrastructure(monkeypatch):
    import business_app.provisioning_web as provisioning_web

    class ConnectedProvider:
        def check_connection(self):
            return {"provider": "test", "connected": True}

    monkeypatch.setattr(provisioning_web, "GitHubProvider", ConnectedProvider)
    monkeypatch.setattr(provisioning_web, "NeonProvider", ConnectedProvider)
    monkeypatch.setattr(provisioning_web, "RenderProvider", ConnectedProvider)

    client = make_client()
    client.post("/login", data={"password": "developer-test-password"})
    response = client.post("/connections/check", data={
        "business_name": "עסק בדיקה",
        "slug": "test-business",
        "category": "services",
        "modules": ["lead_capture", "lead_management"],
        "tagline": "פתרונות מקצועיים",
        "phone": "050-123-4567",
        "whatsapp_number": "050-123-4567",
        "address": "רחוב הדוגמה 12",
        "service_area": "בית שמש והסביבה",
        "primary_color": "#123456",
        "accent_color": "#abcdef",
        "font_family": "Rubik",
        "services": "פרגולות, דקים",
    })

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'data-initial-step="3"' in page
    assert 'value="עסק בדיקה"' in page
    assert 'value="test-business"' in page
    assert 'value="פתרונות מקצועיים"' in page
    assert 'value="רחוב הדוגמה 12"' in page
    assert 'value="פרגולות, דקים"' in page
    assert "הבדיקה הושלמה" in page
    assert "מחובר" in page



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


def test_wizard_carries_brand_profile_into_the_dry_run_plan():
    client = make_client()
    client.post("/login", data={"password": "developer-test-password"})
    response = client.post("/wizard", data={
        "business_name": "עסק בדיקה",
        "slug": "test-business",
        "category": "services",
        "modules": ["lead_capture", "lead_management"],
        "tagline": "פתרונות מקצועיים לבית",
        "phone": "050-123-4567",
        "whatsapp_number": "050-123-4567",
        "address": "רחוב הדוגמה 12",
        "service_area": "בית שמש והסביבה",
        "primary_color": "#123456",
        "accent_color": "#abcdef",
        "font_family": "Rubik",
        "services": "פרגולות, דקים",
    })
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'name="tagline" value="פתרונות מקצועיים לבית"' in page
    assert '"whatsapp_url": "https://wa.me/972501234567"' in page
    assert 'value="פרגולות, דקים"' in page


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


def test_wizard_sets_security_headers_and_disables_caching():
    client = make_client()
    response = client.get("/login")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"] == "no-store"


def test_audit_log_records_auth_events_without_passwords(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    client = make_client(audit_path)
    client.post("/login", data={"password": "wrong-secret-value"})
    client.post("/login", data={"password": "developer-test-password"})
    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert [record["event"] for record in records] == ["login_failure", "login_success"]
    serialized = audit_path.read_text(encoding="utf-8")
    assert "wrong-secret-value" not in serialized
    assert "developer-test-password" not in serialized


def test_execution_preserves_brand_profile_after_preflight(monkeypatch):
    import business_app.provisioning_web as provisioning_web
    from business_app.provisioning import build_provisioning_plan
    from business_app.provisioning_web import _plan_fingerprint

    captured = {}

    class FakeWorkflow:
        def run(self, plan, **kwargs):
            captured["plan"] = plan
            return {"workflow_id": "profile-plan", "status": "completed"}

    monkeypatch.setattr(provisioning_web, "ProvisioningWorkflow", FakeWorkflow)
    monkeypatch.setenv("GITHUB_OWNER", "developer")
    profile = {
        "business_name": "עסק בדיקה",
        "slug": "test-business",
        "category": "services",
        "modules": ["lead_capture", "lead_management"],
        "tagline": "פתרונות מקצועיים לבית",
        "phone": "050-123-4567",
        "whatsapp_number": "050-123-4567",
        "address": "רחוב הדוגמה 12",
        "service_area": "בית שמש והסביבה",
        "primary_color": "#123456",
        "accent_color": "#abcdef",
        "font_family": "Rubik",
        "services": ["פרגולות", "דקים"],
    }
    plan = build_provisioning_plan(**profile)
    client = make_client()
    client.post("/login", data={"password": "developer-test-password"})
    with client.session_transaction() as session:
        session["preflight_fingerprint"] = _plan_fingerprint(plan)

    response = client.post("/execute", data={
        **{key: value for key, value in profile.items() if key != "services"},
        "services": "פרגולות, דקים",
        "approval": "CREATE",
        "confirm_zero_cost": "yes",
        "admin_password": "a-very-long-test-password",
    })

    assert response.status_code == 200
    assert "PROVISIONING RESULT" in response.get_data(as_text=True)
    assert captured["plan"]["business"]["tagline"] == profile["tagline"]
    assert captured["plan"]["business"]["contact"]["phone_href"] == "tel:0501234567"
    assert captured["plan"]["business"]["contact"]["whatsapp_url"] == "https://wa.me/972501234567"
    assert captured["plan"]["business"]["branding"]["font_family"] == "Rubik"
    assert captured["plan"]["business"]["services"] == ["פרגולות", "דקים"]



def test_remote_deployment_requires_exact_host_https_and_secure_cookie():
    config = {
        "TESTING": True,
        "SECRET_KEY": "remote-session-secret-that-is-at-least-32-characters",
        "PROVISIONING_PASSWORD": "a-remote-password-with-at-least-32-characters",
        "PROVISIONING_PUBLIC_ACCESS": True,
        "PROVISIONING_ALLOWED_HOSTS": ["wizard.example.test"],
        "WTF_CSRF_ENABLED": False,
    }
    app = create_provisioning_app(config)
    client = app.test_client()
    response = client.get("/login", base_url="https://wizard.example.test")
    assert response.status_code == 200
    login = client.post(
        "/login",
        data={"password": config["PROVISIONING_PASSWORD"]},
        base_url="https://wizard.example.test",
    )
    cookie = login.headers.get("Set-Cookie", "")
    assert login.status_code == 302
    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert login.headers["Strict-Transport-Security"].startswith("max-age=31536000")


def test_remote_deployment_rejects_unapproved_host_and_plain_http():
    config = {
        "TESTING": True,
        "SECRET_KEY": "remote-session-secret-that-is-at-least-32-characters",
        "PROVISIONING_PASSWORD": "a-remote-password-with-at-least-32-characters",
        "PROVISIONING_PUBLIC_ACCESS": True,
        "PROVISIONING_ALLOWED_HOSTS": ["wizard.example.test"],
        "WTF_CSRF_ENABLED": False,
    }
    client = create_provisioning_app(config).test_client()
    assert client.get("/login", base_url="https://attacker.example").status_code == 404
    assert client.get("/login", base_url="http://wizard.example.test").status_code == 404


def test_remote_deployment_requires_32_character_password_and_allowed_host():
    import pytest

    base_config = {
        "TESTING": True,
        "SECRET_KEY": "remote-session-secret-that-is-at-least-32-characters",
        "PROVISIONING_PASSWORD": "too-short-password",
        "PROVISIONING_PUBLIC_ACCESS": True,
        "PROVISIONING_ALLOWED_HOSTS": ["wizard.example.test"],
    }
    with pytest.raises(RuntimeError, match="at least 32 characters"):
        create_provisioning_app(base_config)
    base_config["PROVISIONING_PASSWORD"] = "a-remote-password-with-at-least-32-characters"
    base_config["PROVISIONING_ALLOWED_HOSTS"] = []
    with pytest.raises(RuntimeError, match="exact public host"):
        create_provisioning_app(base_config)


def test_healthz_is_public_and_does_not_disclose_configuration():
    client = make_client()
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}



def test_remote_login_accepts_a_valid_csrf_token_with_seconds_time_limit():
    config = {
        "TESTING": True,
        "SECRET_KEY": "remote-session-secret-that-is-at-least-32-characters",
        "PROVISIONING_PASSWORD": "a-remote-password-with-at-least-32-characters",
        "PROVISIONING_PUBLIC_ACCESS": True,
        "PROVISIONING_ALLOWED_HOSTS": ["wizard.example.test"],
    }
    client = create_provisioning_app(config).test_client()
    page = client.get("/login", base_url="https://wizard.example.test")
    assert page.status_code == 200
    assert page.headers["Referrer-Policy"] == "same-origin"
    token = re.search(
        r'name="csrf_token" value="([^"]+)"',
        page.get_data(as_text=True),
    )
    assert token is not None

    response = client.post(
        "/login",
        data={
            "password": config["PROVISIONING_PASSWORD"],
            "csrf_token": token.group(1),
        },
        headers={"Referer": "https://wizard.example.test/login"},
        base_url="https://wizard.example.test",
    )
    assert response.status_code == 302, response.get_data(as_text=True)
    assert response.headers["Location"].endswith("/wizard")


def test_remote_deployment_keeps_csrf_protection_enabled_by_default():
    config = {
        "TESTING": True,
        "SECRET_KEY": "remote-session-secret-that-is-at-least-32-characters",
        "PROVISIONING_PASSWORD": "a-remote-password-with-at-least-32-characters",
        "PROVISIONING_PUBLIC_ACCESS": True,
        "PROVISIONING_ALLOWED_HOSTS": ["wizard.example.test"],
    }
    client = create_provisioning_app(config).test_client()
    page = client.get("/login", base_url="https://wizard.example.test")
    assert page.status_code == 200
    rejected = client.post(
        "/login",
        data={"password": config["PROVISIONING_PASSWORD"]},
        base_url="https://wizard.example.test",
    )
    assert rejected.status_code == 400



def test_developer_studio_layout_and_stylesheet_render_for_authenticated_user():
    client = make_client()
    client.post("/login", data={"password": "developer-test-password"})
    response = client.get("/wizard")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Business Studio" in page
    assert "מרכז הפיתוח שלך" in page
    assert 'href="#business-profile"' in page
    assert 'href="#brand-profile"' in page
    assert 'href="#modules"' in page
    assert 'href="#infrastructure"' in page
    assert 'href="#plan-review"' in page
    assert "css/provisioning.css" in page

    stylesheet = client.get("/static/css/provisioning.css")
    assert stylesheet.status_code == 200
    css = stylesheet.get_data(as_text=True)
    assert ".workspace" in css
    assert "@media(max-width:760px)" in css
    assert ".status-chip" in css



def test_provisioning_wizard_has_four_validated_steps_and_serves_navigation_script():
    client = make_client()
    client.post("/login", data={"password": "developer-test-password"})
    response = client.get("/wizard")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    for step in range(4):
        assert f'data-wizard-step="{step}"' in page
        assert f'data-step-label="{step}"' in page
    assert 'data-wizard-prev' in page
    assert 'data-wizard-next' in page
    assert 'data-wizard-submit' in page

    script = client.get("/static/js/provisioning.js")
    assert script.status_code == 200
    script_text = script.get_data(as_text=True)
    assert "validateCurrent" in script_text
    assert "reportValidity" in script_text
    assert "data-wizard-step" in script_text



def test_provider_cards_reflect_connection_check_results(monkeypatch):
    import business_app.provisioning_web as provisioning_web

    class ConnectedProvider:
        def check_connection(self):
            return {"provider": "test", "status": "ok"}

    monkeypatch.setattr(provisioning_web, "GitHubProvider", ConnectedProvider)
    monkeypatch.setattr(provisioning_web, "NeonProvider", ConnectedProvider)
    monkeypatch.setattr(provisioning_web, "RenderProvider", ConnectedProvider)

    client = make_client()
    client.post("/login", data={"password": "developer-test-password"})
    response = client.post("/connections/check")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "הבדיקה הושלמה" in page
    assert page.count("מחובר") == 3
    assert "לא נבדק" not in page




def test_healthz_reports_unavailable_when_configured_database_is_down(monkeypatch):
    import business_app.provisioning.workflow as workflow_module

    def unavailable(self):
        raise RuntimeError("database unavailable")

    monkeypatch.setenv("DATABASE_URL", "postgresql://test-user:test-password@localhost/testdb")
    monkeypatch.setattr(workflow_module.WorkflowStateStore, "check_connection", unavailable)

    response = make_client().get("/healthz")
    assert response.status_code == 503
    assert response.get_json() == {"status": "unavailable"}


def test_audit_events_use_postgres_when_database_is_configured(tmp_path, monkeypatch):
    import business_app.provisioning.workflow as workflow_module

    captured = []

    def capture(self, event, actor, details):
        captured.append((event, actor, details))

    monkeypatch.setenv("DATABASE_URL", "postgresql://test-user:test-password@localhost/testdb")
    monkeypatch.setattr(workflow_module.WorkflowStateStore, "append_audit_event", capture)
    audit_path = tmp_path / "local-audit.jsonl"
    client = make_client(audit_path)
    client.post("/login", data={"password": "wrong-secret-value"})

    assert captured[0][0] == "login_failure"
    assert captured[0][1] == "developer"
    assert captured[0][2]["reason"] == "invalid_password"
    assert not audit_path.exists()



def test_storage_round_trip_route_requires_login_and_preserves_form_values(monkeypatch):
    import business_app.provisioning.workflow as workflow_module

    calls = []

    def successful_round_trip(self):
        calls.append(True)
        return True

    monkeypatch.setenv("DATABASE_URL", "postgresql://test-user:test-password@localhost/testdb")
    monkeypatch.setattr(workflow_module.WorkflowStateStore, "check_round_trip", successful_round_trip)
    client = make_client()

    denied = client.post("/storage/check", data={"business_name": "לא אמור להישמר"})
    assert denied.status_code == 302
    assert calls == []

    client.post("/login", data={"password": "developer-test-password"})
    response = client.post("/storage/check", data={
        "business_name": "עסק בדיקה",
        "slug": "test-business",
        "category": "services",
        "modules": ["lead_capture"],
    })
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert calls == [True]
    assert "בדיקת Neon הצליחה" in page
    assert 'value="עסק בדיקה"' in page



def test_remote_render_loopback_head_probe_returns_200():
    config = {
        "TESTING": True,
        "SECRET_KEY": "remote-session-secret-that-is-at-least-32-characters",
        "PROVISIONING_PASSWORD": "a-remote-password-with-at-least-32-characters",
        "PROVISIONING_PUBLIC_ACCESS": True,
        "PROVISIONING_ALLOWED_HOSTS": ["wizard.example.test"],
        "WTF_CSRF_ENABLED": False,
    }
    client = create_provisioning_app(config).test_client()
    response = client.head("/", base_url="http://127.0.0.1")
    assert response.status_code == 200
    assert response.data == b""


def test_remote_healthz_is_public_but_https_only_for_non_loopback_clients():
    config = {
        "TESTING": True,
        "SECRET_KEY": "remote-session-secret-that-is-at-least-32-characters",
        "PROVISIONING_PASSWORD": "a-remote-password-with-at-least-32-characters",
        "PROVISIONING_PUBLIC_ACCESS": True,
        "PROVISIONING_ALLOWED_HOSTS": ["wizard.example.test"],
        "WTF_CSRF_ENABLED": False,
    }
    client = create_provisioning_app(config).test_client()
    public_peer = {"REMOTE_ADDR": "198.51.100.24"}
    secure = client.get(
        "/healthz", base_url="https://unlisted.example", environ_base=public_peer
    )
    insecure = client.get(
        "/healthz", base_url="http://unlisted.example", environ_base=public_peer
    )
    assert secure.status_code == 200
    assert secure.get_json() == {"status": "ok"}
    assert insecure.status_code == 404


def test_handover_download_requires_developer_login():
    client = make_client()
    response = client.post("/handover/download", data={"workflow_id": "a" * 20})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_handover_download_returns_zip_for_completed_verified_workflow(monkeypatch):
    import zipfile
    import io
    import business_app.provisioning_web as provisioning_web

    state = {
        "workflow_id": "a" * 20,
        "plan_id": "a" * 20,
        "business_slug": "sample-business",
        "business_name": "עסק לדוגמה",
        "modules": ["lead_capture", "lead_management"],
        "status": "completed",
        "resources": {
            "github": {
                "repository_id": 123,
                "repository": "customer/sample-business",
                "url": "https://github.com/customer/sample-business",
            },
            "neon": {"project_id": "neon-123", "project_name": "sample-business"},
            "render": {
                "service_id": "srv-123",
                "service_name": "sample-business",
                "url": "https://sample-business.onrender.com",
                "health_url": "https://sample-business.onrender.com/health",
            },
        },
    }

    class FakeStore:
        def __init__(self, database_url=None):
            self.database_url = database_url

        def load(self, workflow_id):
            assert workflow_id == "a" * 20
            return state

    monkeypatch.setattr(provisioning_web, "WorkflowStateStore", FakeStore)
    client = make_client()
    client.post("/login", data={"password": "developer-test-password"})
    response = client.post("/handover/download", data={"workflow_id": "a" * 20})

    assert response.status_code == 200
    assert response.mimetype == "application/zip"
    assert "sample-business-handover.zip" in response.headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        assert set(archive.namelist()) == {"HANDOVER_GUIDE.md", "TECHNICAL_INVENTORY.json"}
        assert "עסק לדוגמה" in archive.read("HANDOVER_GUIDE.md").decode("utf-8")


def test_handover_download_refuses_incomplete_workflow(monkeypatch):
    import business_app.provisioning_web as provisioning_web

    class FakeStore:
        def __init__(self, database_url=None):
            pass

        def load(self, workflow_id):
            return {"status": "running", "business_slug": "sample-business", "resources": {}}

    monkeypatch.setattr(provisioning_web, "WorkflowStateStore", FakeStore)
    client = make_client()
    client.post("/login", data={"password": "developer-test-password"})
    response = client.post("/handover/download", data={"workflow_id": "a" * 20})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/wizard")
