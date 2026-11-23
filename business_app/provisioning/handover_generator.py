"""Generate a verified, secret-free customer handover ZIP."""
from __future__ import annotations
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

class HandoverValidationError(ValueError):
    """Raised when a provisioning run is not ready for customer handover."""

_SECRET_KEY = re.compile(
    r"(secret|token|password|credential|authorization|api[_-]?key|private[_-]?key|database[_-]?url|connection[_-]?(uri|string)|webhook[_-]?url|environment[_-]?vars?)",
    re.IGNORECASE,
)
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

def _safe_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username or parsed.password:
            return None
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except ValueError:
        return None

def _safe_identifier(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    if not text or len(text) > 200 or _SECRET_KEY.search(text):
        return None
    if re.search(r"(?i)(?:postgres(?:ql)?://|bearer\s+|gh[pousr]_[A-Za-z0-9_]+|github_pat_)", text):
        return None
    return text

def _markdown_cell(value):
    text = str(value or "לא זמין").replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    return text[:240]

def _resource_inventory(resources):
    github = resources.get("github") if isinstance(resources.get("github"), dict) else {}
    neon = resources.get("neon") if isinstance(resources.get("neon"), dict) else {}
    render = resources.get("render") if isinstance(resources.get("render"), dict) else {}
    pr = github.get("pull_request") if isinstance(github.get("pull_request"), dict) else {}
    return {
        "github": {
            "repository_id": _safe_identifier(github.get("repository_id")),
            "repository": _safe_identifier(github.get("repository")),
            "repository_url": _safe_url(github.get("url")),
            "pull_request_number": _safe_identifier(pr.get("number")),
            "merge_commit": _safe_identifier(github.get("merge_commit")),
        },
        "neon": {
            "project_id": _safe_identifier(neon.get("project_id")),
            "project_name": _safe_identifier(neon.get("project_name")),
            "region": _safe_identifier(neon.get("region")),
            "organization_id": _safe_identifier(neon.get("org_id")),
        },
        "render": {
            "service_id": _safe_identifier(render.get("service_id")),
            "service_name": _safe_identifier(render.get("service_name")),
            "service_url": _safe_url(render.get("url")),
            "health_url": _safe_url(render.get("health_url")),
            "plan": _safe_identifier(render.get("plan")),
            "region": _safe_identifier(render.get("region")),
            "workspace_id": _safe_identifier(render.get("workspace_id")),
        },
    }

def build_handover_pack(state):
    """Return Markdown, JSON inventory and ZIP bytes for a completed workflow."""
    if not isinstance(state, dict) or state.get("status") != "completed":
        raise HandoverValidationError("Only a completed provisioning workflow can be handed over.")
    slug = state.get("business_slug")
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        raise HandoverValidationError("The workflow has no valid business slug.")
    resources = state.get("resources")
    if not isinstance(resources, dict):
        raise HandoverValidationError("Provisioning resource inventory is missing.")
    safe = _resource_inventory(resources)
    gh, neon, render = safe["github"], safe["neon"], safe["render"]
    required = {
        "GitHub repository ID": gh["repository_id"],
        "GitHub repository": gh["repository"],
        "Neon project ID": neon["project_id"],
        "Render service ID": render["service_id"],
        "verified Render health URL": render["health_url"],
    }
    missing = [label for label, value in required.items() if not value]
    if missing:
        raise HandoverValidationError("Handover acceptance failed; missing: " + ", ".join(missing))

    name = _markdown_cell(state.get("business_name") or slug)
    modules = state.get("modules") if isinstance(state.get("modules"), list) else []
    modules = sorted({str(item)[:80] for item in modules if isinstance(item, str)})
    generated_at = datetime.now(timezone.utc).isoformat()
    workflow_id = _safe_identifier(state.get("workflow_id")) or _safe_identifier(state.get("plan_id"))
    whatsapp_selected = "whatsapp_inbound" in modules
    automation_selected = "automation_webhooks" in modules
    whatsapp_status = (
        "נבחר בתכנון; חיבור Meta ובדיקת קליטה מקצה לקצה לא אומתו"
        if whatsapp_selected else "לא נכלל בפרויקט"
    )
    automation_status = (
        "נבחר בתכנון; כתובת webhook ובדיקת תהליך מקצה לקצה לא אומתו"
        if automation_selected else "לא נכלל בפרויקט"
    )
    guide = f"""<div dir="rtl">

# תיק מסירה ותפעול מערכת — {name}

**מזהה פרויקט:** {slug}  
**מזהה תהליך הקמה:** {_markdown_cell(workflow_id)}  
**הופק בתאריך:** {generated_at}  
**סטטוס הקמה:** הושלמה; בדיקת Render /health החזירה HTTP 200.

> **הבהרה חשובה:** תיק זה מתעד משאבים ותוצאות שניתן לאמת מתהליך ההקמה. הוא אינו אישור להעברת בעלות על חשבונות, אינו כולל סיסמאות או מפתחות, ואינו מאשר אינטגרציות או גיבויים שלא נבדקו בנפרד.

## 1. גישה למערכת ולתשתיות

| רכיב | פרטים |
|---|---|
| אתר / שירות Render | {render["service_url"] or "כתובת ציבורית לא הוחזרה על ידי Render"} |
| בדיקת תקינות | {render["health_url"]} |
| מאגר GitHub | {gh["repository_url"] or gh["repository"]} |
| מסד נתונים Neon | מזהה פרויקט: {neon["project_id"]} |
| אזור Neon | {neon["region"] or "לא דווח"} |
| שירות Render | מזהה: {render["service_id"]} |

## 2. סטטוס יכולות ואינטגרציות

| יכולת | סטטוס מאומת |
|---|---|
| פריסת שירות Render | שירות נוצר ובדיקת /health עברה |
| אתר וטופס לידים | הקוד נכלל בפרויקט; שליחת ליד אמיתית וקליטתו ב־CRM דורשות בדיקת קבלה |
| WhatsApp | {whatsapp_status} |
| Make / n8n / Webhooks | {automation_status} |
| גיבוי ושחזור מסד נתונים | לא אומתו במסגרת תהליך ההקמה |
| ניטור חיצוני | לא אומת במסגרת תהליך ההקמה |

## 3. בעלות, חשבונות והרשאות

**סטטוס בעלות על חשבונות: לא אומת.**

יש לוודא בנפרד שהלקוח הוא בעל החשבונות או שהמשאבים הועברו אליו בפועל:
- GitHub: בעלות על המאגר, הרשאות מנהלים ויכולת לשנות הגדרות.
- Render: בעלות על Workspace, הרשאות שירות וחיוב.
- Neon: בעלות על הארגון והפרויקט, הרשאות וגיבויים.
- דומיין, DNS, Meta וחשבונות אוטומציה — אם נכללו בהיקף.

אין למסור ללקוח סיסמאות או מפתחות בתוך קובץ זה. יש להעבירם בערוץ מאובטח, ולאחר מכן להחליף או לסובב פרטי גישה לפי הצורך.

## 4. עלויות, גיבויים ותחזוקה

תוכנית Render המדווחת במלאי: {render["plan"] or "לא דווחה"}. זהו תיעוד הגדרה בלבד ואינו אישור לכך שהשימוש חינמי או שלא ייגבה תשלום. יש לבדוק את תנאי החשבונות, המכסות ואמצעי התשלום בלוחות הבקרה של הספקים.

לפני מסירה סופית יש לבצע גיבוי יזום, לוודא מדיניות שמירה ולבדוק שחזור. אין להניח שקיום מסד נתונים או שירות ענן מעיד על גיבוי תקין.

## 5. צ'ק־ליסט קבלה ומסירה

- [ ] הלקוח קיבל גישה למאגר GitHub ולחשבונות הענן.
- [ ] אומתה בעלות הלקוח או הושלמה העברת הבעלות בכל ספק.
- [ ] הלקוח התחבר לממשק הניהול והוגדרה גישת משתמשים.
- [ ] נשלח ליד ניסיון מטופס האתר ונבדקה הופעתו ב־CRM.
- [ ] אם WhatsApp נכלל: בוצעה בדיקת Meta אמיתית מקצה לקצה.
- [ ] אם Make / n8n נכללו: בוצעה בדיקת webhook אמיתית מקצה לקצה.
- [ ] בוצעו גיבוי ושחזור ניסיון.
- [ ] נבדקו עלויות, מכסות ואמצעי תשלום.
- [ ] נמסרו פרטי גישה בערוץ מאובטח ונבדקה החלפתם.
- [ ] הלקוח אישר קבלת תיעוד, גישה והדרכה.

**נמסר על ידי:** ____________________  
**אישור הלקוח:** ____________________  
**תאריך:** ____ / ____ / ______

</div>
"""
    inventory = {
        "schema_version": 1,
        "generated_at": generated_at,
        "workflow_id": workflow_id,
        "business": {"name": str(state.get("business_name") or slug)[:200], "slug": slug, "modules": modules},
        "provisioning": {"status": "completed", "render_health_verified": True},
        "ownership": {
            "status": "not_verified",
            "note": "Provider account ownership and transfer are not inferred from provisioning success.",
        },
        "resources": safe,
        "integrations": {
            "whatsapp": "selected_not_end_to_end_verified" if whatsapp_selected else "not_selected",
            "automation_webhooks": "selected_not_end_to_end_verified" if automation_selected else "not_selected",
            "database_backup_restore": "not_verified",
            "external_monitoring": "not_verified",
        },
    }
    inventory_text = json.dumps(inventory, ensure_ascii=False, indent=2) + "\n"
    serialized = guide + inventory_text
    forbidden = (
        r"(?i)postgres(?:ql)?://[^\s\"']+",
        r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+",
        r"(?i)(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)",
    )
    if any(re.search(pattern, serialized) for pattern in forbidden):
        raise HandoverValidationError("Handover output failed the secret-safety check.")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("HANDOVER_GUIDE.md", guide)
        archive.writestr("TECHNICAL_INVENTORY.json", inventory_text)
    return {"guide": guide, "inventory": inventory, "inventory_json": inventory_text, "zip_bytes": buffer.getvalue()}
