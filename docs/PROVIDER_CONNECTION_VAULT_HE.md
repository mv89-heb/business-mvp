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
