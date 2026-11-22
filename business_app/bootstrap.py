"""Initialize a clean customer-specific business configuration."""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from business_app.config import validate_runtime_environment

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "config" / "business.template.json"

def bootstrap(target, business_name=None, slug=None):
    destination = Path(target).expanduser().resolve()
    config_dir = destination / "config"
    config_path = config_dir / "business.json"
    if config_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing configuration: {config_path}")
    if slug and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("Slug must use lowercase letters, numbers and single hyphens")
    config_dir.mkdir(parents=True, exist_ok=True)
    data = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    if business_name:
        data["name"] = business_name.strip()
    if slug:
        data["id"] = slug
    config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    example = ROOT / ".env.example"
    if example.exists() and not (destination / ".env.example").exists():
        shutil.copyfile(example, destination / ".env.example")
    return config_path

def main(argv=None):
    parser = argparse.ArgumentParser(description="Business App Starter bootstrap")
    parser.add_argument("--target")
    parser.add_argument("--business-name")
    parser.add_argument("--slug")
    parser.add_argument("--check-env", action="store_true")
    args = parser.parse_args(argv)
    if args.check_env:
        missing = validate_runtime_environment()
        if missing:
            print("Missing required variables: " + ", ".join(missing), file=sys.stderr)
            return 1
        print("Required runtime variables are present.")
        return 0
    if not args.target:
        parser.error("--target is required unless --check-env is used")
    try:
        path = bootstrap(args.target, args.business_name, args.slug)
    except (FileExistsError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Created clean business configuration: {path}")
    print("No customer data, database, provider credentials or accounts were copied.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
