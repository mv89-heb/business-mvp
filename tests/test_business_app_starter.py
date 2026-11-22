import json
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
    assert validate_runtime_environment({}) == ["SECRET_KEY", "DATABASE_URL", "ADMIN_PASSWORD or ADMIN_PASSWORD_HASH"]

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
    assert not (exported / "docs" / "DEVELOPER_PROVISIONING_WIZARD_HE.md").exists()
    assert not (exported / "docs" / "PROVIDER_INTEGRATIONS_HE.md").exists()
    assert not (exported / "tests" / "test_provisioning.py").exists()
    assert not (exported / "tests" / "test_provisioning_web.py").exists()
    assert not (exported / "tests" / "test_provisioning_workflow.py").exists()
    assert not (exported / "tests" / "test_provider_adapters.py").exists()
    assert (exported / "tests" / "test_app.py").is_file()
    assert (exported / "tests" / "test_business_app_starter.py").is_file()


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
