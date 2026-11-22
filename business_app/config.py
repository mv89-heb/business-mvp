"""Centralized, validated business configuration."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "business.json"
STARTER_CONFIG_PATH = ROOT / "config" / "business.template.json"

class BusinessConfigError(ValueError):
    pass

def load_business_config(path=None):
    explicit_path = path or os.getenv("BUSINESS_CONFIG_PATH")
    config_path = Path(explicit_path) if explicit_path else DEFAULT_CONFIG_PATH
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    if not explicit_path and not config_path.is_file():
        config_path = STARTER_CONFIG_PATH
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BusinessConfigError(f"Business configuration not found: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise BusinessConfigError(f"Invalid JSON in business configuration: {config_path}") from exc
    required = {"id": str, "name": str, "tagline": str, "locale": dict, "branding": dict, "contact": dict, "modules": dict, "integrations": dict, "content": dict}
    for key, expected in required.items():
        if not isinstance(data.get(key), expected):
            raise BusinessConfigError(f"Configuration field '{key}' must be {expected.__name__}")
    if not data["id"].strip() or not data["name"].strip():
        raise BusinessConfigError("Business id and name must not be empty")
    for key in ("language", "direction"):
        if not isinstance(data["locale"].get(key), str) or not data["locale"][key].strip():
            raise BusinessConfigError(f"locale.{key} is required")
    if not isinstance(data["content"].get("services"), list):
        raise BusinessConfigError("content.services must be a list")
    for name, integration in data["integrations"].items():
        if not isinstance(integration, dict) or not isinstance(integration.get("enabled", False), bool):
            raise BusinessConfigError(f"Integration '{name}' must have an enabled boolean")
    return data

def validate_runtime_environment(env=None):
    values = env if env is not None else os.environ
    missing = []
    if not values.get("SECRET_KEY"):
        missing.append("SECRET_KEY")
    if not values.get("DATABASE_URL"):
        missing.append("DATABASE_URL")
    if not (values.get("ADMIN_PASSWORD") or values.get("ADMIN_PASSWORD_HASH")):
        missing.append("ADMIN_PASSWORD or ADMIN_PASSWORD_HASH")
    return missing
