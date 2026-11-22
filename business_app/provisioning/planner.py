"""Safe, deterministic provisioning plans for isolated business installations."""
from __future__ import annotations

import hashlib
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
}

SUPPORTED_REGIONS = {
    "frankfurt": {"render": "frankfurt", "neon": "eu-central-1", "label": "פרנקפורט / אירופה"},
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
    return normalized


def build_provisioning_plan(
    *,
    business_name: str,
    slug: str,
    category: str,
    modules: Iterable[str],
    region: str = "frankfurt",
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
    return {
        "plan_version": PLAN_VERSION,
        "plan_id": hashlib.sha256(f"{slug}:{PLAN_VERSION}".encode("utf-8")).hexdigest()[:20],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "dry_run",
        "execution_status": "not_started",
        "business": {
            "name": business_name,
            "slug": slug,
            "category": category,
            "category_label": BUSINESS_CATEGORIES[category],
        },
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
            "github": "credentials_and_repository_creation_adapter_required",
            "neon": "credentials_and_project_creation_adapter_required",
            "render": "credentials_and_service_creation_adapter_required",
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
            "Only the modules listed as available in this starter can be selected.",
            "The live provider account, quotas, free-tier eligibility and current prices are not verified by this plan.",
        ],
    }
