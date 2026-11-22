# אלי כהן בניה ועץ — MVP

אתר תדמית ומערכת לידים בעברית (RTL), מותאמים למובייל.

## יכולות
- דף נחיתה ופרטי קשר של העסק.
- טופס פנייה עם בדיקות קלט, הגבלת קצב ו-honeypot.
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

## פריסה
`render.yaml` מגדיר Web Service בתוכנית Free. יש לחבר את המאגר ל-Render ולהגדיר את `DATABASE_URL` ואת סיסמת המנהל לפני הפעלה ציבורית. אין להכניס סודות ל-Git.

## גבולות MVP ואבטחה
אין כרגע שליחת WhatsApp אוטומטית, סליקה או חיבור לכלי צד שלישי. הטפסים מוגנים ב-CSRF. הגבלת הקצב נשמרת בזיכרון של תהליך האפליקציה (מתאים ל-MVP חד-מופעי, לא להגנה מבוזרת). לפני הרחבה יש לחבר אחסון משותף כמו Redis ולהוסיף אימות מנהל מתקדם, מדיניות פרטיות וגיבויים.