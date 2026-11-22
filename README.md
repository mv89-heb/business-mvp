# אלי כהן בניה ועץ — MVP

אתר תדמית ומערכת לידים בעברית (RTL), מותאמים למובייל.

## יכולות
- דף נחיתה ופרטי קשר של העסק.
- טופס פנייה עם בדיקות קלט, הסכמה מפורשת ליצירת קשר, שמירת מועד ההסכמה, הגבלת קצב ו-honeypot.
- שמירת פניות ב-Neon PostgreSQL.
- מסך ניהול מוגן בסיסמה, סינון פניות ועדכון סטטוסים.
- מחשבון אומדן המציג טווח רק לאחר הגדרת תעריפים שאושרו על ידי בעל העסק. כברירת מחדל לא מומצאים מחירים.
- נקודת health check ב-`/health` ובדיקות אוטומטיות ב-GitHub Actions.

## הרצה מקומית ב-Windows PowerShell
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:SECRET_KEY="local-development-secret"
$env:ADMIN_PASSWORD="choose-a-local-password"
$env:DATABASE_URL="sqlite:///local.db"
python -m flask --app wsgi run
```

## משתני סביבה ב-Render
- `DATABASE_URL`: כתובת החיבור ל-Neon, לשמור כ-Secret.
- `SECRET_KEY`: מפתח אקראי ארוך.
- `ADMIN_PASSWORD`: סיסמת מנהל חזקה, או `ADMIN_PASSWORD_HASH` עם hash של Werkzeug.
- `ESTIMATE_BASE_RATE` ו-`ESTIMATE_RATE_PER_SQM`: להשאיר 0 עד לקבלת תעריפים אמיתיים ואישור בעל העסק.
- `LEAD_WEBHOOK_URL` (אופציונלי): כתובת Web App של Google Apps Script או Webhook תואם לקבלת פניות חדשות כ-JSON.
- `LEAD_WEBHOOK_SECRET` (חובה כאשר מוגדר URL): סוד משותף הנשלח בגוף הבקשה. אין לשמור אותו ב-GitHub; יש להגדיר אותו ב-Render וב-Script Properties. כשל בשליחה אינו מונע שמירת פנייה.
- מדריך חיבור חינמי ל-Google Sheets: `integrations/google-apps-script/SETUP.md`.

## פריסה
`render.yaml` מגדיר Web Service בתוכנית Free. יש לחבר את המאגר ל-Render ולהגדיר את `DATABASE_URL` ואת סיסמת המנהל לפני הפעלה ציבורית. אין להכניס סודות ל-Git.

## גבולות MVP ואבטחה
אין כרגע שליחת WhatsApp אוטומטית, סליקה או חיבור לכלי צד שלישי. הטפסים מוגנים ב-CSRF. הגבלת הקצב נשמרת בזיכרון של תהליך האפליקציה (מתאים ל-MVP חד-מופעי, לא להגנה מבוזרת). לפני הרחבה יש לחבר אחסון משותף כמו Redis ולהוסיף אימות מנהל מתקדם, מדיניות פרטיות וגיבויים.

### מדריך הקמת עסק חדש
למדריך המלא לשכפול נקי והקמת שירותים נפרדים ב־GitHub, Neon ו־Render, כולל PowerShell, משתני סביבה, אבטחה ובדיקות: [docs/NEW_CUSTOMER_INSTALLATION_HE.md](docs/NEW_CUSTOMER_INSTALLATION_HE.md).

## Business App Starter
This repository is structured as a reusable **single-customer** application starter, not a multi-tenant SaaS product. Business identity, content, branding, feature switches and integration enablement live in `config/business.json`. Credentials remain environment secrets.

- Architecture: `docs/ARCHITECTURE.md`
- New installation: `docs/CLONING.md`
- Ownership transfer: `docs/HANDOVER.md`
- GitHub template setup: `docs/GITHUB_TEMPLATE_REPOSITORY.md`
- Initialize a clean project: `python -m business_app.bootstrap --target . --business-name "New Business" --slug "new-business"`
- Validate required environment variables: `python -m business_app.bootstrap --check-env`

The live source repository contains customer-specific configuration and must not be marked as a GitHub template. Export a clean project with `business_app.export_starter`; only the exported project may become a template after a clean clone, CI and deployment review. The Google Apps Script webhook contract is retained. WhatsApp is disabled and sending is deliberately blocked pending explicit business-owner approval and official API setup.

## כלי מפתח פנימי — אשף הקמת עסקים
אשף ההקמה המקומי מיועד למפתח בלבד, כולל ממשק עברי רספונסיבי שמאזין ל־127.0.0.1 ומוגן בסיסמה. בשלב הנוכחי הוא מפיק תוכנית Dry Run ליצירת Repository פרטי, פרויקט Neon ושירות Render נפרדים; הוא עדיין אינו יוצר משאבי ענן. הוראות הפעלה, יכולות ומגבלות: [docs/DEVELOPER_PROVISIONING_WIZARD_HE.md](docs/DEVELOPER_PROVISIONING_WIZARD_HE.md).
