"""Create a customer-neutral, isolated copy of this application starter."""
import argparse
import fnmatch
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = (".env.example", ".gitignore", "app.py", "wsgi.py", "requirements.txt", "pytest.ini")
# Explicit customer-runtime allowlist. Never recursively copy developer-owned folders.
EXPORT_TREE_FILES = (
    "business_app/__init__.py",
    "business_app/bootstrap.py",
    "business_app/config.py",
    "business_app/integrations/__init__.py",
    "business_app/integrations/email.py",
    "business_app/integrations/google_sheets.py",
    "business_app/integrations/whatsapp_cloud.py",
    "static/css/app.css",
    "static/js/app.js",
    "templates/admin.html",
    "templates/admin_login.html",
    "templates/base.html",
    "templates/index.html",
    "templates/thanks.html",
    "tests/test_app.py",
    "tests/test_lead_integrations.py",
    "tests/test_whatsapp_cloud.py",
    "tests/test_customer_starter.py",
)
# Scan source trees for symlinks, but do not copy them wholesale.
SCAN_DIRECTORIES = ("business_app", "integrations", "static", "templates", "tests", ".github", "docs", "starter")
EXTRA_SOURCE_FILES = ("config/business.template.json", "render.yaml.template", "starter/README.md")

# Developer-only provisioning must never enter a customer application.
EXCLUDED_EXPORT_PATHS = (
    "provisioning",
    "provisioning_web.py",
    "provisioning_wizard.html",
    "DEVELOPER_PROVISIONING_WIZARD_HE.md",
    "PROVIDER_INTEGRATIONS_HE.md",
    "test_provisioning*.py",
    "test_provider_adapters.py",
    ".provisioning-state",
    ".business-app-provisioner",
    "provisioning-state",
    "instance",
    "uploads",
    "user_uploads",
    "backups",
    "runtime",
    "logs",
)

SENSITIVE_EXPORT_SUFFIXES = (
    ".pyc", ".pyo", ".log", ".sqlite", ".sqlite3", ".db", ".pem",
    ".key", ".p12", ".pfx", ".p8", ".jks", ".keystore", ".bak",
    ".backup", ".dump",
)
SENSITIVE_EXPORT_FILENAMES = {
    ".env", ".envrc", ".netrc", ".npmrc", "credentials.json",
    "service-account.json", "id_rsa", "id_ed25519",
}


def _is_excluded_export_name(name):
    lowered = name.lower()
    if lowered == ".env.example":
        return False
    if lowered in SENSITIVE_EXPORT_FILENAMES:
        return True
    if lowered.startswith(".env.") or lowered.endswith(SENSITIVE_EXPORT_SUFFIXES):
        return True
    return any(fnmatch.fnmatchcase(lowered, pattern.lower()) for pattern in EXCLUDED_EXPORT_PATHS)


def _ignore_export_artifacts(directory, names):
    """Exclude developer tooling, credentials and runtime/customer data at every level."""
    return {
        name for name in names
        if name in {"__pycache__", ".pytest_cache", ".git", ".venv", "venv", "node_modules", ".DS_Store"}
        or _is_excluded_export_name(name)
    }


def _validate_export_tree(source_root):
    """Reject symlinks instead of following paths outside the source repository."""
    for relative in FILES:
        current = source_root
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"Starter source contains a symbolic link: {relative}")
    for relative in EXTRA_SOURCE_FILES:
        candidate = source_root / relative
        current = source_root
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"Starter source contains a symbolic link: {relative}")
    for directory in SCAN_DIRECTORIES:
        root = source_root / directory
        if root.is_symlink():
            raise ValueError(f"Starter source contains a symbolic link: {directory}")
        if not root.is_dir():
            continue
        for current, dirnames, filenames in os.walk(root, followlinks=False):
            for name in dirnames + filenames:
                candidate = Path(current) / name
                if candidate.is_symlink():
                    relative = candidate.relative_to(source_root)
                    raise ValueError(f"Starter source contains a symbolic link: {relative.as_posix()}")


def export_starter(target, business_name, slug, modules=None, business_profile=None):
    if not isinstance(business_name, str) or not business_name.strip():
        raise ValueError("Business name must not be empty.")
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("Slug must use lowercase letters, numbers and single hyphens.")
    destination = Path(target).expanduser().resolve()
    if destination == ROOT or ROOT in destination.parents or destination in ROOT.parents:
        raise ValueError("Starter destination must be outside the source repository and its parent folders.")
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"Destination must be empty: {destination}")

    # Validate every source tree before copying. Export itself uses only explicit
    # paths below; developer tools, wizard assets and private docs are never copied.
    _validate_export_tree(ROOT)

    destination.mkdir(parents=True, exist_ok=True)
    for relative in (*FILES, *EXPORT_TREE_FILES):
        source = ROOT / relative
        if not source.is_file():
            raise FileNotFoundError(f"Required Starter source file is missing: {relative}")
        target_path = destination / relative
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target_path)

    # Customer repositories get minimal CI without developer-only PowerShell or relay jobs.
    workflow_path = destination / ".github" / "workflows" / "tests.yml"
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(
        "name: Customer Starter Tests\n"
        "on:\n"
        "  push:\n"
        "    branches: [main]\n"
        "  pull_request:\n"
        "    branches: [main]\n"
        "permissions:\n"
        "  contents: read\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        "          python-version: \"3.11\"\n"
        "      - run: pip install -r requirements.txt\n"
        "      - run: python -m pytest -q\n",
        encoding="utf-8",
    )

    config_dir = destination / "config"
    config_dir.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "config" / "business.template.json", config_dir / "business.template.json")
    shutil.copy2(ROOT / "render.yaml.template", destination / "render.yaml")
    shutil.copy2(ROOT / "starter" / "README.md", destination / "README.md")

    # The export deliberately does not copy the live customer's business.json or render.yaml.
    from business_app.bootstrap import bootstrap
    bootstrap(destination, business_name=business_name, slug=slug)
    config_path = destination / "config" / "business.json"
    config_data = json.loads(config_path.read_text(encoding="utf-8"))

    if business_profile is not None:
        if not isinstance(business_profile, dict):
            raise ValueError("Business profile must be an object")
        contact = business_profile.get("contact") or {}
        branding = business_profile.get("branding") or {}
        config_data["tagline"] = business_profile.get("tagline", "")
        config_data.setdefault("contact", {}).update({
            "phone_display": contact.get("phone_display", ""),
            "phone_href": contact.get("phone_href", ""),
            "whatsapp_url": contact.get("whatsapp_url", ""),
            "address": contact.get("address", ""),
            "service_area": contact.get("service_area", ""),
        })
        config_data.setdefault("branding", {}).update({
            "primary_color": branding.get("primary_color", "#172b25"),
            "theme_color": branding.get("primary_color", "#172b25"),
            "accent_color": branding.get("accent_color", "#b88a54"),
            "font_family": branding.get("font_family", "Heebo"),
        })
        page_content = config_data.setdefault("content", {})
        hero = page_content.setdefault("hero", {})
        hero["eyebrow"] = business_profile.get("category_label", "עסק")
        hero["title_line_1"] = business_name
        hero["title_line_2"] = business_profile.get("tagline") or "נשמח לעזור לכם"
        hero["description"] = "השאירו פרטים ונחזור אליכם בהקדם."
        configured_services = business_profile.get("services") or []
        page_content["services"] = [
            {
                "id": f"service-{index}",
                "number": f"{index:02d}",
                "name": service,
                "form_value": service,
                "description": "",
                "link_text": "לפרטים נוספים",
                "prompt": f"ספרו לנו על השירות המבוקש: {service}",
            }
            for index, service in enumerate(configured_services, start=1)
        ]
        if not configured_services:
            page_content["services"] = []

    if modules is not None:
        selected = set(modules)
        for module_name in (
            "lead_capture", "lead_management", "estimate_calculator",
            "whatsapp_inbound", "automation_webhooks", "ai_lead_enrichment",
        ):
            config_data.setdefault("modules", {})[module_name] = module_name in selected
        config_data.setdefault("integrations", {}).setdefault("whatsapp_cloud_api", {})["enabled"] = (
            "whatsapp_inbound" in selected
        )
        config_data.setdefault("integrations", {}).setdefault("automation_webhook", {})["enabled"] = (
            "automation_webhooks" in selected
        )
    config_path.write_text(
        json.dumps(config_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export a clean, customer-neutral Business App Starter.")
    parser.add_argument("--target", required=True, help="New empty destination folder outside this repository")
    parser.add_argument("--business-name", required=True)
    parser.add_argument("--slug", required=True)
    args = parser.parse_args(argv)
    try:
        destination = export_starter(args.target, args.business_name, args.slug)
    except (FileExistsError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Clean starter exported to: {destination}")
    print("Customer database, live business configuration, secrets and developer provisioning tools were not copied.")
    print("Review render.yaml and configure new hosting, database and credentials before deployment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
