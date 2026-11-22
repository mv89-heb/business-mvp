"""Safe, deterministic provisioning plans for isolated business installations."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Iterable

PLAN_VERSION = 1

BUSINESS_CATEGORIES = {
    "services": "עסק שירותים",
    "construction": "בנייה ושיפוצים",
    "consulting": "ייעוץ",
    "retail": "מסחר",
    "institution": "מוסד / ארגון",
    "custom": "עסק בהתאמה אישית",
}

SUPPORTED_MODULES = {
    "lead_capture": {
        "label": "קליטת פניות",
        "description": "טופס פנייה ציבורי ושמירת לידים",
        "status": "available",
    },
    "lead_management": {
        "label": "ניהול פניות",
        "description": "מסך ניהול, סינון, סטטוסים וייצוא CSV",
        "status": "available",
    },
    "estimate_calculator": {
        "label": "מחשבון אומדן",
        "description": "מחשבון אומדן בסיסי לפי תעריפים מוגדרים",
        "status": "available",
    },
    "whatsapp_inbound": {
        "label": "קליטת לידים מ־WhatsApp",
        "description": "Webhook Meta מאומת בקוד; נדרשים חשבון Meta, App Secret ו־Verify Token",
        "status": "implemented_setup_required",
    },
    "automation_webhooks": {
        "label": "אוטומציות Make / n8n",
        "description": "API Outbox/ACK/Callback קיים; נדרש workflow נפרד ב־Make או n8n",
        "status": "api_ready_automation_required",
    },
    "ai_lead_enrichment": {
        "label": "סיווג לידים באמצעות Gemini",
        "description": "Callback CRM קיים; נדרש workflow חיצוני עם Gemini; טקסט חופשי כבוי כברירת מחדל",
        "status": "external_gemini_workflow_required",
    },
}

FUTURE_MODULES = {
    "crm": "CRM מלא",
    "inventory": "ניהול מלאי",
    "orders": "הזמנות ורכש",
    "documents": "ניהול מסמכים",
    "appointments": "יומן ותורים",
    "projects": "ניהול פרויקטים",
    "finance": "כספים וגבייה",
    "analytics": "אנליטיקה מתקדמת",
    "canva_branding": "עיצוב ומיתוג באמצעות Canva",
}

SUPPORTED_REGIONS = {
    "frankfurt": {"render": "frankfurt", "neon": "aws-eu-central-1", "label": "פרנקפורט / אירופה"},
}

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MODULE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class ProvisioningInputError(ValueError):
    """Raised when a requested business setup cannot be represented safely."""


def _idempotency_key(slug: str, provider: str, resource: str) -> str:
    raw = f"{PLAN_VERSION}:{slug}:{provider}:{resource}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _normalize_modules(modules: Iterable[str]) -> list[str]:
    if not isinstance(modules, (list, tuple, set)):
        raise ProvisioningInputError("modules must be a list of module identifiers")
    if any(not isinstance(item, str) or not MODULE_RE.fullmatch(item) for item in modules):
        raise ProvisioningInputError("Invalid module identifier")
    normalized = sorted(set(modules))
    unsupported = [item for item in normalized if item not in SUPPORTED_MODULES]
    if unsupported:
        labels = [FUTURE_MODULES.get(item, item) for item in unsupported]
        raise ProvisioningInputError(
            "Requested modules are not implemented in this starter: " + ", ".join(labels)
        )
    if "ai_lead_enrichment" in normalized and "automation_webhooks" not in normalized:
        raise ProvisioningInputError("Gemini lead enrichment requires the automation_webhooks module")
    return normalized


def _normalize_contact_number(value: str, label: str) -> tuple[str, str]:
    """Return a display-safe number and a digits-only tel target."""
    if not isinstance(value, str):
        raise ProvisioningInputError(f"{label} must be text")
    display = value.strip()
    if not display:
        return "", ""
    if len(display) > 30 or re.search(r"[^0-9+().\s-]", display):
        raise ProvisioningInputError(f"{label} contains unsupported characters")
    digits = re.sub(r"\D", "", display)
    if not 7 <= len(digits) <= 15:
        raise ProvisioningInputError(f"{label} must contain 7 to 15 digits")
    return display, digits


def _normalize_whatsapp_number(value: str) -> tuple[str, str]:
    display, digits = _normalize_contact_number(value, "WhatsApp number")
    if not digits:
        return "", ""
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = "972" + digits[1:]
    elif len(digits) == 10 and digits.startswith("5"):
        digits = "972" + digits
    if not 8 <= len(digits) <= 15:
        raise ProvisioningInputError("WhatsApp number must be a valid international number")
    return display, f"https://wa.me/{digits}"


def _optional_text(value, label, maximum):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ProvisioningInputError(f"{label} must be text")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ProvisioningInputError(f"{label} must be {maximum} characters or fewer")
    return normalized


def build_provisioning_plan(
    *,
    business_name: str,
    slug: str,
    category: str,
    modules: Iterable[str],
    region: str = "frankfurt",
    tagline: str = "",
    phone: str = "",
    whatsapp_number: str = "",
    address: str = "",
    service_area: str = "",
    primary_color: str = "#172b25",
    accent_color: str = "#b88a54",
    font_family: str = "Heebo",
    services: Iterable[str] | None = None,
) -> dict:
    """Build a deterministic dry-run plan; never contacts a provider or creates resources."""
    if not isinstance(business_name, str) or not business_name.strip():
        raise ProvisioningInputError("Business name is required")
    business_name = business_name.strip()
    if len(business_name) > 120:
        raise ProvisioningInputError("Business name must be 120 characters or fewer")
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
        raise ProvisioningInputError("Slug must use lowercase letters, numbers and single hyphens")
    if not isinstance(category, str) or category not in BUSINESS_CATEGORIES:
        raise ProvisioningInputError("Unknown business category")
    if not isinstance(region, str) or region not in SUPPORTED_REGIONS:
        raise ProvisioningInputError("Unsupported deployment region")

    selected_modules = _normalize_modules(modules)
    tagline = _optional_text(tagline, "Tagline", 160)
    address = _optional_text(address, "Address", 160)
    service_area = _optional_text(service_area, "Service area", 160)
    phone_display, phone_digits = _normalize_contact_number(phone, "Phone number")
    whatsapp_display, whatsapp_url = _normalize_whatsapp_number(whatsapp_number)
    if not isinstance(primary_color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", primary_color):
        raise ProvisioningInputError("Primary color must be a six-digit hexadecimal color")
    if not isinstance(accent_color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", accent_color):
        raise ProvisioningInputError("Accent color must be a six-digit hexadecimal color")
    allowed_fonts = {"Heebo", "Assistant", "Rubik", "Arial"}
    if not isinstance(font_family, str) or font_family not in allowed_fonts:
        raise ProvisioningInputError("Unsupported font family")
    if services is None:
        services = []
    if not isinstance(services, (list, tuple)):
        raise ProvisioningInputError("Services must be a list")
    if len(services) > 6:
        raise ProvisioningInputError("A maximum of six services can be configured")
    normalized_services = []
    seen_services = set()
    for item in services:
        service = _optional_text(item, "Service name", 80)
        if not service:
            continue
        key = service.casefold()
        if key not in seen_services:
            normalized_services.append(service)
            seen_services.add(key)

    business_profile = {
        "name": business_name,
        "slug": slug,
        "category": category,
        "category_label": BUSINESS_CATEGORIES[category],
        "tagline": tagline,
        "contact": {
            "phone_display": phone_display,
            "phone_href": f"tel:{phone_digits}" if phone_digits else "",
            "whatsapp_number_display": whatsapp_display,
            "whatsapp_url": whatsapp_url,
            "address": address,
            "service_area": service_area,
        },
        "branding": {
            "primary_color": primary_color.lower(),
            "accent_color": accent_color.lower(),
            "font_family": font_family,
        },
        "services": normalized_services,
    }
    region_config = SUPPORTED_REGIONS[region]
    resources = [
        {
            "provider": "github",
            "kind": "repository",
            "name": slug,
            "visibility": "private",
            "status": "planned",
            "idempotency_key": _idempotency_key(slug, "github", "repository"),
        },
        {
            "provider": "neon",
            "kind": "postgres_project",
            "name": slug,
            "region": region_config["neon"],
            "status": "planned",
            "idempotency_key": _idempotency_key(slug, "neon", "postgres_project"),
        },
        {
            "provider": "render",
            "kind": "web_service",
            "name": slug,
            "region": region_config["render"],
            "plan": "free",
            "status": "planned",
            "idempotency_key": _idempotency_key(slug, "render", "web_service"),
        },
    ]
    identity = {
        "business": business_profile,
        "modules": selected_modules,
        "region": region,
    }
    plan_id = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return {
        "plan_version": PLAN_VERSION,
        "plan_id": plan_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "dry_run",
        "execution_status": "not_started",
        "business": business_profile,
        "application": {
            "modules": selected_modules,
            "module_details": {
                module: SUPPORTED_MODULES[module] for module in selected_modules
            },
        },
        "infrastructure": {
            "region": region,
            "region_label": region_config["label"],
            "resources": resources,
        },
        "provider_readiness": {
            "github": "adapter_available_credentials_required",
            "neon": "adapter_available_credentials_required",
            "render": "adapter_available_credentials_required",
        },
        "cost_policy": {
            "requested_render_plan": "free",
            "paid_resources_allowed": False,
            "live_price_check_required": True,
            "note": "Provider pricing and free-tier eligibility must be checked at execution time.",
        },
        "security": {
            "secrets_included": False,
            "customer_data_copied": False,
            "separate_repository_database_and_service": True,
        },
        "requires_explicit_execution_approval": True,
        "warnings": [
            "This is a dry-run plan. No provider API was called and no cloud resource was created.",
            "Only modules implemented in this starter can be selected; some require separate provider accounts and external workflow setup.",
            "The live provider account, quotas, free-tier eligibility and current prices are not verified by this plan.",
        ],
    }
