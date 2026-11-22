"""Create a customer-neutral, isolated copy of this application starter."""
import argparse
import fnmatch
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = (".env.example", ".gitignore", "app.py", "wsgi.py", "requirements.txt", "pytest.ini")
DIRECTORIES = ("business_app", "integrations", "static", "templates", "tests", ".github", "docs")

# Developer-only provisioning must never enter a customer application.
EXCLUDED_EXPORT_PATHS = (
    "provisioning",
    "provisioning_web.py",
    "provisioning_wizard.html",
    "DEVELOPER_PROVISIONING_WIZARD_HE.md",
    "PROVIDER_INTEGRATIONS_HE.md",
    "test_provisioning*.py",
    "test_provider_adapters.py",
)


def _ignore_export_artifacts(directory, names):
    """Exclude developer tooling and local artifacts at every copied directory level."""
    ignored = set()
    for name in names:
        if name in {"__pycache__", ".pytest_cache", ".git", ".venv", "venv", "node_modules", ".DS_Store"}:
            ignored.add(name)
        elif name.endswith((".pyc", ".pyo", ".log", ".sqlite", ".db")):
            ignored.add(name)
        elif any(fnmatch.fnmatchcase(name, pattern) for pattern in EXCLUDED_EXPORT_PATHS):
            ignored.add(name)
        elif name == ".env":
            ignored.add(name)
    return ignored


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
