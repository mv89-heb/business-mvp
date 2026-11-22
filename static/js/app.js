document.addEventListener("DOMContentLoaded", () => {
  const button = document.getElementById("estimate-button");
  if (!button) return;
  button.addEventListener("click", async () => {
    const area = document.getElementById("area-estimate").value;
    const output = document.getElementById("estimate-result");
    if (!area || Number(area) <= 0) {
      output.textContent = "נא להזין שטח חיובי במ״ר.";
      return;
    }
    button.disabled = true;
    output.textContent = "בודקים...";
    try {
      const response = await fetch("/estimate", {
        method: "POST",
        headers: {"Content-Type": "application/x-www-form-urlencoded"},
        body: new URLSearchParams({area})
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "לא ניתן לחשב כרגע.");
      output.textContent = data.available
        ? `אומדן ראשוני: ${data.minimum.toLocaleString("he-IL")}–${data.maximum.toLocaleString("he-IL")} ₪. ${data.message}`
        : data.message;
    } catch (error) {
      output.textContent = error.message || "אירעה שגיאה. נסו שוב.";
    } finally {
      button.disabled = false;
    }
  });
});
