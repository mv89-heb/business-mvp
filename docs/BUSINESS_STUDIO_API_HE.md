# Business Studio — API וזרימת אישור

## מודל עבודה

- ChatGPT/המפתח שולח פרטי עסק ל־API הפנימי של Business Studio.
- המערכת בודקת את תבניות המקור והעסקים הקיימים ב־Neon.
- הצעה נשמרת בסטטוס `PENDING_APPROVAL`; היא אינה עסק פעיל ואינה מפעילה אוטומציה.
- אישור אנושי בממשק `/businesses` יוצר רשומת עסק פעילה ומכניס אירוע Outbox עמיד מסוג `business.approved`.
- Make.com מקבל Webhook רק לאחר האישור. אם פרטי החיבור חסרים, האירוע נשאר בתור ולא נזרק.

## API

| פעולה | HTTP | נתיב |
|---|---|---|
| getBusinesses | GET | `/api/businesses` או `/api/getBusinesses` |
| createBusinessProposal | POST | `/api/business-proposals` או `/api/createBusinessProposal` |
| אישור הצעה | POST | `/api/business-proposals/{proposal_id}/approve` |
| דחיית הצעה | POST | `/api/business-proposals/{proposal_id}/reject` |
| ניסיון שליחה חוזר ל־Make | POST | `/api/business-proposals/{proposal_id}/dispatch` |
| createTask | POST | `/api/tasks` או `/api/createTask` |
| רשימת הצעות ומשימות | GET | `/api/business-proposals`, `/api/tasks` |

כל הנתיבים דורשים session של מפתח. פעולות POST מוגנות גם ב־CSRF. קריאות דפדפן שולחות את הטוקן בכותרת `X-CSRFToken`.

## מבנה הצעת עסק

```json
{
  "name": "דני מיזוג אוויר",
  "slug": "dani-ac",
  "industry": "מיזוג אוויר",
  "sourceBusinessId": "base-template",
  "branding": {
    "primaryColor": "#173B35",
    "secondaryColor": "#D5A66F",
    "heroImageUrl": null,
    "logoUrl": null,
    "fontFamily": "Heebo"
  },
  "siteContent": {
    "heroTitle": "דני מיזוג אוויר – שירות מקצועי",
    "heroSubtitle": "פתרונות מיזוג בהתאמה לצורך שלכם",
    "services": []
  },
  "automationConfig": {
    "provider": "make.com",
    "botId": null,
    "whatsappNumber": null,
    "businessKnowledge": "מידע עסקי...",
    "leadQualificationQuestions": [
      {"key": "service", "question": "איזה שירות או איזו תקלה תרצו שנבדוק?"},
      {"key": "city", "question": "באיזו עיר נמצא הנכס?"},
      {"key": "urgency", "question": "מה רמת הדחיפות ומתי נוח לחזור אליכם?"}
    ],
    "timezone": "Asia/Jerusalem",
    "timeFormat": "24h",
    "locale": "he-IL",
    "direction": "rtl"
  }
}
```

## Neon

הטבלאות נוצרות באופן idempotent:
- `business_catalog` — עסקים ותבניות לשכפול.
- `business_proposals` — הצעות ואישורים.
- `business_tasks` — משימות משלימות.
- `business_automation_outbox` — אירועי Make עמידים עם Idempotency.

`base-template` נוצר אוטומטית רק אם אינו קיים. עסק שאושר נשמר גם כתבנית אפשרית להקמות הבאות. אין שינוי בטבלאות ה־Provisioning הקיימות.

## Make.com

הגדר ב־Render Environment Variables:
- `MAKE_WEBHOOK_URL` — כתובת Custom Webhook של תרחיש Make.
- `MAKE_WEBHOOK_SECRET` — סוד אקראי חזק משותף לאימות חתימה.

הבקשה נשלחת עם `X-Business-Studio-Signature: sha256=...` (HMAC-SHA256) ו־`Idempotency-Key`. יש לאמת חתימה ב־Make ולשמור את מזהה האירוע כדי למנוע עיבוד כפול. אין להפעיל את התרחיש לפני שהוגדרו ואומתו שני הערכים.

## אימות ובטיחות

- אישור הוא פעולה מפורשת בלבד; אין auto-activation ביצירת הצעה.
- מספר WhatsApp אינו מועתק מתבנית מקור כדי למנוע שליחת לידים למספר של עסק אחר.
- צבעים, גופן, שירותים ושאלות סינון יכולים לרשת את תבנית המקור.
- שדות Canva ופרטי WhatsApp חסרים יוצרים משימות מעקב.
- אין סודות בתוך Payload, Audit או מסכי הממשק.
