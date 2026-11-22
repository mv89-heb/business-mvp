"""Verified inbound WhatsApp Cloud API webhook helpers.

This module only parses inbound events. Outbound messaging is intentionally
not implemented here; sending requires explicit business approval and Meta
policy/price checks.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timezone


_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?972[-\s]?)?0?5\d(?:[-\s().]?\d){7}(?!\w)")


def verify_signature(raw_body: bytes, signature: str, app_secret: str) -> bool:
    """Validate Meta's X-Hub-Signature-256 header using constant-time comparison."""
    if not isinstance(raw_body, bytes) or not signature or not app_secret:
        return False
    if not signature.startswith("sha256="):
        return False
    supplied = signature.partition("=")[2].strip().lower()
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return bool(supplied) and hmac.compare_digest(supplied, expected)


def verify_webhook_challenge(mode: str, token: str, expected_token: str, challenge: str):
    """Return Meta's challenge only for a valid subscription handshake."""
    if mode != "subscribe" or not token or not expected_token or not challenge:
        return None
    if not hmac.compare_digest(str(token), str(expected_token)):
        return None
    return str(challenge)


def normalize_phone(phone: str) -> str:
    """Normalize common Israeli WhatsApp numbers to the local 0-prefixed form."""
    digits = re.sub(r"\D", "", str(phone or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("972"):
        digits = "0" + digits[3:]
    return digits


def redact_for_ai(value: str) -> str:
    """Remove obvious phone numbers and email addresses before AI processing."""
    text = str(value or "")[:4000]
    text = _EMAIL_RE.sub("[דוא״ל הושחר]", text)
    text = _PHONE_RE.sub("[טלפון הושחר]", text)
    return text


def extract_inbound_messages(payload: dict):
    """Yield normalized inbound message records from Meta webhook payloads."""
    if not isinstance(payload, dict):
        return
    for entry in payload.get("entry", []) or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes", []) or []:
            if not isinstance(change, dict):
                continue
            value = change.get("value") or {}
            if not isinstance(value, dict):
                continue
            contacts = {}
            for contact in value.get("contacts", []) or []:
                if isinstance(contact, dict) and contact.get("wa_id"):
                    contacts[str(contact["wa_id"])] = contact
            for message in value.get("messages", []) or []:
                if not isinstance(message, dict):
                    continue
                message_id = str(message.get("id") or "").strip()
                sender = normalize_phone(message.get("from", ""))
                if not message_id or not sender:
                    continue
                message_type = str(message.get("type") or "unknown").lower()
                body = ""
                if message_type == "text":
                    body = str((message.get("text") or {}).get("body") or "")
                elif message_type == "interactive":
                    interactive = message.get("interactive") or {}
                    reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
                    body = str(reply.get("title") or "[תגובה אינטראקטיבית]")
                else:
                    body = f"[הודעת {message_type}]"
                contact = contacts.get(str(message.get("from") or ""), {})
                profile = contact.get("profile") or {}
                raw_timestamp = message.get("timestamp")
                try:
                    message_at = datetime.fromtimestamp(int(raw_timestamp), tz=timezone.utc)
                except (TypeError, ValueError, OSError, OverflowError):
                    message_at = datetime.now(timezone.utc)
                yield {
                    "message_id": message_id,
                    "phone": sender,
                    "name": str(profile.get("name") or "").strip()[:120],
                    "message_type": message_type[:32],
                    "body": body[:4000],
                    "ai_text": redact_for_ai(body),
                    "message_at": message_at,
                }
