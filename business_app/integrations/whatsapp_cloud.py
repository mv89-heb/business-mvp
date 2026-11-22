"""WhatsApp is intentionally a disabled integration boundary."""
def is_configured(integration_config, env=None):
    import os
    values = env if env is not None else os.environ
    return bool(integration_config.get("enabled", False) and values.get("WHATSAPP_ENABLED", "").lower() == "true" and values.get("WHATSAPP_PHONE_NUMBER_ID") and values.get("WHATSAPP_ACCESS_TOKEN"))

def send_message(*args, **kwargs):
    raise RuntimeError("WhatsApp sending is not implemented or enabled. Obtain explicit business-owner authorization and official Cloud API credentials first.")
