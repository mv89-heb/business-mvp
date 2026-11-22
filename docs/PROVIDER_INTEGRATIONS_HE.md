# אינטגרציות ספקים והקמה אוטומטית

## עקרון תכנון

האשף הוא כלי מפתח מקומי בלבד. מתאמי הספקים נמצאים בתיקיית business_app/provisioning/providers ומשתמשים ב-REST APIs רשמיים. כל לקוח מקבל Repository, פרויקט Neon ושירות Render נפרדים.

Canva מנוהלת דרך חיבור Canva המאומת ב-ChatGPT (Canva MCP) עבור יצירת נכסי מותג ועיצובים. אין לשמור סיסמת Canva או טוקן Canva בקובצי התוכנית. חיבור Canva דרך אפליקציה עצמאית ידרוש OAuth, scopes ואישור של בעל החשבון; אין להניח שכל API כלול בכל תוכנית Canva.

## משתני סביבה לספקים

יש להגדיר רק במחשב המפתח / במנגנון סודות, לעולם לא ב-Git:

- GITHUB_TOKEN — GitHub fine-grained token עם הרשאות יצירת Repository פרטי.
- NEON_API_KEY — Neon API key.
- NEON_ORG_ID — אופציונלי כאשר המפתח משויך לארגון מסוים.
- RENDER_API_KEY — Render API key.
- RENDER_WORKSPACE_ID — מזהה סביבת העבודה שאושרה במפורש.

המתאמים אינם קוראים סודות מקובצי JSON ואינם מחזירים אותם בסטטוס החיבור.

## יכולות המתאמים

- GitHub: בדיקת זהות, בדיקת Repository קיים, יצירת Repository פרטי בלבד. המתאם אינו מעתיק קוד ואינו מבצע push.
- Neon: בדיקת חיבור, איתור פרויקט קיים לפי שם, יצירת פרויקט עם compute מינימלי והחזרת URI לחיבור. ה-URI הוא סוד; אין לכתוב אותו ללוגים או לקובצי מצב.
- Render: בדיקת סביבת עבודה, איתור שירות קיים ויצירת Web Service בתוכנית Free בלבד, בפרנקפורט, עם deploy רק לאחר מעבר בדיקות CI (checksPass).

## בטיחות ותפעול

1. לפני יצירה יש לבצע preflight לכל הספקים, לאמת הרשאות ולבדוק כפילויות.
2. במקרה של timeout לאחר בקשת יצירה, אין לבצע retry אוטומטי. יש לבדוק קודם אם המשאב נוצר.
3. אין לאפשר בחירה של תוכנית Render בתשלום.
4. Neon Free eligibility ומחירים/מכסות משתנים לפי החשבון והתנאים. יצירת פרויקט מחייבת להעביר free_tier_verified=True רק לאחר בדיקה חיה ואישור שהעלות הצפויה אפס. ברירת המחדל היא חסימה; אם לא ניתן לאמת עלות אפס — עוצרים.
5. אין ליצור או לשנות משאבי ענן כחלק מבדיקות אוטומטיות. הבדיקות משתמשות ב-mocks בלבד.
6. יצירת שירות Render דורשת ש-GitHub repository כבר מחובר לחשבון Git Provider ב-Render. אם לא, יש לבצע חיבור ידני בלוח הבקרה לפני יצירת השירות.
7. מתאם GitHub יוצר Repository ריק עם README ראשוני. שכפול קוד נקי, push, בדיקת health ואימות deploy הם שלבי workflow נפרדים שטרם אוטומטו במתאם זה.

## בדיקת חיבור מתוך Python

    from business_app.provisioning.providers import GitHubProvider, NeonProvider, RenderProvider

    # בדיקות חיבור בלבד — ללא יצירת משאבים
    print(GitHubProvider().check_connection())
    print(NeonProvider().check_connection())
    print(RenderProvider().check_connection())

## מגבלת שלב נוכחי

הוספת המתאמים מאפשרת בדיקת חיבור ופעולות יצירה מבוקרות ברמת ספק, אך עדיין אינה workflow מלא שמבצע את כל השלבים ברצף. אין לסמן התקנה כ"הושלמה" עד להוספת שכבת orchestration, publishing של קוד ה-Starter, טיפול בהתאוששות, הזנת סודות ל-Render ובדיקת health של הפריסה.
