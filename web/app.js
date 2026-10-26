/* Leash console — AG Grid attempts, human gates, watchdog alerts, Rex demo. */
"use strict";

const $ = (id) => document.getElementById(id);

const grid = agGrid.createGrid($("attempts-grid"), {
  columnDefs: [
    { headerName: "#", field: "id", width: 64, sort: "desc" },
    { headerName: "Time", field: "ts", width: 190,
      valueFormatter: (p) => p.value?.replace("T", " ").replace("+00:00", " UTC") },
    { headerName: "Agent", field: "agent", width: 100 },
    { headerName: "Payee", field: "payee", flex: 1, minWidth: 210 },
    { headerName: "Amount", field: "amount", width: 100,
      valueFormatter: (p) => Number(p.value).toFixed(2) },
    { headerName: "Purpose", field: "purpose", width: 140 },
    { headerName: "Verdict", field: "decision", width: 150,
      cellRenderer: (p) =>
        `<span class="decision ${p.value}">${p.value}</span>` },
    { headerName: "Reasons", field: "reasons", flex: 2, minWidth: 260,
      valueFormatter: (p) => (p.value || []).join("; ") },
    { headerName: "Receipt", field: "receipt_id", width: 120 },
    { headerName: "PayPal order", field: "order_id", width: 150 },
  ],
  rowData: [],
  defaultColDef: { resizable: true, sortable: true, filter: true },
  pagination: true, paginationPageSize: 15,
});

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "content-type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(JSON.stringify(data));
  return data;
}

async function refresh() {
  const [attempts, holds, alerts, health] = await Promise.all([
    api("GET", "/attempts"), api("GET", "/holds"),
    api("GET", "/alerts"), api("GET", "/healthz"),
  ]);
  grid.updateData({ rows: attempts.map((a) => ({ ...a,
    reasons: a.reasons, order_id: a.order_id || "—" })) });
  $("health").textContent =
    `paypal: ${health.paypal_mode} · llm: ${health.llm_provider}` +
    (health.zapier ? " · zapier: on" : "");
  const allow = attempts.filter((a) => a.decision === "ALLOW").length;
  const deny = attempts.filter((a) => a.decision === "DENY").length;
  const hold = attempts.filter((a) => a.decision === "HOLD_FOR_HUMAN").length;
  $("stats").textContent =
    `${attempts.length} attempts · ${allow} allowed · ${hold} held · ${deny} denied`;
  renderHolds(holds);
  renderAlerts(alerts);
}

function renderHolds(holds) {
  const el = $("holds");
  el.innerHTML = holds.length ? "" :
    '<p class="empty">no pending holds — the gate is calm</p>';
  for (const h of holds) {
    const div = document.createElement("div");
    div.className = "hold-card";
    div.innerHTML = `
      <b>${h.agent} → ${h.payee} · ${Number(h.amount).toFixed(2)}</b>
      <div class="why">${(h.reasons || []).join("; ")}</div>
      <div class="row-actions">
        <button class="approve">Approve (creates PayPal order)</button>
        <button class="deny">Deny</button>
      </div>`;
    const rid = h.receipt_id;
    div.querySelector(".approve").onclick = async () => {
      await api("POST", `/holds/${rid}`, { approve: true });
      await refresh();
    };
    div.querySelector(".deny").onclick = async () => {
      await api("POST", `/holds/${rid}`, { approve: false });
      await refresh();
    };
    el.appendChild(div);
  }
}

function renderAlerts(alerts) {
  const el = $("alerts");
  el.innerHTML = alerts.length ? "" :
    '<p class="empty">no drift detected</p>';
  for (const a of alerts) {
    const div = document.createElement("div");
    div.className = `alert-card sev-${a.severity}`;
    const drafted = a.status === "dispute_drafted";
    div.innerHTML = `
      <b>${a.kind.replace("_", " ")}</b>
      <span class="badge">${a.status}</span>
      <div class="why">${a.summary}</div>
      <div class="row-actions">
        <button class="ghost" ${drafted ? "disabled" : ""}>
          ${drafted ? "Dispute drafted ✓" : "Draft PayPal dispute"}</button>
        <button class="ghost view">View evidence</button>
      </div>`;
    div.querySelector("button:not([disabled])").onclick = async () => {
      const out = await api("POST", `/alerts/${a.id}/dispute`);
      showModal(out.draft.title, out.draft.body +
        `\n\n— drafted by ${out.draft.drafted_by}`);
      await refresh();
    };
    div.querySelector(".view").onclick = () =>
      showModal(`${a.kind} · ${a.id}`, JSON.stringify(a.evidence, null, 2));
    el.appendChild(div);
  }
}

function showModal(title, body) {
  $("modal-title").textContent = title;
  $("modal-body").textContent = body;
  $("modal").classList.remove("hidden");
}

$("run-rex").onclick = async (e) => {
  e.target.disabled = true;
  try {
    const out = await api("POST", "/demo/rex");
    const el = $("rex-report");
    el.innerHTML = "";
    for (const r of out.report) {
      const div = document.createElement("div");
      div.className = `rex-row ${r.caught ? "caught" : "missed"}`;
      div.innerHTML = `
        <span class="verdict">${r.actual}</span> ← <b>${r.attack}</b>
        <div class="why">${r.why}</div>
        <div class="why">receipt ${r.receipt_id} · ${r.reasons[0] || "clean"}</div>`;
      el.appendChild(div);
    }
    await refresh();
  } finally { e.target.disabled = false; }
};

$("seed-scan").onclick = async (e) => {
  e.target.disabled = true;
  try {
    const out = await api("POST", "/demo/drift");
    showModal("Watchdog scan",
      `new alerts: ${out.new_alerts.length}\n\n` +
      out.new_alerts.map((a) => `• ${a.kind}: ${a.summary}`).join("\n"));
    await refresh();
  } finally { e.target.disabled = false; }
};

$("scan").onclick = async () => {
  const out = await api("POST", "/watchdog/scan");
  showModal("Watchdog scan",
    `new alerts: ${out.new_alerts.length} (open: ${out.total_open})\n\n` +
    out.new_alerts.map((a) => `• ${a.kind}: ${a.summary}`).join("\n"));
  await refresh();
};

refresh().catch((e) => { $("health").textContent = "backend offline"; });
setInterval(() => refresh().catch(() => {}), 6000);
