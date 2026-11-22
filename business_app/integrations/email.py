"""Email integration placeholder; no provider or sending is enabled."""
def is_enabled(integration_config):
    return bool(integration_config.get("enabled", False))
