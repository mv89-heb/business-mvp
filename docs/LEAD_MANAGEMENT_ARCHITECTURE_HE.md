# ארכיטקטורת מערכת לידים ואוטומציה

## מטרה

כל התקנה עסקית היא מערכת עצמאית לקליטה, תיעוד, ניהול והמרה של לידים. האתר ו־WhatsApp הם ערוצי הכניסה; Neon הוא מקור האמת; ממשק CRM הוא מסך העבודה; Make או n8n מבצעים אוטומציות; Gemini מסייע בסיווג; Canva מייצרת שפה ונכסי מותג; GPT מתזמר את עבודת המפתח.

## הפרדה בין רכיבים

1. **Developer Studio / Wizard** — כלי פרטי למפתח בלבד, מקומי ב־127.0.0.1 עד להשלמת הקשחת גישה מרחוק.
2. **Customer Lead App** — יישום נפרד לכל לקוח: אתר, CRM, Webhooks ו־API.
3. **GitHub** — מאגר קוד פרטי נפרד לכל התקנה.
4. **Render** — שירות Web נפרד לכל התקנה, עם deploy שממתין לבדיקות CI.
5. **Neon** — פרויקט ו־DB נפרדים לכל התקנה. אין שיתוף טבלאות או credentials בין לקוחות.
6. **Canva** — תבניות מותג ונכסים נפרדים לכל עסק.
7. **Automation** — n8n או Make, עם credentials נפרדים לכל לקוח/ערוץ.
8. **Gemini** — סיווג ועיבוד טקסט מצומצם; ללא PII כברירת מחדל.
9. **GPT** — תזמור פעולות בין הכלים המחוברים, בדיקות, החלטות ואישורים. אין להניח ש־ChatGPT subscription מעניק API usage.

## זרימת ליד מהאתר

1. טופס האתר מבצע ולידציה, CSRF, honeypot ו־rate limit.
2. הליד נשמר ב־Neon לפני קריאה לכל שירות חיצוני.
3. נשמרים מקור, זמן הסכמה, פרטי הפנייה ו־event id.
4. אירוע `lead.created` נכנס ל־outbox עמיד.
5. Make/n8n מקבל את האירוע, מבצע סיווג באמצעות Gemini ומחזיר תוצאה ל־callback מאומת.
6. ה־CRM מציג את הסיווג כהמלצה; בעל העסק מחליט על סטטוס והמשך טיפול.

## זרימת ליד מ־WhatsApp

1. Meta שולחת GET verification ו־POST events ל־HTTPS endpoint.
2. GET מאומת מול Verify Token; POST מאומת מול `X-Hub-Signature-256` ו־App Secret.
3. הודעה מזוהה לפי provider message ID ונשמרת פעם אחת בלבד.
4. הליד מותאם לפי מספר טלפון מנורמל; אם לא נמצא ליד, נוצר ליד חדש עם source=whatsapp.
5. נשמרת היסטוריית הודעות נכנסות וקישור לליד.
6. אירוע נשלח ל־outbox לעיבוד AI/אוטומציה.
7. אין שליחה יוצאת אוטומטית ללא opt-in/חלון שירות/תבנית מאושרת ובדיקת עלות.

## מודל נתונים יעד

- `lead`: פרטי קשר ונתוני הפנייה העסקית.
- `lead_metadata`: מקור, מזהה חיצוני, בעלים, עדיפות, תקציר AI, קטגוריה, tags ותאריך follow-up.
- `lead_message`: הודעות נכנסות/יוצאות, ספק, message ID, זמן, תוכן וסוג.
- `lead_event`: ציר זמן של יצירה, שינוי סטטוס, הקצאה, הערה והעברת הודעה.
- `integration_outbox`: אירועים להזרמה, סטטוס delivery, attempts, שגיאה אחרונה וזמן אישור.

כל שינוי סכימה יהיה additive ו־idempotent. אין לשנות או למחוק נתוני לידים קיימים. יש לגבות לפני migration ולבדוק מול branch נפרד ב־Neon.

## אבטחה ופרטיות

- Secrets נשמרים ב־Render Environment או במנהל סודות; לעולם לא ב־Git, JSON, logs או Dry Run.
- כל Webhook חיצוני דורש אימות חתימה או bearer secret, rate limiting ו־idempotency.
- אין לשמור payload מלא של Meta אם אין צורך; לשמור רק שדות נדרשים.
- PII לא נשלח ל־Gemini כברירת מחדל. יש להשחיר טלפונים, כתובות, שמות ודוא״ל לפני עיבוד.
- תוצאות AI הן הצעות בלבד; אין לבצע שליחה, שינוי מחיר או סגירת עסקה אוטומטיים.
- הפרדה מלאה בין tenant/customer: repository, DB, service, secrets, webhook tokens, Canva assets ו־automation credentials.
- מסך Wizard לא יפורסם באינטרנט לפני אימות משתמש חזק, MFA/SSO לפי הצורך, CSRF, RBAC, audit log, session hardening ובדיקות אבטחה.

## תפעול ועלויות

- Render: Free בלבד כברירת מחדל; אין להניח זמינות רציפה של Free instance.
- Neon: יש לבדוק זכאות Free, compute/storage/egress לפני כל יצירה. אין לאשר עלות אפס על סמך ברירת מחדל.
- Gemini: קיימת שכבת Free למודלים מסוימים, אך תנאי שימוש בנתונים ומכסות משתנים. יש לאמת לפני שימוש בנתוני לקוחות.
- Make Free: מתאים לניסוי; מגבלת credits ו־schedule interval אינה תחליף לתור production.
- n8n Community: ניתן להפעלה עצמית ללא דמי רישוי במקרים המותרים ברישיון; אין לתת ללקוחות גישה לעורך/לוגיקת workflows אם פועלים תחת Sustainable Use License. אין להציג n8n כשירות מתארח ללקוחות ללא רישיון מתאים.
- WhatsApp Business Platform: אין להבטיח שימוש חינמי; תמחור Meta תלוי בסוג/מדינת ההודעה והחשבון.
- Canva: חיבור API עצמאי דורש Developer App ו־OAuth; חיבור Canva בתוך ChatGPT אינו credential של האשף.

## מצב סביבת העבודה שנבדק

- Render service `business-mvp-iqem` פעיל, Free, Frankfurt, ומחובר ל־`business-mvp/main` עם auto-deploy.
- Neon project `eli-cohen-business-mvp` קיים באזור `aws-eu-central-1`; branch `main` כולל כרגע `lead` ו־`setting`.
- היישום הקיים כולל טופס אתר, שמירת לידים, מסך ניהול בסיסי, חיפוש, סטטוסים וייצוא CSV.
- WhatsApp Cloud API אינו ממומש כרגע; קיים רק adapter חסום.
- PR #34 אינו ממוזג. אין למזג אותו לפני פתרון תצורת העסק והפרדת סביבת ה־Wizard מהיישום החי, כי הוא מסיר את `config/business.json` בעוד השירות הפעיל תלוי בתצורת האתר.

## שלבי מימוש

1. שמירת מצב קיים וגיבוי נתונים; הקמת branch בדיקה ב־Neon.
2. מודל CRM additive: metadata, messages, events ו־outbox.
3. WhatsApp Cloud API inbound: verification, signature validation, deduplication ו־phone matching.
4. API מאובטח ל־Make/n8n: outbox, ack, callback ועדכון סיווג.
5. Gemini redaction + classification עם audit trail.
6. שדרוג מסך CRM: מקור, עדיפות, תקציר, timeline, follow-up, פילטרים וייצוא.
7. הרחבת Wizard ל־Preflight, תבניות workflows, Canva/Gemini/automation connections ו־per-customer secrets.
8. בדיקות unit/integration, CI, staging, health, בדיקת ליד end-to-end ואז פריסה מבוקרת.
