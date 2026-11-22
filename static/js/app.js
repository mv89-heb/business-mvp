document.addEventListener("DOMContentLoaded", () => {
  const estimateButton = document.getElementById("estimate-button");
  if (estimateButton) {
    estimateButton.addEventListener("click", async () => {
      const area = document.getElementById("area-estimate").value;
      const output = document.getElementById("estimate-result");
      if (!area || Number(area) <= 0) {
        output.textContent = "נא להזין שטח חיובי במ״ר.";
        return;
      }
      estimateButton.disabled = true;
      output.textContent = "בודקים...";
      try {
        const response = await fetch("/estimate", {
          method: "POST",
          headers: {"Content-Type": "application/x-www-form-urlencoded"},
          body: new URLSearchParams({area})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "לא ניתן לבדוק כרגע.");
        output.textContent = data.available
          ? "אומדן ראשוני: " + data.minimum.toLocaleString("he-IL") + "–" + data.maximum.toLocaleString("he-IL") + " ₪. " + data.message
          : data.message;
      } catch (error) {
        output.textContent = error.message || "אירעה שגיאה. נסו שוב.";
      } finally {
        estimateButton.disabled = false;
      }
    });
  }

  const serviceSelect = document.getElementById("service-select");
  const details = document.getElementById("project-details");
  if (serviceSelect && details) {
    const prompts = {
      "דק": "איפה מתוכנן הדק? מה המידות המשוערות, ומה סוג העץ או הגימור המועדף?",
      "פרגולה": "איפה תוקם הפרגולה? מה המידות, והאם יש העדפה לסוג עץ או לסגנון מסוים?",
      "חיפוי עץ": "איזה משטח מיועד לחיפוי, מה המידות, והאם יש דוגמה לסגנון הרצוי?",
      "תיקונים וחידוש": "מה צריך לתקן או לחדש, מה מצב העבודה הקיימת, והיכן היא נמצאת?",
      "עבודת עץ אחרת": "תארו בקצרה את העבודה, המידות והמיקום."
    };
    const updatePrompt = () => {
      details.placeholder = prompts[serviceSelect.value] || "ספרו לנו על הפרויקט...";
    };
    serviceSelect.addEventListener("change", updatePrompt);
    updatePrompt();
    document.querySelectorAll("[data-service]").forEach(link => {
      link.addEventListener("click", () => {
        serviceSelect.value = link.dataset.service;
        updatePrompt();
      });
    });
  }
});
