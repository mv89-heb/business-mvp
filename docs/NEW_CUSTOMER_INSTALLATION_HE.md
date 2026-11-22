# מדריך הקמת מערכת עסקית חדשה ונפרדת

המדריך מתאר תהליך מלא ליצירת התקנה עצמאית של Business Application Starter עבור עסק חדש, על בסיס קוד המקור הנקי.

> **עיקרון אבטחה:** כל עסק מקבל מאגר GitHub, מסד נתונים, שירות Render, מפתחות וסיסמת מנהל משלו. אין לחבר התקנה חדשה למסד הנתונים או לסודות של עסק אחר. אין להעתיק נתוני לקוחות, לידים, קובצי העלאה או קובצי סביבה.

## מפת התהליך

1. יצירת עותק קוד נקי באמצעות ה־Exporter.
2. יצירת Repository חדש ב־GitHub והעלאת הקוד.
3. יצירת פרויקט PostgreSQL חדש ב־Neon.
4. יצירת Web Service חדש ב־Render וחיבורו ל־Repository החדש.
5. הגדרת משתני הסביבה והסודות.
6. בדיקות, פריסה ואימות שהמערכת החדשה מבודדת ופועלת.

---

## שלב 1 — יצירת עותק קוד נקי ב־Windows

יש להתקין Git ו־Python 3.11, ולוודא שפקודות `git`, `py` ו־`python` זמינות ב־PowerShell.

פתח PowerShell בתיקייה שבה תרצה לשמור את הפרויקטים והריץ:

```powershell
git clone https://github.com/mv89-heb/business-mvp.git
Set-Location .\business-mvp
git switch main
git pull
```

צור התקנה חדשה באמצעות כלי הייצוא המובנה. יש להחליף את שם העסק ואת ה־slug:

```powershell
python -m business_app.export_starter --target ..\new-business-app --business-name "New Business" --slug "new-business"
Set-Location ..\new-business-app
```

ה־Exporter יוצר תיקייה נקייה ואינו מעתיק את הגדרות העסק החי, קובצי Git, סודות, מסדי נתונים מקומיים, לידים, העלאות או לוגים. הוא יוצר `config/business.json` חדש מתוך תבנית ניטרלית ומעתיק את תבנית Render לקובץ `render.yaml`.

אם הייצוא נכשל משום שתיקיית היעד כבר קיימת, בחר נתיב חדש וריק. אין למחוק תיקייה קיימת בלי לבדוק את תוכנה.

### התאמת פרטי העסק

פתח את `config/business.json` ועדכן את שם העסק, מזהה העסק, פרטי קשר, תוכן, שירותים, מיתוג, שפה/כיווניות ומודולים פעילים. אין להכניס לקובץ הזה סיסמאות, מפתחות API או כתובות חיבור למסדי נתונים.

בדוק את `render.yaml` ועדכן את `name` לשם ייחודי לשירות החדש. השאר את `DATABASE_URL` ואת `ADMIN_PASSWORD_HASH` כמשתנים שיסופקו באופן מאובטח ב־Render. אין להגדיר סיסמה גלויה במשתנה סביבה בפריסה.

---

## שלב 2 — יצירת Repository חדש ב־GitHub

1. היכנס ל־GitHub ולחץ על **New repository**.
2. בחר שם חדש, למשל `new-business-app`.
3. מומלץ לבחור **Private** עבור מערכת עסקית.
4. אל תוסיף README, .gitignore או License ביצירת המאגר — העותק המקומי כבר מכיל את הקבצים הדרושים.
5. צור את המאגר הריק והעתק את כתובת ה־HTTPS שלו.

מתוך תיקיית הפרויקט החדשה ב־PowerShell הרץ, תוך החלפת כתובת המאגר:

```powershell
git init
git branch -M main
git add .
git status
```

לפני יצירת commit, בדוק ב־`git status` שלא מופיעים קובצי סביבה, סודות, מסדי נתונים, קובצי גיבוי, לידים או קבצים פרטיים. אם מופיע מידע כזה, עצור והסר אותו מהעותק לפני ההעלאה.

לאחר הבדיקה:

```powershell
git commit -m "Initial clean business application"
git remote add origin https://github.com/OWNER/NEW-REPOSITORY.git
git push -u origin main
```

החלף את `OWNER/NEW-REPOSITORY` בפרטי המאגר החדש. אם GitHub מבקש אימות, השלם אותו דרך מנגנון האימות הרשמי.

### הגדרות Repository מומלצות

- השאר את המאגר Private כל עוד לא הוחלט אחרת.
- ודא ש־GitHub Actions מופעל ושבדיקות ה־CI עוברות.
- הגן על ענף `main` לפי הצורך: Pull Request ובדיקות לפני מיזוג.
- אל תפעיל **Template repository** על מאגר המקור שמכיל את הגדרות העסק הקיים.
- אפשר להפוך את המאגר הנקי החדש ל־Template רק לאחר בדיקת תוכן, CI ופריסה עצמאית מוצלחת. לאחר מכן ניתן להשתמש ב־**Use this template** עבור התקנות נוספות.

---

## שלב 3 — יצירת מסד נתונים חדש ב־Neon

1. היכנס ל־[Neon Console](https://console.neon.tech/).
2. בחר **Create project**.
3. תן לפרויקט שם ייחודי לעסק החדש.
4. בחר אזור קרוב לשירות Render. אם השירות נפרס בפרנקפורט, בחר אזור אירופי מתאים הזמין בחשבון.
5. צור את הפרויקט ואת מסד הנתונים.
6. העתק את מחרוזת החיבור של PostgreSQL (רצוי Pooled connection string אם האפליקציה ותצורת החיבור תומכות בכך).
7. שמור אותה באופן זמני במקום מאובטח בלבד. אין להדביק אותה בקוד, ב־README, ב־GitHub Issues או בצ'אט ציבורי.

מחרוזת החיבור תוגדר ב־Render כערך של `DATABASE_URL`. יש להשתמש בפרויקט ובמסד נתונים חדשים, ולא במחרוזת החיבור של עסק אחר.

> מסד נתונים חדש מתחיל ללא נתוני העסק הקודם. יש לוודא שתהליך האתחול/יצירת הטבלאות של גרסת האפליקציה הנוכחית רץ בהצלחה לפני שימוש. אין לייבא גיבוי של העסק הקיים כהתחלה להתקנה חדשה.

---

## שלב 4 — יצירת שירות Render חדש

1. היכנס ל־[Render Dashboard](https://dashboard.render.com/).
2. בחר את ה־Workspace המתאים.
3. לחץ **New +** ובחר **Blueprint** אם ברצונך ש־Render יקרא את `render.yaml` מהמאגר, או **Web Service** אם אתה מגדיר את השירות ידנית.
4. חבר את חשבון GitHub ובחר את ה־Repository החדש שיצרת בשלב 2.
5. אם בחרת Blueprint, בדוק את כל הערכים שמוצגים לפני אישור היצירה.
6. אם בחרת Web Service, הגדר:
   - Runtime: Python
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn --workers 1 --threads 4 --timeout 60 wsgi:app`
   - Health Check Path: `/health`
   - Region: אזור מתאים, רצוי קרוב ל־Neon
   - Instance plan: בחר תוכנית זמינה ומתאימה. אם נדרשת התקנה ללא עלות, ודא במסך Render שהתוכנית החינמית זמינה ומתאימה למגבלות השימוש הנוכחיות לפני יצירת השירות.

**אל תבחר שירות קיים של עסק אחר ואל תעדכן את השירות הקיים.** יש לוודא ש־Render יוצר Service ID חדש ושכתובת השירות החדשה שונה מכתובת המערכת המקורית.

### משתני סביבה ב־Render

הגדר בשירות החדש את המשתנים הבאים:

| משתנה | ערך / אופן הגדרה |
|---|---|
| `PYTHON_VERSION` | `3.11.9` |
| `BUSINESS_CONFIG_PATH` | `config/business.json` |
| `DATABASE_URL` | מחרוזת החיבור של פרויקט Neon החדש בלבד |
| `SECRET_KEY` | מפתח אקראי חדש; אפשר להשתמש ב־`generateValue: true` ב־Blueprint |
| `ADMIN_PASSWORD_HASH` | Hash של סיסמת מנהל חדשה וחזקה, להגדיר כ־Secret; אין לשמור את הסיסמה הגלויה ב־Render |
| `FLASK_ENV` | `production` |
| `ESTIMATE_BASE_RATE` | `0` עד שבעל העסק מאשר תעריף אמיתי |
| `ESTIMATE_RATE_PER_SQM` | `0` עד שבעל העסק מאשר תעריף אמיתי |

כדי להפיק Hash בלי להציג את הסיסמה בשורת הפקודה, אפשר להריץ ב־PowerShell מתוך סביבת Python של הפרויקט: `python -c "from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass('Admin password: ')))"`. העתק ל־Render רק את פלט ה־Hash תחת `ADMIN_PASSWORD_HASH`.\n\nאם מוגדר `LEAD_WEBHOOK_URL`, יש להגדיר גם `LEAD_WEBHOOK_SECRET` חדש ומתאים ולבצע את ההגדרה התואמת בצד המקבל. אין להעתיק כתובת Google Apps Script, מזהה Sheet או webhook מעסק אחר. אם אין צורך באינטגרציה, השאר אותה כבויה ואל תגדיר URL.

אין להוסיף סודות ל־`render.yaml` או ל־GitHub. השתמש במסך Environment של Render ובשדות Secret המתאימים.

---

## שלב 5 — פריסה ובדיקות

לאחר שמירת משתני הסביבה, הפעל Deploy ועקוב אחר Build ו־Deploy Logs.

במחשב המקומי, אפשר להריץ בדיקות בסיסיות בעותק החדש:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:SECRET_KEY = "local-development-only-change-me"
$env:ADMIN_PASSWORD = "local-development-only-password"  # local development only; use ADMIN_PASSWORD_HASH in Render
$env:DATABASE_URL = "sqlite:///local.db"
python -m business_app.bootstrap --check-env
python -m pytest -q
```

הערה: בדיקת SQLite מקומית אינה מחליפה בדיקת חיבור ל־Neon. בדיקות מול מסד הנתונים החדש יש לבצע בסביבה החדשה, ורק לאחר שהוגדרו בה משתני הסביבה הנכונים.

לאחר שהפריסה ב־Render מסתיימת:

1. פתח את כתובת השירות החדש.
2. בדוק ש־`/health` מחזיר HTTP 200.
3. בדוק טעינת דף הבית, טופס ליד ומסך הניהול.
4. ודא שניתן להתחבר באמצעות סיסמת המנהל החדשה.
5. בצע ליד בדיקה אחד בסביבה החדשה בלבד, לאחר שאושרו יעד השמירה והאינטגרציות.
6. ודא שהליד מופיע במסד הנתונים החדש ולא במערכת המקורית.
7. בדוק את לוגי Render ואת תוצאות GitHub Actions.

אל תכריז על התקנה מוכנה לשימוש לפני שכל הבדיקות הרלוונטיות עברו.

---

## שלב 6 — רשימת אימות לפני מסירה

- [ ] Repository חדש ונפרד ב־GitHub.
- [ ] אין בקוד שהועלה נתוני לקוחות, סודות או קובצי סביבה.
- [ ] קובץ `config/business.json` מכיל רק הגדרות של העסק החדש.
- [ ] פרויקט Neon חדש ומחרוזת חיבור ייחודית.
- [ ] שירות Render חדש עם Service ID וכתובת נפרדים.
- [ ] `SECRET_KEY` וסיסמת מנהל חדשים.
- [ ] אינטגרציות חיצוניות כבויות עד לאישור והגדרה מפורשת.
- [ ] GitHub Actions ובדיקות הפרויקט עברו.
- [ ] Render Build ו־Deploy הצליחו.
- [ ] נקודת `/health` מחזירה HTTP 200.
- [ ] נבדקו התחברות, טופס ליד ושמירה במסד הנתונים החדש.
- [ ] הוכח שהמערכת החדשה אינה קוראת או כותבת נתונים במערכת המקורית.

## עלויות ומגבלות

הקוד וכלי הייצוא אינם דורשים שירות בתשלום. עם זאת, זמינות מסלולי Free, מגבלות שימוש, שינה אוטומטית, אחסון, חיבורי מסד נתונים ותנאי ספקי הענן עשויים להשתנות. לפני יצירת משאבים יש לבדוק את התוכנית והחיוב שמוצגים בפועל ב־GitHub, Neon ו־Render. אין לאשר שדרוג בתשלום או להוסיף אמצעי תשלום ללא אישור בעל החשבון.

## מסמכים קשורים

- [תהליך ייצוא ושכפול](CLONING.md)
- [הגדרת GitHub Template](GITHUB_TEMPLATE_REPOSITORY.md)
- [ארכיטקטורה](ARCHITECTURE.md)
- [העברת בעלות ותפעול](HANDOVER.md)
