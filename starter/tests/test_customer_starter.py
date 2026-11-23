import json

from business_app.bootstrap import bootstrap
from business_app.config import load_business_config


def test_customer_starter_bootstraps_without_developer_modules(tmp_path):
    config_path = bootstrap(tmp_path / "customer", "עסק בדיקה", "customer-test")
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert config["id"] == "customer-test"
    assert config["name"] == "עסק בדיקה"
    assert config["integrations"]["whatsapp_cloud_api"]["enabled"] is False
    assert load_business_config(config_path)["id"] == "customer-test"
