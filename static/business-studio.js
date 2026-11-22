(() => {
  "use strict";
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const $ = (id) => document.getElementById(id);
  const escapeText = (value) => String(value ?? "");
  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: {"Content-Type": "application/json", "X-CSRFToken": csrf, ...(options.headers || {})}
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "הפעולה נכשלה.");
    return data;
  }
  function cell(row, value) {
    const td = document.createElement("td");
    td.textContent = escapeText(value);
    row.appendChild(td);
    return td;
  }
  function status(text, active = false) {
    const span = document.createElement("span");
    span.className = "status" + (active ? " active" : "");
    span.textContent = text;
    return span;
  }
  async function approve(id, button) {
    if (!window.confirm("לאשר את ההצעה? האישור ייצור רשומת עסק וינסה להעביר אירוע לתור Make.")) return;
    button.disabled = true;
    try {
      const result = await api("/api/business-proposals/" + encodeURIComponent(id) + "/approve", {method:"POST", body:"{}"});
      const webhook = result.result?.webhook?.status || "QUEUED";
      window.alert(webhook === "SENT" ? "העסק אושר והודעת Make נשלחה." : "העסק אושר. אירוע Make נשאר בתור עד להגדרת Webhook וסוד חתימה.");
      await load();
    } catch (error) { window.alert(error.message); button.disabled = false; }
  }
  async function dispatch(id, button) {
    button.disabled = true;
    try {
      const result = await api("/api/business-proposals/" + encodeURIComponent(id) + "/dispatch", {method:"POST", body:"{}"});
      window.alert(result.result?.status === "SENT" ? "אירוע Make נשלח בהצלחה." : "האירוע נשאר בתור: " + (result.result?.status || "לא נשלח"));
      await load();
    } catch (error) { window.alert(error.message); button.disabled = false; }
  }
  async function reject(id, button) {
    if (!window.confirm("לדחות את הצעת העסק?")) return;
    button.disabled = true;
    try { await api("/api/business-proposals/" + encodeURIComponent(id) + "/reject", {method:"POST", body:"{}"}); await load(); }
    catch (error) { window.alert(error.message); button.disabled = false; }
  }
  async function load() {
    $("page-error").textContent = "";
    try {
      const [businesses, proposals, tasks] = await Promise.all([
        api("/api/businesses"), api("/api/business-proposals"), api("/api/tasks")
      ]);
      $("business-count").textContent = businesses.businesses.length;
      $("pending-count").textContent = proposals.proposals.filter(p => p.status === "PENDING_APPROVAL").length;
      $("task-count").textContent = tasks.tasks.filter(t => t.status === "OPEN").length;
      const pbody = $("proposals-body"); pbody.replaceChildren();
      const pending = proposals.proposals.filter(p => p.status === "PENDING_APPROVAL");
      const approved = proposals.proposals.filter(p => p.status === "APPROVED");
      if (!pending.length) { const tr=document.createElement("tr"); const td=document.createElement("td"); td.colSpan=6; td.className="empty"; td.textContent="אין הצעות שממתינות לאישור."; tr.append(td); pbody.append(tr); }
      pending.forEach(p => {
        const tr=document.createElement("tr"); cell(tr,p.name); cell(tr,p.industry); cell(tr,p.sourceBusinessId); cell(tr,p.createdAt ? new Date(p.createdAt).toLocaleString("he-IL") : "—"); const st=cell(tr,""); st.append(status("ממתין לאישור"));
        const actions=cell(tr,""); actions.className="row-actions";
        const yes=document.createElement("button"); yes.className="primary"; yes.textContent="אישור"; yes.addEventListener("click",()=>approve(p.id,yes));
        const no=document.createElement("button"); no.className="danger"; no.textContent="דחייה"; no.addEventListener("click",()=>reject(p.id,no)); actions.append(yes,no); pbody.append(tr);
      });
      const abody=$("approved-body"); abody.replaceChildren();
      if (!approved.length) { const tr=document.createElement("tr"); const td=document.createElement("td"); td.colSpan=5; td.className="empty"; td.textContent="עדיין אין עסקים שאושרו."; tr.append(td); abody.append(tr); }
      approved.forEach(p=>{const tr=document.createElement("tr");cell(tr,p.name);cell(tr,p.slug);const bs=cell(tr,"");bs.append(status("פעיל",true));const ws=cell(tr,"");ws.append(status(p.webhookStatus==="SENT"?"נשלח":p.webhookStatus==="RETRY"?"ניסיון חוזר":"ממתין להגדרה"));const action=cell(tr,"");if(p.webhookStatus!=="SENT"){const retry=document.createElement("button");retry.className="secondary";retry.textContent="נסה שליחה";retry.addEventListener("click",()=>dispatch(p.id,retry));action.append(retry);}abody.append(tr);});
      const bbody=$("businesses-body"); bbody.replaceChildren();
      businesses.businesses.forEach(b=>{const tr=document.createElement("tr");cell(tr,b.name);cell(tr,b.slug);cell(tr,b.industry);cell(tr,b.isTemplate?"תבנית":"עסק");const st=cell(tr,"");st.append(status(b.status,b.status==="ACTIVE"));bbody.append(tr);});
      const tbody=$("tasks-body"); tbody.replaceChildren();
      if (!tasks.tasks.length) { const empty=document.createElement("div"); empty.className="empty"; empty.textContent="אין משימות פתוחות."; tbody.append(empty); }
      tasks.tasks.forEach(t=>{const item=document.createElement("div");item.className="task";const title=document.createElement("strong");title.textContent=t.title;const desc=document.createElement("span");desc.className="hint";desc.textContent=t.description;const state=status(t.status==="OPEN"?"פתוחה":t.status);const left=document.createElement("div");left.append(title,document.createElement("br"),desc);item.append(left,state);tbody.append(item);});
    } catch(error) { $("page-error").textContent=error.message; }
  }
  document.addEventListener("DOMContentLoaded", load);
})();
