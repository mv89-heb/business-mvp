"""Existing Google Apps Script webhook adapter. Contract is preserved."""
import json
import os
import urllib.error
import urllib.request

def send_lead_created(lead, app, integration_config):
    if not integration_config.get("enabled", False):
        app.logger.info("Google Sheets integration disabled; lead remains stored")
        return
    webhook_url = os.getenv("LEAD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return
    webhook_secret = os.getenv("LEAD_WEBHOOK_SECRET", "").strip()
    if not webhook_secret:
        app.logger.warning("Google Sheets webhook configured without shared secret; delivery skipped")
        return
    payload = {"event":"lead.created","token":webhook_secret,"lead":{"id":lead.id,"created_at":lead.created_at.isoformat() if lead.created_at else None,"name":lead.name,"phone":lead.phone,"email":lead.email,"city":lead.city,"service":lead.service,"area_sqm":lead.area,"budget":lead.budget,"details":lead.details,"status":lead.status}}
    request_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    webhook_request = urllib.request.Request(webhook_url, data=request_data, headers={"Content-Type":"application/json","User-Agent":"EliCohenBusinessMVP/1.0"}, method="POST")
    try:
        with urllib.request.urlopen(webhook_request, timeout=8) as response:
            if not 200 <= response.status < 300:
                app.logger.warning("Google Sheets webhook returned HTTP %s", response.status)
                return
            try:
                result = json.loads(response.read(4096).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                app.logger.warning("Google Sheets webhook returned unreadable response")
                return
            if result.get("ok") is not True:
                app.logger.warning("Google Sheets webhook rejected event; check Apps Script logs")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        app.logger.exception("Google Sheets delivery failed; lead remains saved")
