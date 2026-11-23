# אשף הקמת עסקים — כלי מפתח פנימי

## מטרה וגבולות

אשף ההקמה מיועד למפתח בלבד, כדי לקצר ולהאחיד הקמת אפליקציות עסקיות חדשות. הוא אינו מסך ניהול של לקוח, אינו חלק מהאפליקציה המיוצאת ואסור לחשוף אותו למשתמשי קצה.

כל התקנה עסקית נשארת עצמאית: Repository פרטי ב־GitHub, פרויקט PostgreSQL ב־Neon, שירות Render, סודות ונתוני לקוחות נפרדים.


## הפעלה פשוטה בפקודה אחת — Windows PowerShell

למי שמעדיף לא להפעיל פקודה אחר פקודה, המאגר כולל סקריפט PowerShell מרכזי בשם `RUN-BUSINESS-STARTER.ps1`. לאחר מיזוגו ל־`main`, אפשר לפתוח PowerShell רגיל ולהריץ את שלוש השורות הבאות. אין צורך בהרשאות מנהל:

```powershell
$scriptPath = Join-Path $env:TEMP "RUN-BUSINESS-STARTER.ps1"
Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/mv89-heb/business-mvp/main/RUN-BUSINESS-STARTER.ps1" -OutFile $scriptPath
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath
```

הסקריפט מציג תפריט בעברית:

1. **הכנה מלאה ובדיקות מקומיות** — זיהוי Git ו־Python 3.11, הצעת התקנה דרך winget אם חסרים, שכפול המאגר או עדכון `main`, יצירת `.venv`, התקנת תלויות, הרצת pytest ויצירת תוכנית Dry Run לעסק בדיקה. אין יצירת משאבי ענן.
2. **הפעלת האשף הגרפי** — יצירת סוד Session וסיסמת גישה בתהליך בלבד, הפעלה ב־`127.0.0.1` ופתיחת הדפדפן.
3. **Preflight לספקים** — קבלת מזהי ספקים ומפתחות API בקלט מוסתר, הגדרתם למשתני Process והרצת בדיקות קריאה בלבד.
4. **הקמה חיה** — אפשרות נפרדת, לאחר בדיקה ידנית של עלות Neon ואישור נוסף. כלי ה־CLI מבצע Preflight חדש ודורש הקלדת `CREATE` ו־`ZERO-COST` לפני יצירת משאבים.

הסקריפט אינו מדלג על שגיאות, אינו מבצע pull כאשר יש שינויים מקומיים לא שמורים, אינו שומר מפתחות API בקבצים, ואינו מוחק משאבים אוטומטית במקרה כשל. קובצי בדיקה ותוכניות מקומיות נשמרים תחת `work/`, שמוחרגת מ־Git. יצירת משאבי ענן אינה חלק מבדיקת ברירת המחדל.

## הרצה ידנית (חלופה)

מתוך סביבת הפיתוח של המאגר, ב־PowerShell:

```powershell
py -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
py -m business_app.provisioning --output .\\work\\new-business-plan.json
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

פתח בדפדפן: `http://127.0.0.1:8765`. במצב מקומי השרת מאזין ל־localhost בלבד ודוחה בקשות שאינן loopback. אין לשנות את ה־host ל־`0.0.0.0`. במצב המקומי נדרשים מפתח Session באורך 32 תווים לפחות וסיסמת מפתח באורך 16 תווים לפחות. מסך ההתחברות מוגבל לחמישה ניסיונות בדקה. אירועי כניסה, בדיקות חיבור, Preflight והקמה נרשמים בקובץ Audit מצומצם ללא סיסמאות או מפתחות.

כדי לבדוק חיבורי ספקים ולהפעיל הקמה מתוך הממשק, הגדר באותה חלונית PowerShell גם את משתני הסביבה `GITHUB_TOKEN`, `GITHUB_OWNER`, `NEON_API_KEY`, `RENDER_API_KEY`, `RENDER_WORKSPACE_ID` ו־`NEON_ORG_ID` (חובה). אין להקליד מפתחות API בשדות הטופס או לשמור אותם בקובצי הפרויקט.

טופס ההקמה מציג תחילה תוכנית Dry Run. לאחר מכן יש להריץ Preflight לקריאה בלבד, שבודק הרשאות, שמות משאבים קיימים והתאמת תצורה ב־GitHub, Neon ו־Render. רק אם הבדיקה הצליחה ייפתח טופס הביצוע, שבו נדרשים אישור `CREATE`, אישור ידני לעלות אפס ב־Neon וסיסמת מנהל ללקוח. בקשת הביצוע נשארת פתוחה בזמן יצירת המשאבים והמתנה ל־`/health`, ולכן ייתכן שיידרשו כמה דקות. סיסמת מנהל הלקוח אינה מוצגת שוב ואינה נשמרת במצב התהליך. לפני העברתה ל־Render היא מומרת ל־Werkzeug password hash; רק ה־hash נשמר במשתני הסביבה של השירות.

## מה קיים בשלב זה

- תכנון התקנה נפרדת ל־GitHub, Neon ו־Render.
- בדיקת חיבור חיה ל־GitHub, Neon ו־Render מתוך ממשק האשף, כאשר משתני הסביבה מוגדרים.
- Preflight לקריאה בלבד שמאתר Repository פרטי קיים, פרויקט Neon קיים ושירות Render קיים, ומכשיל תצורה לא תואמת; אינו יוצר, מעדכן או מוחק משאבים.
- מתאמי API נפרדים: GitHub יכול ליצור Repository פרטי; Neon יכול לאתר/ליצור פרויקט ולהחזיר URI; Render יכול לאתר/ליצור Web Service בתוכנית Free בלבד.
- איתור משאבים קיימים לפני יצירה, טיפול בכפילויות ו-pagination. משאב קיים לא יאומץ לפי שם בלבד: נדרש מזהה משאב תואם שנשמר במצב של אותה תוכנית הקמה; אחרת הבדיקה נעצרת בבטחה.
- טיפול זהיר ב-timeout של בקשת כתיבה: התוצאה מסומנת כלא ידועה, ואין retry אוטומטי.
- בחירת קטגוריית עסק והמודולים הממומשים בקוד: קליטת פניות, ניהול פניות, מחשבון אומדן, WhatsApp inbound, API לאוטומציות ו־callback לסיווג AI. מודולי WhatsApp/Automation/AI עדיין דורשים חיבור חשבונות ו־workflow חיצוני.
- ברירת מחדל ל־Render Free בפרנקפורט ול־Neon באזור aws-eu-central-1.
- מניעת הכללת סודות או נתוני לקוחות בתוכנית ובלוגי שגיאה.

## בדיקת היתכנות והקמה מקצה לקצה

לאחר יצירת תוכנית Dry Run יש לבצע תחילה בדיקת Preflight לקריאה בלבד. בממשק המקומי משתמשים בכפתור „בצע בדיקת היתכנות לקריאה בלבד”. ב־CLI מריצים:

```powershell
py -m business_app.provisioning.execute --plan .\\work\\new-business-plan.json --preflight-only
```

ה־Preflight מבצע קריאות GET בלבד: מאמת גישה לחשבונות, מחייב NEON_ORG_ID כדי לתחום את חיפוש הפרויקטים לארגון המאושר, בודק אם Repository פרטי / פרויקט Neon / שירות Render בשם המבוקש כבר קיימים, ומוודא התאמת אזור, מקור, branch ותוכנית Render. הבדיקה נכשלת במצב של משאב ציבורי, כפילות או תצורה לא תואמת. היא אינה יוצרת, מעדכנת או מוחקת משאבים, אינה מבצעת deploy ואינה מאמתת בפועל זכאות Free או מחיר עדכני.

רק לאחר עיון בדוח ואימות ידני של עלות אפס ניתן להפעיל את פקודת הביצוע ללא `--preflight-only`. זהו שלב שיוצר משאבים אמיתיים ולכן דורש אישור מפורש:

1. להגדיר משתני סביבה במחשב המפתח בלבד: `GITHUB_TOKEN`, `GITHUB_OWNER`, `NEON_API_KEY`, `RENDER_API_KEY`, `RENDER_WORKSPACE_ID` ו־`NEON_ORG_ID` (חובה).
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

אם שלב נכשל, התהליך נעצר, מצב ההתקדמות נשמר והמשאבים שכבר נוצרו אינם נמחקים אוטומטית. ניסיון חוזר משתמש באיתור משאבים קיימים ובמנגנוני idempotency, ורק מזהי GitHub/Neon/Render שנשמרו באותה תוכנית מאפשרים שימוש חוזר. משאב שנוצר אך מזההו לא נשמר לאחר timeout לא יאומץ אוטומטית; יש לבדוק אותו ידנית ולפתור את המצב לפני ניסיון נוסף. אין לבצע rollback אוטומטי שעלול למחוק משאב קיים או נתוני לקוח.

הבדיקה האוטומטית משתמשת ב־mocks בלבד ואינה יוצרת משאבי ענן. יצירת Render דורשת חיבור Git Provider פעיל. אם Render לא מחזיר URL או שה־health check נכשל, המצב לא יסומן כהושלם.

## פרטיות AI ואוטומציות

טקסט חופשי מפרטי ליד אינו נשלח ל־Outbox כברירת מחדל. כדי לאפשר העברת טקסט ל־Make/n8n לצורך סיווג Gemini יש להגדיר במפורש `AI_ALLOW_FREE_TEXT=true` בסביבת הלקוח. במצב opt-in מתבצעת השחרת טלפונים ודוא״ל בלבד; היא best-effort ואינה מבטיחה הסרת שמות או כתובות. אין להפעיל זאת לפני בדיקת פרטיות והגדרת סינון נוסף ב־Make/n8n. ה־API הבסיסי כולל שליפת Outbox, ACK ו־callback מאומת; אין כרגע workflow פעיל או חיבור חשבון Make/n8n מתוך האשף.

## Canva

Canva משמשת דרך חיבור Canva המאומת ב־ChatGPT, כאשר הוא מחובר, ליצירת Brand Kit ונכסי עיצוב. החיבור הזה אינו טוקן מקומי שהאשף יכול להשתמש בו; אינטגרציה עצמאית מתוך האשף תחייב OAuth, scopes ואישור API נפרד. אין לשמור סיסמת Canva או טוקן בקובצי הפרויקט.

מדריך המתאמים והמשתנים נמצא ב־docs/PROVIDER_INTEGRATIONS_HE.md.

## יכולות ומגבלות מוצר

קטגוריית עסק אינה מפעילה יכולות שלא קיימות בקוד. מודולים כגון CRM מלא, מלאי, רכש, מסמכים, תורים, פרויקטים וכספים אינם חלק מה־Starter הנוכחי; בקשה למודול כזה תידחה עד שיפותח וייבדק בנפרד.

## אימות

```powershell
py -m pytest -q
```

הכלי אינו מיובא על ידי `app.py`, אינו מוסיף route לאפליקציה העסקית ואינו משנה את מסד הנתונים או את שירות Render הקיים.

## תוכנית בדיקות מעשית — מתי ואיך מתחילים

### מצב מוכנות נוכחי

אפשר להתחיל בבדיקות המקומיות וב־Preflight לקריאה בלבד על בסיס `main`. יש לבדוק את ריצת GitHub Actions העדכנית בענף `main` לפני מסירה. בדיקות האינטגרציה האוטומטיות משתמשות ב־mocks בלבד; הן אינן מוכיחות עדיין הקמה אמיתית או חיבור חי לחשבונות הספקים.

### שלב 1 — בדיקה מקומית ללא חשבונות ענן

1. שכפל את המאגר למחשב Windows או עדכן עותק קיים ל־`main`.
2. פתח PowerShell בתיקיית המאגר והריץ:

   ```powershell
   py -3.11 -m venv .venv
   .\\.venv\\Scripts\\Activate.ps1
   python -m pip install -r requirements.txt
   python -m pytest -q
   ```

3. ודא שכל הבדיקות עוברות.
4. הפק תוכנית בדיקה עם שם עסק ו־slug ייחודיים שאינם שייכים ללקוח אמיתי:

   ```powershell
   New-Item -ItemType Directory -Force .\\work | Out-Null
   py -m business_app.provisioning --output .\\work\\test-business-plan.json
   ```

5. פתח את קובץ ה־JSON ובדוק שהשם, ה־slug, הקטגוריה והמודולים נכונים. בשלב זה לא נוצרים משאבים ולא נדרשים מפתחות API.

### שלב 2 — בדיקות חיבור ו־Preflight לקריאה בלבד

נדרשים חשבונות פעילים ב־GitHub, Neon ו־Render, ומפתחות API בעלי הרשאות מתאימות. הגדר את הערכים רק בחלון PowerShell הנוכחי; אל תשלח אותם בצ'אט, אל תכניס אותם לקובץ ואל תשמור אותם ב־Git.

```powershell
$env:GITHUB_OWNER = "your-github-owner"

function Set-ProcessSecret {
    param([string]$Name, [string]$Prompt)
    $secure = Read-Host $Prompt -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        [Environment]::SetEnvironmentVariable(
            $Name,
            [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr),
            "Process"
        )
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        $secure.Dispose()
    }
}

Set-ProcessSecret "GITHUB_TOKEN" "GitHub API token"
Set-ProcessSecret "NEON_API_KEY" "Neon API key"
Set-ProcessSecret "RENDER_API_KEY" "Render API key"

$env:NEON_ORG_ID = "your-approved-neon-organization-id"
$env:RENDER_WORKSPACE_ID = "your-approved-render-workspace-id"
```

לאחר מכן הרץ:

```powershell
py -m business_app.provisioning.execute --plan .\\work\\test-business-plan.json --preflight-only
```

תוצאה תקינה צריכה להראות חיבור מאומת לכל שלושת הספקים, סביבת Render תואמת, ארגון Neon מוגדר, וללא התנגשויות או משאבים קיימים שלא שייכים לתוכנית. Preflight הוא לקריאה בלבד: הוא אינו יוצר, מעדכן, מוחק או פורס משאבים.

אם מתקבלת שגיאה — עוצרים ומתקנים אותה. אין לעקוף בדיקת הרשאות, בעלות, ארגון או התאמת משאב.

### שלב 3 — הקמה חיה של עסק בדיקה מבודד

שלב זה יוצר משאבים אמיתיים בחשבונות שלך. לא מתחילים בו לפני שכל התנאים הבאים מתקיימים:

- GitHub token מסוגל ליצור Repository פרטי תחת ה־owner שנבחר.
- Render API key יכול לגשת בדיוק ל־workspace שנבחר, וחשבון Render מחובר ל־GitHub כ־Git Provider.
- `NEON_ORG_ID` הוא מזהה הארגון המיועד בלבד.
- בדקת בחשבון Neon את תנאי התוכנית, המכסות והחיוב בפועל, ווידאת שהפעולה המסוימת זכאית לעלות אפס. אישור `ZERO-COST` הוא הצהרת המפתח בלבד, לא בדיקת מחיר אוטומטית.
- ה־slug ייחודי לחלוטין לעסק הבדיקה, ואין משאבים קיימים באותו שם שאינך מזהה.
- Preflight עבר מיד לפני ההקמה.

רק אז הרץ:

```powershell
py -m business_app.provisioning.execute --plan .\\work\\test-business-plan.json
```

הקלד `CREATE`, אשר `ZERO-COST`, והזן סיסמת מנהל ייעודית לבדיקה. אין להשתמש בסיסמת המנהל של עסק קיים. האשף ינסה ליצור Repository פרטי, פרויקט Neon ושירות Render Free, לפרסם Starter נקי, להגדיר סודות ולהמתין ל־`/health`.

### שלב 4 — בדיקות קבלה לאחר הפריסה

רק לאחר שה־workflow דיווח על הצלחה ובידך כתובת Render:

1. פתח את כתובת השירות ובדוק שהאתר נטען.
2. פתח את `/health` וודא שמתקבלת תשובת HTTP תקינה.
3. פתח את מסך הניהול והתחבר עם סיסמת הבדיקה.
4. שלח ליד בדיקה עם פרטי דמה, וודא שהוא נשמר במסד Neon החדש ומופיע במסך הניהול.
5. ודא שה־Repository החדש פרטי, מכיל Starter נקי ואינו מכיל סודות או נתוני עסק אחר.
6. ודא ב־Render שהשירות נמצא ב־Free ובאזור Frankfurt, וב־Neon שהפרויקט נמצא בארגון ובתוכנית שאישרת.
7. בדוק שאין חיבור למסד הנתונים או לשירות של עסק קיים.

### עצירה, כשל וניקוי

- אם שלב נכשל, אל תריץ שוב את ההקמה באופן עיוור. בדוק קודם את מצב התהליך המקומי ואת המשאבים שנוצרו בכל ספק. שימוש חוזר מותר רק עם מזהי המשאבים שנשמרו לאותה תוכנית.
- אין מחיקה אוטומטית במקרה כשל, כדי לא לפגוע במשאב או בנתונים קיימים.
- בסיום בדיקות הקבלה, אם אין צורך להשאיר את סביבת הבדיקה, מחק ידנית ורק לאחר אימות בעלות את שירות Render, פרויקט Neon וה־Repository הייעודיים לבדיקה. ודא תחילה שאין בהם מידע שצריך לשמור.
- סגור את חלון PowerShell שבו הוגדרו מפתחות ה־API; משתני הסביבה הוגדרו ל־Process בלבד ואינם אמורים להישמר מעבר לתהליך.
- אין לבצע בדיקת הקמה ראשונה על Repository, Neon project או Render service של לקוח אמיתי.


## פריסת Developer Studio ב־Render — מצב גישה מרחוק

האשף רץ כתהליך נפרד מהאפליקציה העסקית. השירות הקיים במרחב Render הוא `business-developer-studio` בכתובת:

`https://business-developer-studio.onrender.com`

הגדרות השירות שנבדקו ב־Render:
- Repository: `mv89-heb/business-mvp`, branch: `main`.
- Build: `pip install -r requirements.txt`
- Start: `gunicorn --workers 1 --bind 0.0.0.0:$PORT --timeout 60 provisioning_wsgi:app`
- Runtime: Python, Frankfurt, Free.
- יש להגדיר ידנית ב־Render Dashboard את Health Check Path ל־`/healthz`. בבדיקה האחרונה השדה היה ריק, ולכן Render עדיין לא השתמש בנתיב הבריאות הייעודי.

משתני סביבה נדרשים ב־Render:
- `PROVISIONING_PUBLIC_ACCESS=true`
- `PROVISIONING_ALLOWED_HOSTS=business-developer-studio.onrender.com` — hostname מדויק בלבד, בלי scheme או path.
- `PROVISIONING_SESSION_SECRET` — סוד אקראי חזק באורך 32 תווים לפחות.
- `PROVISIONING_PASSWORD` — סיסמה אקראית ייעודית באורך 32 תווים לפחות; לא סיסמה שמשמשת במקום אחר.
- `PROVISIONING_RATE_LIMIT_STORAGE_URI=memory://` כברירת מחדל.
- `DATABASE_URL` או `PROVISIONING_DATABASE_URL` — מחרוזת חיבור PostgreSQL של פרויקט Neon הייעודי ל־Business Studio. יש להעתיק אותה ישירות ממסך Neon אל Render Environment; לעולם לא להדביק אותה בצ'אט, ב־GitHub או בלוגים.
- `CUSTOMER_CREDENTIALS_ENCRYPTION_KEY` — מפתח אקראי של 32 bytes בקידוד URL-safe Base64. יש לייצר מפתח חדש, לשמור אותו רק ב־Render Secret Environment ולשמור עותק גיבוי בכספת סודות מאובטחת. אין לשנות או לייצר מחדש את המפתח לאחר שנשמרו חיבורים מוצפנים, אחרת לא ניתן יהיה לפענח אותם.
- `STUDIO_COMMAND_PRIVATE_KEY_B64` — חובה במצב גישה מרחוק. מפתח RSA-3072 פרטי בפורמט PKCS#8 PEM, מקודד Base64. יש לייצר ולשמור אותו רק כ־Render Secret לפי ההוראות ב־`docs/GITHUB_RENDER_COMMAND_RELAY_HE.md`; אין לשמור אותו בקוד, ב־GitHub Issues או בלוגים. יש לשמור את אותו מפתח לאורך הפעלות מחדש כדי שמעטפות פקודה קיימות לא יאבדו יכולת פענוח.

**בדיקת מוכנות:** לאחר שמירת המשתנים והפעלת השירות, פתח את `https://business-developer-studio.onrender.com/healthz`. תשובת HTTP 200 עם `{"status":"ok"}` מעידה שבדיקת החיבור למסד הנתונים, מפתח כספת חיבורי הספקים ומפתח הפקודות הקבוע עברה. תשובת 503 פירושה שאחד מתנאי המוכנות חסר או נכשל. במצב מרוחק, מפתח פקודות חסר או לא תקין גורם לכשל סגור; יצירת מפתח זמני מותרת רק בהרצה מקומית. אין להחשיב GET למסך ההתחברות או HTTP 200 של דף הבית כהוכחה שמסד הנתונים והמפתחות תקינים. אם הנתיב אינו נגיש מבחוץ, בדוק את ה־Request Logs ואת Health Check ב־Render; אל תעקוף את בדיקת המוכנות.

במצב זה האפליקציה דורשת Host מדויק, HTTPS, session cookie מאובטח, CSRF פעיל, HSTS והגבלת ניסיונות. יש להשאיר worker יחיד כדי שמגבלת הניסיונות בזיכרון לא תתפצל בין תהליכים. אין לפרוס את האשף דרך `wsgi:app`, שמפעיל את אפליקציית הלידים. אין להוסיף סודות GitHub/Neon/Render עד שהשירות מוגן ונבדק; לאחר מכן יש להוסיף רק מפתחות עם ההרשאות המזעריות הנדרשות.

**הגבלת אבטחה:** סיסמה חזקה והגבלת ניסיונות מגנות על מסך הכניסה, אך אינן מספקות MFA או Zero Trust. לפני חיבור מפתחות ענן בעלי הרשאות כתיבה, מומלץ להוסיף Cloudflare Access או VPN, ולהפעיל התראות על ניסיונות כניסה. אין לשתף את כתובת האשף או את הסיסמה.
