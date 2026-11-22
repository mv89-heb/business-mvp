"""Execute a reviewed provisioning plan from a local developer terminal."""
import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from .providers.http import ProviderError
from .workflow import ProvisioningWorkflow


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Execute a reviewed Business App Starter plan (creates real cloud resources)."
    )
    parser.add_argument("--plan", required=True, help="Path to a JSON dry-run plan")
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Perform read-only provider and resource checks; do not create or modify cloud resources",
    )
    args = parser.parse_args(argv)
    try:
        plan = json.loads(Path(args.plan).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"לא ניתן לקרוא את תוכנית ההקמה: {exc}", file=sys.stderr)
        return 1

    owner = os.getenv("GITHUB_OWNER", "").strip()
    if not owner:
        print("יש להגדיר GITHUB_OWNER בסביבת המפתח.", file=sys.stderr)
        return 1

    if args.preflight_only:
        try:
            report = ProvisioningWorkflow().preflight(plan, github_owner=owner)
        except ProviderError as exc:
            print(f"בדיקת ההיתכנות נכשלה: {exc}", file=sys.stderr)
            return 1
        print("\nדוח Preflight לקריאה בלבד:")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print("\nלא נוצרו או שונו משאבים בענן.")
        return 0

    workflow = ProvisioningWorkflow()
    try:
        preflight_report = workflow.preflight(plan, github_owner=owner)
    except ProviderError as exc:
        print(f"בדיקת ההיתכנות נכשלה; לא נוצרו משאבים: {exc}", file=sys.stderr)
        return 1
    print("\nדוח Preflight לקריאה בלבד:")
    print(json.dumps(preflight_report, ensure_ascii=False, indent=2))

    print("\nאזהרה: הפעולה תיצור משאבים אמיתיים ב-GitHub, Neon ו-Render.")
    print("יש לוודא מראש שהחשבון זכאי ל-Neon Free ושלא צפוי חיוב.")
    approval = input('להמשך, הקלד CREATE: ').strip()
    free_confirmation = input('לאישור בדיקת עלות אפס ב-Neon, הקלד ZERO-COST: ').strip()
    if free_confirmation != "ZERO-COST":
        print("ההקמה בוטלה: לא התקבל אישור עלות אפס.", file=sys.stderr)
        return 1

    admin_password = getpass.getpass("בחר סיסמת מנהל ללקוח (לפחות 14 תווים): ")
    repeat = getpass.getpass("הזן שוב את סיסמת המנהל: ")
    if admin_password != repeat:
        print("הסיסמאות אינן תואמות.", file=sys.stderr)
        return 1

    try:
        result = workflow.run(
            plan,
            github_owner=owner,
            admin_password=admin_password,
            approval=approval,
            neon_free_tier_verified=True,
        )
    except ProviderError as exc:
        print(f"ההקמה נעצרה בבטחה: {exc}", file=sys.stderr)
        print("מצב התהליך נשמר מקומית ללא סודות; בדוק את המשאבים לפני ניסיון חוזר.")
        return 1

    print("\nההקמה הושלמה:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("שמור את סיסמת המנהל בכספת סיסמאות מאובטחת; היא אינה נשמרת באשף.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
