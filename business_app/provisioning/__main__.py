"""Run the internal provisioning plan wizard from a terminal."""
import argparse
import json
import re
import sys
from pathlib import Path

from .planner import (
    BUSINESS_CATEGORIES,
    SUPPORTED_MODULES,
    ProvisioningInputError,
    build_provisioning_plan,
)


def _prompt_required(label: str) -> str:
    while True:
        value = input(f"{label}: ").strip()
        if value:
            return value


def run_wizard() -> dict:
    print("\nאשף הקמת עסק חדש — כלי מפתח פנימי")
    print("הכלי יפיק תוכנית הקמה בלבד; בשלב זה לא ייווצרו משאבים בענן.\n")
    name = _prompt_required("שם העסק")
    slug = _prompt_required("מזהה באנגלית (slug)")
    print("\nסוג העסק:")
    categories = list(BUSINESS_CATEGORIES.items())
    for index, (key, label) in enumerate(categories, start=1):
        print(f"{index}. {label} ({key})")
    category_index = int(_prompt_required("מספר סוג העסק"))
    if not 1 <= category_index <= len(categories):
        raise ProvisioningInputError("Invalid business category selection")
    category = categories[category_index - 1][0]

    modules = list(SUPPORTED_MODULES.items())
    print("\nמודולים זמינים:")
    for index, (key, details) in enumerate(modules, start=1):
        print(f"{index}. {details['label']} ({key})")
    raw_selection = input("בחר מספרים מופרדים בפסיקים (למשל 1,2): ").strip()
    selected = []
    if raw_selection:
        for value in raw_selection.split(","):
            index = int(value.strip())
            if not 1 <= index <= len(modules):
                raise ProvisioningInputError("Invalid module selection")
            selected.append(modules[index - 1][0])
    return build_provisioning_plan(
        business_name=name,
        slug=slug,
        category=category,
        modules=selected,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Internal developer provisioning plan wizard")
    parser.add_argument("--output", default="provisioning-plan.json")
    args = parser.parse_args(argv)
    try:
        plan = run_wizard()
        destination = Path(args.output).expanduser()
        if destination.exists():
            raise ProvisioningInputError(
                f"Output file already exists; choose a new path: {destination}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (ProvisioningInputError, OSError, ValueError) as exc:
        print(f"לא ניתן ליצור תוכנית: {exc}", file=sys.stderr)
        return 1
    print(f"\nתוכנית ההקמה נשמרה: {destination.resolve()}")
    print("לא נוצרו מאגר, מסד נתונים או שירות ענן.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
