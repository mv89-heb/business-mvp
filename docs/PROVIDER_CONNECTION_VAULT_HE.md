# כספות חיבורי ספקים — תכנון ומצב מימוש

## מה נוסף

המודול `business_app/provisioning/connections.py` מספק בסיס מאובטח לשמירת חיבורי GitHub, Neon ו-Render לפי מזהה לקוח:

- AES-256-GCM עם nonce אקראי לכל הצפנה ואימות שלמות.
- ה-customer ID ושם הספק משמשים כ-Associated Data, ולכן לא ניתן להעביר ciphertext בין לקוחות או בין ספקים.
- PostgreSQL שומר ciphertext בלבד ומטא-נתונים לא סודיים.
- מפתח ההצפנה אינו נשמר במסד הנתונים; הוא נטען מ-`CUSTOMER_CREDENTIALS_ENCRYPTION_KEY`.
- מחיקה מתבצעת לפי צמד מדויק של customer_id ו-provider.
- אין API שמחזיר סוד למסך, ל-session או ללוג.

## הפעלת המפתח

יש לייצר מפתח מחוץ ל-Git ולהעבירו ישירות למנגנון Secrets של סביבת ההרצה:

```powershell
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

יש להגדיר את התוצאה כ-`CUSTOMER_CREDENTIALS_ENCRYPTION_KEY`. אין לשלוח אותה בצ'אט, להכניס אותה ל-Git או לשמור אותה ב-Database. אובדן המפתח פירושו שלא ניתן לפענח חיבורים קיימים; יש לגבות אותו במנהל סודות מאובטח.

## תנאי הפעלה

- נדרש PostgreSQL מתמשך דרך `DATABASE_URL`; אין fallback לקובץ מקומי או לאחסון זמני.
- בהיעדר מפתח או מסד נתונים, הפעולה נכשלת סגור.
- אין עדיין מסך UI להזנת מפתחות, אימות מול ספקים, OAuth/GitHub App או חיבור ה-vault ל-Preflight/Provisioning. לכן Customer-owned נשאר חסום עד השלמת האינטגרציה והבדיקות החיות.
- המודול אינו משנה את משתני הסביבה הגלובליים ואינו משתמש בהם כ-fallback.

## החלטת אבטחה

ב-MVP, Neon ו-Render עשויים לדרוש API keys בעלי הרשאות רחבות. לכן יש להעדיף OAuth/delegated access כשספק תומך בכך; אם משתמשים ב-API key, יש להציג למפתח את ההרשאות והסיכון, לאמת את זהות החשבון והיעד, לאפשר revoke, ולהצפין לפני התמדה. GitHub App Installation Token הוא היעד המועדף במקום PAT קבוע.


## חיבור מתוך אשף המפתח

בגרסה זו נוספו נקודות קצה וממשק להזנת חיבורי לקוח לפי slug:

1. GitHub: PAT נבדק מול `GET /user`; נשמר שם החשבון המאומת. בדיקת הרשאת יצירת Repository מתבצעת שוב רק בזמן Provisioning.
2. Neon: API key נבדק מול רשימת הפרויקטים של `NEON_ORG_ID` שהוזן.
3. Render: API key נבדק מול רשימת ה-owners ומאמת שה-Workspace ID המדויק נגיש.
4. כל חיבור נשמר רק לאחר בדיקת הספק ובהצפנת AES-256-GCM. מפתחות לא נשמרים ב-session ולא מוחזרים ל-HTML.
5. ניתן להסיר כל חיבור לפי customer slug וספק. Preflight טוען רק את שלושת החיבורים המאומתים של אותו slug, ומסמן את המתאמים כ-scope-bound. ה-workflow דוחה adapters שאינם משויכים בדיוק ללקוח שבתוכנית.

## גבולות אימות

- בדיקת GitHub מאמתת זהות, לא את כל ה-scopes. הרשאות יצירה, כתיבה לארגון והגבלות SSO עשויות להיכשל רק בפעולת היצירה; במקרה כזה ההקמה נעצרת ללא fallback.
- חיבורי Neon ו-Render מאמתים גישה לארגון/workspace שסופקו, אך אינם מאמתים מראש עלות, quota או יכולת ליצור משאב בתוכנית Free. תנאים אלה עדיין נבדקים לפני/במהלך Preflight ו-Provisioning.
- חיבור שנשמר כמאומת אינו הבטחה שהמפתח עדיין פעיל. כל Preflight מבצע קריאות live מחדש.
- כרגע GitHub account login משמש כ-owner. תמיכה מלאה ב-GitHub App Installation ובחיבור ארגון ייעודי היא שלב המשך.
- מפתח ההצפנה עדיין חייב להיות מוגדר ידנית ב-Render לפני שימוש במסך. לא נוצר ולא הוגדר מפתח בפריסה זו.
