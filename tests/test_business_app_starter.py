import json
import pytest
from business_app.bootstrap import bootstrap
from business_app.config import BusinessConfigError, load_business_config, validate_runtime_environment

def test_current_business_configuration_and_integration_safety():
    config = load_business_config()
    assert config["id"] == "eli-cohen-wood-construction"
    assert config["integrations"]["google_sheets_webhook"]["enabled"] is True
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
