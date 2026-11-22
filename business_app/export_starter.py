"""Create a customer-neutral, isolated copy of this application starter."""
import argparse
import fnmatch
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = (".env.example", ".gitignore", "app.py", "wsgi.py", "requirements.txt", "pytest.ini")
DIRECTORIES = ("business_app", "integrations", "static", "templates", "tests", ".github", "docs")
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
    for relative in EXTRA_SOURCE_FILES:
        candidate = source_root / relative
        current = source_root
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"Starter source contains a symbolic link: {relative}")
    for directory in DIRECTORIES:
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


def export_starter(target, business_name, slug):
    if not isinstance(business_name, str) or not business_name.strip():
        raise ValueError("Business name must not be empty.")
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("Slug must use lowercase letters, numbers and single hyphens.")
    destination = Path(target).expanduser().resolve()
    if destination == ROOT or ROOT in destination.parents or destination in ROOT.parents:
        raise ValueError("Starter destination must be outside the source repository and its parent folders.")
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"Destination must be empty: {destination}")

    # Validate before copying anything: copytree follows symlinks by default,
    # which could otherwise pull credentials or customer files from outside ROOT.
    for filename in FILES:
        source_file = ROOT / filename
        if source_file.is_symlink():
            raise ValueError(f"Starter source contains a symbolic link: {filename}")
    _validate_export_tree(ROOT)

    destination.mkdir(parents=True, exist_ok=True)
    for filename in FILES:
        source = ROOT / filename
        if source.is_file() and filename != ".env":
            shutil.copy2(source, destination / filename)

    for directory in DIRECTORIES:
        source = ROOT / directory
        if source.is_dir():
            shutil.copytree(
                source,
                destination / directory,
                dirs_exist_ok=True,
                ignore=_ignore_export_artifacts,
            )

    config_dir = destination / "config"
    config_dir.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "config" / "business.template.json", config_dir / "business.template.json")
    shutil.copy2(ROOT / "render.yaml.template", destination / "render.yaml")
    shutil.copy2(ROOT / "starter" / "README.md", destination / "README.md")

    # The export deliberately does not copy the live customer's business.json or render.yaml.
    from business_app.bootstrap import bootstrap
    bootstrap(destination, business_name=business_name, slug=slug)
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
