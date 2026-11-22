/**
 * Free Google Sheets receiver for Eli Cohen Construction & Wood.
 *
 * Configure Script Properties before deploying:
 *   SPREADSHEET_ID = the ID between /d/ and /edit in the Google Sheet URL
 *   WEBHOOK_SECRET = the same random secret configured as LEAD_WEBHOOK_SECRET in Render
 *
 * Deploy as a Web app, execute as yourself, access: Anyone.
 * The endpoint is protected by the shared secret in the JSON request body.
 */

function doPost(e) {
  const lock = LockService.getScriptLock();
  try {
    const body = JSON.parse((e && e.postData && e.postData.contents) || "{}");
    const props = PropertiesService.getScriptProperties();
    const expectedSecret = props.getProperty("WEBHOOK_SECRET") || "";
    if (!expectedSecret || !body.token || !constantTimeEquals_(String(body.token), expectedSecret)) {
      return json_({ ok: false, error: "unauthorized" });
    }
    if (body.event !== "lead.created" || !body.lead || !body.lead.id) {
      return json_({ ok: false, error: "invalid_event" });
    }

    const spreadsheetId = props.getProperty("SPREADSHEET_ID");
    if (!spreadsheetId) throw new Error("Missing SPREADSHEET_ID script property");

    lock.waitLock(5000);
    const spreadsheet = SpreadsheetApp.openById(spreadsheetId);
    const sheet = spreadsheet.getSheetByName("פניות") || spreadsheet.insertSheet("פניות");
    const headers = [
      "מספר פנייה", "תאריך", "שם לקוח", "טלפון", "דוא״ל",
      "יישוב", "סוג עבודה", "שטח במ״ר", "תקציב", "פרטי הפרויקט", "סטטוס"
    ];
    if (sheet.getLastRow() === 0) {
      sheet.appendRow(headers);
      sheet.setFrozenRows(1);
    }

    // Avoid duplicate rows if the sender retries after a network timeout.
    const leadId = String(body.lead.id);
    if (sheet.getLastRow() > 1) {
      const existing = sheet.getRange(2, 1, sheet.getLastRow() - 1, 1)
        .createTextFinder(leadId).matchEntireCell(true).findNext();
      if (existing) return json_({ ok: true, duplicate: true, lead_id: leadId });
    }

    const lead = body.lead;
    sheet.appendRow([
      safeCell_(leadId),
      safeCell_(lead.created_at || new Date().toISOString()),
      safeCell_(lead.name),
      safeCell_(lead.phone),
      safeCell_(lead.email),
      safeCell_(lead.city),
      safeCell_(lead.service),
      lead.area_sqm == null ? "" : Number(lead.area_sqm),
      safeCell_(lead.budget),
      safeCell_(lead.details),
      safeCell_(lead.status)
    ]);
    return json_({ ok: true, lead_id: leadId });
  } catch (err) {
    console.error("Lead webhook failed: " + err);
    return json_({ ok: false, error: "server_error" });
  } finally {
    try { lock.releaseLock(); } catch (ignored) {}
  }
}

function safeCell_(value) {
  const text = value == null ? "" : String(value);
  // Prevent user-supplied values from being interpreted as spreadsheet formulas.
  return /^[=+@-]/.test(text) ? "'" + text : text;
}

function constantTimeEquals_(a, b) {
  if (a.length !== b.length) return false;
  let mismatch = 0;
  for (let i = 0; i < a.length; i++) mismatch |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return mismatch === 0;
}

function json_(value) {
  return ContentService.createTextOutput(JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
}
