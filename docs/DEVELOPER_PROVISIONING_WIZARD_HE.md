# אשף הקמת עסקים — כלי מפתח פנימי

## מטרה וגבולות

אשף ההקמה מיועד למפתח בלבד, כדי לקצר ולהאחיד הקמת אפליקציות עסקיות חדשות. הוא אינו מסך ניהול של לקוח, אינו חלק מהאפליקציה המיוצאת ואסור לחשוף אותו למשתמשי קצה.

כל התקנה עסקית נשארת עצמאית: Repository פרטי ב־GitHub, פרויקט PostgreSQL ב־Neon, שירות Render, סודות ונתוני לקוחות נפרדים.

## הרצה ראשונה

מתוך סביבת הפיתוח של המאגר, ב־PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
py -m business_app.provisioning --output .\work\new-business-plan.json
```

האשף מבקש שם עסק, slug, קטגוריה ומודולים. קובץ התוכנית נוצר רק אם נתיב הפלט אינו קיים; אין דריסה שקטה של תוכנית קודמת.

## הפעלת ממשק האשף הגרפי

לנוחות העבודה אפשר להפעיל ממשק מקומי בעברית, המיועד למפתח בלבד. ב־PowerShell, מתוך תיקיית המאגר:

```powershell
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$bytes = New-Object byte[] 48
$rng.GetBytes($bytes)
$env:PROVISIONING_SESSION_SECRET = [Convert]::ToBase64String($bytes)
$rng.Dispose()
$secure = Read-Host "בחר סיסמת כניסה לאשף" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $env:PROVISIONING_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
py -m business_app.provisioning_web
```

פתח בדפדפן: `http://127.0.0.1:8765`. השרת מאזין ל־localhost בלבד; אין לשנות את ה־host ל־`0.0.0.0` ואין לחשוף את האשף לרשת או ללקוחות. האפליקציה עצמה דוחה גם בקשות שמגיעות מכתובת IP שאינה loopback או עם Host שאינו localhost, כך שהגבלת הגישה אינה נשענת רק על כתובת ההאזנה. נדרשים מפתח Session באורך 32 תווים לפחות וסיסמת מפתח באורך 16 תווים לפחות. מסך ההתחברות מוגבל לחמישה ניסיונות בדקה; חריגה מחזירה HTTP 429.

כדי לבדוק חיבורי ספקים ולהפעיל הקמה מתוך הממשק, הגדר באותה חלונית PowerShell גם את משתני הסביבה `GITHUB_TOKEN`, `GITHUB_OWNER`, `NEON_API_KEY`, `RENDER_API_KEY`, `RENDER_WORKSPACE_ID` (ו־`NEON_ORG_ID` אם נדרש). אין להקליד מפתחות API בשדות הטופס או לשמור אותם בקובצי הפרויקט.

טופס ההקמה מציג תחילה תוכנית Dry Run. לאחר מכן יש להריץ Preflight לקריאה בלבד, שבודק הרשאות, שמות משאבים קיימים והתאמת תצורה ב־GitHub, Neon ו־Render. רק אם הבדיקה הצליחה ייפתח טופס הביצוע, שבו נדרשים אישור `CREATE`, אישור ידני לעלות אפס ב־Neon וסיסמת מנהל ללקוח. בקשת הביצוע נשארת פתוחה בזמן יצירת המשאבים והמתנה ל־`/health`, ולכן ייתכן שיידרשו כמה דקות. סיסמת מנהל הלקוח אינה מוצגת שוב ואינה נשמרת במצב התהליך. לפני העברתה ל־Render היא מומרת ל־Werkzeug password hash; רק ה־hash נשמר במשתני הסביבה של השירות.

## מה קיים בשלב זה

- תכנון התקנה נפרדת ל־GitHub, Neon ו־Render.
- בדיקת חיבור חיה ל־GitHub, Neon ו־Render מתוך ממשק האשף, כאשר משתני הסביבה מוגדרים.\n- Preflight לקריאה בלבד שמאתר Repository פרטי קיים, פרויקט Neon קיים ושירות Render קיים, ומכשיל תצורה לא תואמת; אינו יוצר, מעדכן או מוחק משאבים.
- מתאמי API נפרדים: GitHub יכול ליצור Repository פרטי; Neon יכול לאתר/ליצור פרויקט ולהחזיר URI; Render יכול לאתר/ליצור Web Service בתוכנית Free בלבד.
- איתור משאבים קיימים לפני יצירה, טיפול בכפילויות ו-pagination.
- טיפול זהיר ב-timeout של בקשת כתיבה: התוצאה מסומנת כלא ידועה, ואין retry אוטומטי.
- בחירת קטגוריית עסק והמודולים הקיימים בלבד: קליטת פניות, ניהול פניות ומחשבון אומדן.
- ברירת מחדל ל־Render Free בפרנקפורט ול־Neon באזור aws-eu-central-1.
- מניעת הכללת סודות או נתוני לקוחות בתוכנית ובלוגי שגיאה.

## בדיקת היתכנות והקמה מקצה לקצה

לאחר יצירת תוכנית Dry Run יש לבצע תחילה בדיקת Preflight לקריאה בלבד. בממשק המקומי משתמשים בכפתור „בצע בדיקת היתכנות לקריאה בלבד”. ב־CLI מריצים:

```powershell
py -m business_app.provisioning.execute --plan .\\work\\new-business-plan.json --preflight-only
```

ה־Preflight מבצע קריאות GET בלבד: מאמת גישה לחשבונות, בודק אם Repository פרטי / פרויקט Neon / שירות Render בשם המבוקש כבר קיימים, ומוודא התאמת אזור, מקור, branch ותוכנית Render. הבדיקה נכשלת במצב של משאב ציבורי, כפילות או תצורה לא תואמת. היא אינה יוצרת, מעדכנת או מוחקת משאבים, אינה מבצעת deploy ואינה מאמתת בפועל זכאות Free או מחיר עדכני.

רק לאחר עיון בדוח ואימות ידני של עלות אפס ניתן להפעיל את פקודת הביצוע ללא `--preflight-only`. זהו שלב שיוצר משאבים אמיתיים ולכן דורש אישור מפורש:

1. להגדיר משתני סביבה במחשב המפתח בלבד: `GITHUB_TOKEN`, `GITHUB_OWNER`, `NEON_API_KEY`, `RENDER_API_KEY`, `RENDER_WORKSPACE_ID` (ו־`NEON_ORG_ID` אם נדרש).
2. לוודא ש־Render מחובר לחשבון GitHub ושנבחרה סביבת העבודה הנכונה.
3. לוודא מול חשבון Neon שהפרויקט זכאי למסלול Free ושלא צפוי חיוב. אם אי אפשר לאמת עלות אפס — לא ממשיכים.
4. להפעיל את פקודת הביצוע:
   ```powershell
   py -m business_app.provisioning.execute --plan .\\work\\new-business-plan.json
   ```
5. להקליד `CREATE`, לאשר `ZERO-COST`, ולהזין פעמיים סיסמת מנהל ללקוח (לפחות 14 תווים).

מנגנון הביצוע:
- מבצע Preflight לכל שלושת הספקים לפני יצירת משאב כלשהו.
- מייצא Starter נקי מחוץ למאגר המקור, כולל business.json חדש וללא נתוני הלקוח הקיים.
- יוצר Repository פרטי, מפרסם אליו את קובצי ה־Starter באמצעות Git Data API ומוודא שאין קובצי סוד או קבצים לא צפויים.
- יוצר/מאתר פרויקט Neon, מקבל URI לחיבור ומעביר אותו ל־Render בזיכרון בלבד.
- יוצר/מאתר Web Service בתוכנית Free בפרנקפורט, עם DATABASE_URL, SECRET_KEY ו־ADMIN_PASSWORD_HASH. סיסמת המנהל עוברת hashing עם Werkzeug בזיכרון; הסיסמה הגלויה אינה נשלחת ל־Render ואינה נשמרת.
- ממתין לבדיקת `/health` לפני סימון ההקמה כהושלמה.
- שומר מצב התקדמות מקומי ב־`%USERPROFILE%\\.business-app-provisioner\\runs` (Windows) או `~/.business-app-provisioner/runs` (Linux/macOS). הקובץ אינו שומר סיסמאות, API keys או DATABASE_URL.

אם שלב נכשל, התהליך נעצר, מצב ההתקדמות נשמר והמשאבים שכבר נוצרו אינם נמחקים אוטומטית. ניסיון חוזר משתמש באיתור משאבים קיימים ובמנגנוני idempotency; במקרה של תוצאת כתיבה לא ידועה יש לבדוק את הספק לפני ניסיון נוסף. אין לבצע rollback אוטומטי שעלול למחוק משאב קיים או נתוני לקוח.

הבדיקה האוטומטית משתמשת ב־mocks בלבד ואינה יוצרת משאבי ענן. יצירת Render דורשת חיבור Git Provider פעיל. אם Render לא מחזיר URL או שה־health check נכשל, המצב לא יסומן כהושלם.

## Canva

Canva משמשת דרך חיבור Canva המאומת ב־ChatGPT ליצירת Brand Kit ונכסי עיצוב. החיבור הזה אינו טוקן מקומי שהאשף יכול להשתמש בו; אינטגרציה עצמאית מתוך האשף תחייב OAuth, scopes ואישור API נפרד. אין לשמור סיסמת Canva או טוקן בקובצי הפרויקט.

מדריך המתאמים והמשתנים נמצא ב־docs/PROVIDER_INTEGRATIONS_HE.md.

## יכולות ומגבלות מוצר

קטגוריית עסק אינה מפעילה יכולות שלא קיימות בקוד. מודולים כגון CRM מלא, מלאי, רכש, מסמכים, תורים, פרויקטים וכספים אינם חלק מה־Starter הנוכחי; בקשה למודול כזה תידחה עד שיפותח וייבדק בנפרד.

## אימות

```powershell
py -m pytest -q
```

הכלי אינו מיובא על ידי `app.py`, אינו מוסיף route לאפליקציה העסקית ואינו משנה את מסד הנתונים או את שירות Render הקיים.
