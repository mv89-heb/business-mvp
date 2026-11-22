document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector('form[action$="/wizard"]');
  if (!form) return;

  const steps = Array.from(form.querySelectorAll("[data-wizard-step]"));
  const labels = Array.from(document.querySelectorAll("[data-step-label]"));
  const previous = document.querySelector("[data-wizard-prev]");
  const next = document.querySelector("[data-wizard-next]");
  const submit = document.querySelector("[data-wizard-submit]");
  const progress = document.querySelector("[data-progress]");
  const number = document.querySelector("[data-step-number]");
  const title = document.querySelector("[data-step-title]");
  const description = document.querySelector("[data-step-description]");
  const status = document.querySelector("[data-wizard-status]");

  if (!steps.length || !previous || !next || !submit) return;

  const headings = [
    ["פרטי העסק", "מגדירים את זהות הפרויקט והעסק."],
    ["מיתוג ופרטי קשר", "קובעים את הנראות ואת פרטי ההתקשרות."],
    ["בחירת מודולים", "בוחרים רק יכולות שנדרשות לפרויקט."],
    ["תשתיות ותקציב", "בודקים את סביבת ההקמה לפני יצירת התוכנית."]
  ];
  const requestedStep = Number(document.body.dataset.initialStep || 0);
  let current = Number.isInteger(requestedStep)
    ? Math.min(Math.max(requestedStep, 0), steps.length - 1)
    : 0;

  const render = () => {
    steps.forEach((step, index) => {
      step.hidden = index !== current;
      step.setAttribute("aria-hidden", index === current ? "false" : "true");
    });
    labels.forEach((label, index) => {
      label.classList.toggle("active", index === current);
      label.classList.toggle("complete", index < current);
      if (index === current) label.setAttribute("aria-current", "step");
      else label.removeAttribute("aria-current");
    });
    previous.hidden = current === 0;
    next.hidden = current === steps.length - 1;
    submit.hidden = current !== steps.length - 1;
    if (progress) progress.style.width = ((current + 1) / steps.length * 100) + "%";
    if (number) number.textContent = String(current + 1).padStart(2, "0");
    if (title) title.textContent = headings[current][0];
    if (description) description.textContent = headings[current][1];
    if (status) status.textContent = "שלב " + (current + 1) + " מתוך " + steps.length;
  };

  const validateCurrent = () => {
    const fields = Array.from(steps[current].querySelectorAll("input, select, textarea"));
    for (const field of fields) {
      if (!field.checkValidity()) {
        field.reportValidity();
        field.focus();
        return false;
      }
    }
    return true;
  };

  next.addEventListener("click", () => {
    if (!validateCurrent()) return;
    current = Math.min(current + 1, steps.length - 1);
    render();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  previous.addEventListener("click", () => {
    current = Math.max(current - 1, 0);
    render();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  form.addEventListener("submit", event => {
    for (let index = 0; index < steps.length; index += 1) {
      const invalid = Array.from(steps[index].querySelectorAll("input, select, textarea"))
        .find(field => !field.checkValidity());
      if (invalid) {
        event.preventDefault();
        current = index;
        render();
        invalid.reportValidity();
        invalid.focus();
        return;
      }
    }
  });

  render();
});