import json
import subprocess
import sys
import pytest
from business_app.bootstrap import bootstrap
from business_app.config import BusinessConfigError, load_business_config, validate_runtime_environment

def test_current_business_configuration_and_integration_safety():
    config = load_business_config()
    assert config["id"]
    assert isinstance(config["name"], str) and config["name"]
    assert isinstance(config["integrations"]["google_sheets_webhook"]["enabled"], bool)
    assert config["integrations"]["whatsapp_cloud_api"]["enabled"] is False

def test_configuration_override(tmp_path):
    data = load_business_config()
    data["name"] = "עסק בדיקה"
    path = tmp_path / "business.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert load_business_config(path)["name"] == "עסק בדיקה"

def test_invalid_configuration_fails_fast(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"id":"x"}', encoding="utf-8")
    with pytest.raises(BusinessConfigError):
        load_business_config(path)

def test_bootstrap_creates_isolated_defaults_and_refuses_overwrite(tmp_path):
    target = tmp_path / "customer"
    result = bootstrap(target, "עסק חדש", "new-business")
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["name"] == "עסק חדש"
    assert data["integrations"]["google_sheets_webhook"]["enabled"] is False
    assert data["integrations"]["whatsapp_cloud_api"]["enabled"] is False
    with pytest.raises(FileExistsError):
        bootstrap(target, "לא לדרוס")

def test_bootstrap_rejects_unsafe_slug(tmp_path):
    with pytest.raises(ValueError):
        bootstrap(tmp_path / "bad", slug="../bad")

def test_environment_check_reports_missing_names_only():
    assert validate_runtime_environment({}) == ["SECRET_KEY", "DATABASE_URL", "ADMIN_PASSWORD_HASH"]

def test_whatsapp_sender_is_not_available():
    from business_app.integrations.whatsapp_cloud import send_message
    with pytest.raises(RuntimeError):
        send_message("+972500000000", "template")

def test_clean_export_excludes_live_customer_configuration_and_developer_tools(tmp_path):
    from business_app.export_starter import export_starter

    target = tmp_path / "clean-starter"
    exported = export_starter(target, "עסק בדיקה", "test-business")
    config_path = exported / "config" / "business.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert config["id"] == "test-business"
    assert config["name"] == "עסק בדיקה"
    assert config["integrations"]["google_sheets_webhook"]["enabled"] is False
    assert (exported / "render.yaml").is_file()
    assert "replace-with-customer-service-name" in (exported / "render.yaml").read_text(encoding="utf-8")
    assert not (exported / ".git").exists()
    assert "050-720-8386" not in config_path.read_text(encoding="utf-8")
    assert "eli-cohen-wood-construction" not in config_path.read_text(encoding="utf-8")
    assert "eli-cohen-business-mvp-iqem" not in (exported / "render.yaml").read_text(encoding="utf-8")
    render_template = (exported / "render.yaml").read_text(encoding="utf-8")
    assert "ADMIN_PASSWORD_HASH" in render_template
    assert "key: ADMIN_PASSWORD\n" not in render_template

    # The developer wizard, provider provisioning adapters and their private docs/tests
    # belong only in the source repository, never in a customer clone.
    assert not (exported / "business_app" / "provisioning").exists()
    assert not (exported / "business_app" / "provisioning_web.py").exists()
    assert not (exported / "templates" / "provisioning_wizard.html").exists()
    assert not (exported / "docs").exists()
    assert not (exported / "business_app" / "export_starter.py").exists()
    assert not (exported / "templates" / "business_studio.html").exists()
    assert not (exported / "static" / "business-studio.js").exists()
    assert not (exported / "static" / "css" / "provisioning.css").exists()
    assert not (exported / "static" / "css" / "wizard-premium.css").exists()
    assert not (exported / "static" / "js" / "provisioning.js").exists()
    assert not (exported / ".github" / "workflows" / "studio-command.yml").exists()
    assert not (exported / "RUN-BUSINESS-STARTER.ps1").exists()
    assert not (exported / "tests" / "test_provisioning.py").exists()
    assert not (exported / "tests" / "test_provisioning_web.py").exists()
    assert not (exported / "tests" / "test_provisioning_workflow.py").exists()
    assert not (exported / "tests" / "test_provider_adapters.py").exists()
    assert (exported / "tests" / "test_app.py").is_file()
    assert (exported / "tests" / "test_customer_starter.py").is_file()
    assert (exported / "tests" / "test_lead_integrations.py").is_file()
    assert (exported / "tests" / "test_whatsapp_cloud.py").is_file()
    assert not (exported / "tests" / "test_business_studio.py").exists()
    assert not (exported / "tests" / "test_handover_generator.py").exists()
    assert not (exported / "tests" / "test_provider_connections.py").exists()
    assert not (exported / "tests" / "test_provider_adapters.py").exists()

    workflow = (exported / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "python -m pytest -q" in workflow
    assert "powershell-syntax" not in workflow
    assert "studio-command" not in workflow

    # Run the actual customer repository test suite, not a source-repository approximation.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=exported,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_clean_export_refuses_nonempty_destination(tmp_path):
    from business_app.export_starter import export_starter

    target = tmp_path / "not-empty"
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        export_starter(target, "עסק בדיקה", "test-business")
    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_clean_export_refuses_destination_inside_source_repository(tmp_path):
    from business_app.export_starter import export_starter
    from business_app.export_starter import ROOT

    with pytest.raises(ValueError):
        export_starter(ROOT / "unsafe-export", "עסק בדיקה", "test-business")

def test_clean_export_refuses_source_parent_destination():
    from business_app.export_starter import export_starter, ROOT

    with pytest.raises(ValueError):
        export_starter(ROOT.parent, "עסק בדיקה", "test-business")



def test_export_ignore_rules_exclude_secrets_and_runtime_state_but_keep_example_env():
    from business_app.export_starter import _ignore_export_artifacts

    names = [
        ".env", ".env.production", ".env.example", ".envrc", ".netrc",
        "credentials.json", "service-account.json", "private.pem", "signing.key",
        "customer.sqlite", "customer.db", "application.log", ".provisioning-state",
        ".business-app-provisioner", "uploads", "provisioning", "provisioning_web.py",
        "README.md", "app.py",
    ]

    ignored = _ignore_export_artifacts(".", names)

    assert ".env.example" not in ignored
    for sensitive in names:
        if sensitive not in {".env.example", "README.md", "app.py"}:
            assert sensitive in ignored


def test_export_rejects_symlinks_that_could_escape_source_tree(tmp_path):
    from business_app.export_starter import _validate_export_tree

    source = tmp_path / "source"
    app_dir = source / "business_app"
    app_dir.mkdir(parents=True)
    outside_secret = tmp_path / "outside-secret.txt"
    outside_secret.write_text("must never be copied", encoding="utf-8")
    link = app_dir / "external-secret.txt"
    try:
        link.symlink_to(outside_secret)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is not available on this platform")

    with pytest.raises(ValueError, match="symbolic link"):
        _validate_export_tree(source)



@pytest.mark.parametrize("relative", [
    "config/business.template.json",
    "render.yaml.template",
    "starter/README.md",
])
def test_export_rejects_symlinks_in_all_separately_copied_sources(tmp_path, relative):
    from business_app.export_starter import _validate_export_tree

    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("not for export", encoding="utf-8")
    target = source / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is not available on this platform")

    with pytest.raises(ValueError, match="symbolic link"):
        _validate_export_tree(source)


@pytest.mark.parametrize(
    "key,value",
    [
        ("primary_color", "red; background:url(https://attacker.test)"),
        ("accent_color", "#123"),
        ("theme_color", "javascript:alert(1)"),
        ("font_family", "Arial; background:red"),
    ],
)
def test_branding_configuration_rejects_unsafe_css_values(tmp_path, key, value):
    data = load_business_config()
    data["branding"][key] = value
    path = tmp_path / "unsafe-brand.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(BusinessConfigError):
        load_business_config(path)


def test_selected_brand_font_is_used_by_customer_site_template():
    from pathlib import Path
    from business_app.export_starter import ROOT

    base_template = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    stylesheet = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert "business.branding.font_family" in base_template
    assert "--brand-font:'{{ business.branding.font_family }}'" in base_template
    assert "font-family:var(--brand-font,'Heebo')" in stylesheet


def test_exported_starter_defaults_are_domain_neutral():
    from business_app.config import STARTER_CONFIG_PATH, load_business_config

    config = load_business_config(STARTER_CONFIG_PATH)
    assert config["modules"]["estimate_calculator"] is False
    assert config["modules"]["area_capture"] is False
    assert config["id"] == "replace-with-unique-business-slug"
