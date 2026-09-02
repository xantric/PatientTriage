"use strict";

const state = {
  surge: 1,
  selected: null,
  board: null,
  clinicianActions: {},
  lastIntakeText: null,
  intakeChips: { history: [], medications: [], allergies: [] },
  intakeTempUnit: "C",
  intakeHistoryOpen: false,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const NIL = '<span class="nil">&middot;</span>';

const ICONS = {
  dept: '<svg viewBox="0 0 24 24" fill="none"><path d="M3 21h18M5 21V7l7-4 7 4v14M9 21v-5h6v5M9.5 10h.01M14.5 10h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none"><path d="M12 3 2.5 20h19L12 3Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M12 10v4M12 17h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  eye: '<svg viewBox="0 0 24 24" fill="none"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><circle cx="12" cy="12" r="2.6" stroke="currentColor" stroke-width="1.8"/></svg>',
  up: '<svg viewBox="0 0 24 24" fill="none"><path d="M12 20V5M6 11l6-6 6 6" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="8.5" stroke="currentColor" stroke-width="1.8"/><path d="M12 7.5V12l3 2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none"><path d="M4 20h4L18.5 9.5a2 2 0 0 0-2.8-2.8L5 17.2V20Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M14 8l2.5 2.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function sexLabel(sex) {
  const v = String(sex || "").toUpperCase();
  if (v === "M") return "M";
  if (v === "F") return "F";
  if (v === "O" || v === "OTHER" || v === "OTHERS") return "Others";
  return v || "—";
}

/** decision_source from API: agent | deterministic_fallback */
function sourceTag(decisionSource) {
  if (decisionSource === "agent") {
    return '<span class="tag source-gemini">Gemini</span>';
  }
  return '<span class="tag source-engine">Deterministic engine</span>';
}

function isAwaitingClinician(row, agent) {
  if (resolveClinicianAction(row)) return false;
  if (row && row.agent_status === "AWAITING_HUMAN") return true;
  if (agent && agent.status === "AWAITING_HUMAN") return true;
  if (agent && agent.status_label === "Awaiting clinician") return true;
  return false;
}

function workflowStatusBanner(row, agent, audit) {
  const action = resolveClinicianAction(row, audit);
  if (action === "accept") {
    return `
      <div class="workflow-status accepted" role="status">
        <span class="workflow-icon" aria-hidden="true">✓</span>
        <span class="workflow-copy">
          <strong>Clinician accepted</strong>
          <span>ESI ${row.acuity} confirmed</span>
        </span>
      </div>`;
  }
  if (isAwaitingClinician(row, agent)) {
    return `
      <div class="workflow-status awaiting subtle" role="status">
        <span class="workflow-dot" aria-hidden="true"></span>
        <span class="workflow-copy">
          <strong>Awaiting clinician</strong>
          <span>Accept, modify, or request more information below</span>
        </span>
      </div>`;
  }
  return "";
}

function workflowBoardTag(row) {
  const action = resolveClinicianAction(row);
  if (action === "accept") {
    return '<span class="tag workflow accepted"><span class="tag-dot" aria-hidden="true">✓</span>accepted</span>';
  }
  if (isAwaitingClinician(row)) {
    return '<span class="tag workflow awaiting"><span class="tag-dot pulse" aria-hidden="true"></span>awaiting</span>';
  }
  return "";
}

function renderDecisionBanner(agent, row, adj, audit) {
  const pathway = (agent && agent.care_pathway) || adj.placement || "";
  if (!pathway) return "";

  const confPct = row.confidence != null ? `${Math.round(row.confidence * 100)}%` : null;
  const summary = (agent && agent.reason_summary) || "";

  const metaParts = [];
  if (confPct) metaParts.push(`${confPct} confidence`);

  const metaLine = [
    sourceTag(row.decision_source),
    ...metaParts.map((part) => `<span>${esc(part)}</span>`),
  ].join('<span class="decision-meta-sep">·</span>');

  return `
    <div class="decision-block">
      <div class="decision-meta-line">${metaLine}</div>
      <p class="decision-pathway">${esc(pathway)}</p>
      ${summary && summary !== pathway ? `<p class="decision-summary">${esc(summary)}</p>` : ""}
    </div>`;
}

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch (_) { /* keep statusText */ }
    throw new Error(detail);
  }
  return res.json();
}

const esiChip = (level, big) =>
  `<span class="esi lvl-${level}${big ? " big" : ""}">${level}</span>`;

const esiPill = (level) =>
  `<span class="esi-pill lvl-${level}">ESI ${level}</span>`;

const confMeter = (value, band) => `
  <div class="conf">
    <div class="conf-bar"><span class="${band}" style="width:${Math.round(value * 100)}%"></span></div>
    <span class="conf-val">${value.toFixed(2)}</span>
  </div>`;

/* ---------- board ---------- */

function renderKpis(summary) {
  const immediate = (summary.by_acuity[1] || 0) + (summary.by_acuity[2] || 0);
  const cards = [
    { label: "In department", value: summary.total, sub: `load x${state.surge}`, icon: "dept" },
    { label: "Immediate", value: immediate, sub: "ESI 1 and 2", cls: immediate ? "danger" : "", icon: "alert" },
    { label: "Nurse review", value: summary.routed_to_nurse, sub: "pending", cls: summary.routed_to_nurse ? "warn" : "", icon: "eye" },
    { label: "Escalated", value: summary.escalated_for_uncertainty, sub: "on uncertainty", icon: "up" },
    { label: "Time-critical", value: summary.active_clocks, sub: "clocks running", icon: "clock" },
    { label: "Overrides", value: summary.overrides, sub: "logged", cls: summary.overrides ? "brand" : "", icon: "edit" },
  ];
  $("#kpis").innerHTML = cards.map((c) => `
    <div class="kpi">
      <div class="kpi-top">
        <div class="label">${esc(c.label)}</div>
        <span class="kpi-icon">${ICONS[c.icon] || ""}</span>
      </div>
      <div class="value ${c.cls || ""}">${c.value}</div>
      <div class="sub">${esc(c.sub)}</div>
    </div>`).join("");
}

function rememberClinicianAction(patientId, action) {
  if (patientId && action) state.clinicianActions[patientId] = action;
}

function latestAuditAction(audit) {
  if (!audit || !audit.length) return null;
  const hitl = audit.slice().reverse().find((r) => r.action && r.action.startsWith("hitl_"));
  if (!hitl) return null;
  return hitl.clinician_action || hitl.action.replace(/^hitl_/, "");
}

function resolveClinicianAction(row, audit) {
  const pid = row && row.patient_id;
  if (row && row.clinician_action) return row.clinician_action;
  if (pid && state.clinicianActions[pid]) return state.clinicianActions[pid];
  const fromAudit = latestAuditAction(audit);
  if (fromAudit) return fromAudit;
  return null;
}

function applyClinicianActionToRow(row) {
  if (!row) return row;
  const action = resolveClinicianAction(row);
  if (!action) return row;
  row.clinician_action = action;
  if (action === "accept") {
    row.overridden = false;
    row.override_direction = null;
  }
  rememberClinicianAction(row.patient_id, action);
  return row;
}

function applyClinicianActionsToBoard(board) {
  if (!board || !board.rows) return board;
  board.rows.forEach(applyClinicianActionToRow);
  return board;
}

function hitlLabel(action) {
  const labels = {
    accept: "Accepted",
    modify: "Modified",
    override: "Overridden",
    escalate: "Escalated",
    request_more_information: "Needs info",
  };
  return labels[action] || action.replace(/_/g, " ");
}

function hitlFlag(row) {
  const workflow = workflowBoardTag(row);
  if (workflow) return workflow;

  const action = resolveClinicianAction(row);
  if (action === "modify") {
    const arrow = row.override_direction === "escalate" ? " ↑" : row.override_direction === "de-escalate" ? " ↓" : "";
    return `<span class="tag modified">modified${arrow}</span>`;
  }
  if (action === "override") {
    const arrow = row.override_direction === "escalate" ? " ↑" : row.override_direction === "de-escalate" ? " ↓" : "";
    return `<span class="tag override">override${arrow}</span>`;
  }
  if (row.overridden) {
    const arrow = row.override_direction === "escalate" ? " ↑" : row.override_direction === "de-escalate" ? " ↓" : "";
    return `<span class="tag override">override${arrow}</span>`;
  }
  if (action === "escalate") return '<span class="tag up">escalated</span>';
  if (action === "request_more_information") return '<span class="tag pending">needs info</span>';
  return "";
}

function renderBoard(board) {
  state.board = board;
  renderKpis(board.summary);

  $("#board-body").innerHTML = board.rows.map((r) => {
    const flags = [];
    if (r.escalated_for_uncertainty) flags.push('<span class="tag up">escalated</span>');
    if (r.red_flag_count) flags.push(`<span class="tag flag">${r.red_flag_count} red</span>`);
    const hitl = hitlFlag(r);
    if (hitl) flags.push(hitl);
    if (r.agent_status === "GATHERING_INFORMATION") {
      flags.push('<span class="tag pending">needs info</span>');
    }
    flags.push(sourceTag(r.decision_source));
    const clocks = r.clocks.length
      ? r.clocks.map((c) => `<span class="tag clock${c.includes("missed") ? " missed" : ""}">${esc(c)}</span>`).join(" ")
      : NIL;

    const rowState = resolveClinicianAction(r) === "accept"
      ? "is-accepted"
      : isAwaitingClinician(r)
        ? "is-awaiting"
        : r.agent_status === "GATHERING_INFORMATION"
          ? "is-pending"
          : "";

    return `
      <tr data-id="${esc(r.patient_id)}" class="${state.selected === r.patient_id ? "is-selected" : ""} ${rowState}">
        <td>${esiChip(r.acuity)}</td>
        <td>
          <div class="pname">${esc(r.display_name || "Walk-in")}</div>
          <div class="pid">${esc(r.patient_id)} &middot; ${esc(sexLabel(r.sex))}</div>
        </td>
        <td>${r.age_years}<div class="pid">${esc(r.age_band)}</div></td>
        <td class="complaint">${esc(r.chief_complaint)}</td>
        <td>${confMeter(r.confidence, r.confidence_band)}</td>
        <td><div class="chips">${flags.join(" ") || NIL}</div></td>
        <td><div class="chips">${clocks}</div></td>
      </tr>`;
  }).join("");

  $$("#board-body tr").forEach((tr) => {
    tr.addEventListener("click", () => selectPatient(tr.dataset.id));
  });

  $("#board-note").textContent = `${board.rows.length} patients, most urgent first.`;
}

async function hydrateClinicianActionsFromAudit() {
  try {
    const audit = await api("/api/audit");
    audit.forEach((rec) => {
      if (rec.action && rec.action.startsWith("hitl_")) {
        rememberClinicianAction(
          rec.patient_id,
          rec.clinician_action || rec.action.replace(/^hitl_/, ""),
        );
      }
    });
  } catch (_) { /* audit is optional */ }
}

async function loadBoard() {
  const board = await api("/api/board");
  await hydrateClinicianActionsFromAudit();
  if (board.surge_factor) {
    state.surge = board.surge_factor;
    $$(".topbar-actions .switch button").forEach((b) => {
      b.classList.toggle("is-active", Number(b.dataset.surge) === board.surge_factor);
    });
  }
  renderBoard(applyClinicianActionsToBoard(board));
  // Keep the inspector populated: open the most urgent patient if nothing is picked.
  if (!state.selected && state.board && state.board.rows.length) {
    selectPatient(state.board.rows[0].patient_id);
  }
}

/* ---------- patient detail ---------- */

function renderClocks(clocks) {
  if (!clocks.length) return "";
  const body = clocks.map((c) => {
    const elapsed = c.minutes_elapsed;
    const pct = elapsed == null ? 0 : Math.min(100, Math.round((elapsed / c.window_minutes) * 100));
    const left = c.minutes_remaining == null
      ? "onset unknown"
      : c.minutes_remaining > 0 ? `${c.minutes_remaining} min left` : "window missed";
    return `
      <div class="clock">
        <div class="clock-top">
          <span class="clock-name">${esc(c.name)}</span>
          <span class="clock-left">${esc(left)}</span>
        </div>
        <div class="clock-track"><span style="width:${pct}%"></span></div>
        <div class="clock-note">${esc(c.note)}</div>
      </div>`;
  }).join("");
  return `<div class="section"><h3>Time-critical clocks</h3>${body}</div>`;
}

function renderVitals(flags) {
  if (!flags.length) return "";
  const rows = flags.map((f) => `
    <tr>
      <td class="v-name">${esc(f.vital.replace(/_/g, " "))}</td>
      <td class="v-val">${f.value ?? NIL}</td>
      <td class="status-${esc(f.status)}">${esc(f.status)}</td>
      <td class="v-note">${esc(f.note || "")}</td>
    </tr>`).join("");
  return `<div class="section"><h3>Vitals read against age band</h3><table class="vitals">${rows}</table></div>`;
}

function renderHistory(patient) {
  const prior = !!patient.has_prior_record;
  const history = patient.history || [];
  const meds = patient.medications || [];
  const allergies = patient.allergies || [];
  const zeroHistory = !prior && !history.length && !meds.length && !allergies.length;

  const block = (label, items) => {
    const body = items.length
      ? `<ul class="past-list">${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`
      : `<p class="past-empty">None on file</p>`;
    return `
      <div class="past-block">
        <div class="past-label">${esc(label)}</div>
        ${body}
      </div>`;
  };

  return `
    <div class="section past-history">
      <div class="past-history-head">
        <h3>Past history</h3>
        <span class="tag ${prior ? "nurse" : "flag"}">${prior ? "prior record on file" : "no prior record"}</span>
      </div>
      ${zeroHistory
        ? `<p class="past-zero">First-time presentation. No history, medications, or allergies on file. Triage relies on what is observed now.</p>`
        : `<div class="past-history-stack">
            ${block("Medical history", history)}
            ${block("Medications", meds)}
            ${block("Allergies", allergies)}
          </div>`}
    </div>`;
}

function renderOverrideForm(row) {
  /* kept for compatibility; HITL panel is primary */
  return "";
}

function renderHitl(agent, row, audit) {
  if (!agent) return "";
  const clinicianAction = resolveClinicianAction(row, audit);
  const options = [1, 2, 3, 4, 5].map((l) =>
    `<option value="${l}" ${l === row.acuity ? "selected" : ""}>P${l}</option>`
  ).join("");

  if (agent.status === "GATHERING_INFORMATION") {
    return `
      <div class="section agent-hitl pending-info">
        <h3>Requested Information Pending</h3>
        <p class="muted">The clinician requested more information before finalizing triage.</p>
        <div class="field">
          <label for="update-note">Provide information</label>
          <textarea id="update-note" rows="3" placeholder="e.g. ECG normal, BP rechecked at 120/80..."></textarea>
        </div>
        <button type="button" class="primary" id="btn-update-patient">Submit Information</button>
        <p class="form-msg" id="update-msg"></p>
      </div>`;
  }

  const isCompleted = agent.status === "COMPLETED";
  const decisionStatus = clinicianAction === "modify"
    ? `<div class="workflow-status modified" role="status"><span class="workflow-copy"><strong>Modified</strong><span>Clinician set P${row.acuity}</span></span></div>`
    : clinicianAction === "override"
      ? `<div class="workflow-status override" role="status"><span class="workflow-copy"><strong>Overridden</strong><span>Clinician set P${row.acuity}</span></span></div>`
      : "";
  const buttons = isCompleted
    ? `<button type="button" class="ghost" data-hitl="modify">Modify</button>`
    : `
        <button type="button" class="primary" data-hitl="accept">Accept</button>
        <button type="button" class="ghost" data-hitl="modify">Modify</button>
        <button type="button" class="ghost" data-hitl="request_more_information">Request more information</button>
      `;

  return `
    <div class="section agent-hitl">
      <h3>Clinician decision</h3>
      ${decisionStatus}
      <p class="muted">Final authority stays with the clinician. Modify needs a reason.</p>
      <div class="hitl-actions" id="hitl-actions">
        ${buttons}
      </div>
      <div class="hitl-form" id="hitl-form">
        <div class="field-row">
          <div class="field">
            <label for="hitl-priority">Priority (modify)</label>
            <select id="hitl-priority">${options}</select>
          </div>
          <div class="field">
            <label for="hitl-actor">Clinician</label>
            <input type="text" id="hitl-actor" placeholder="Your Name" />
          </div>
        </div>
        <div class="field">
          <label for="hitl-reason">Reason</label>
          <textarea id="hitl-reason" placeholder="Required for modify"></textarea>
        </div>
        <p class="form-msg" id="hitl-msg"></p>
      </div>
    </div>`;
}

function renderAgentPanel(agent, row) {
  if (!agent) return "";
  const evidence = (agent.key_evidence || []).length
    ? `<ul class="gap-list">${agent.key_evidence.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>`
    : `<p class="muted">No key evidence listed yet.</p>`;
  const gaps = (agent.information_gaps || []).length
    ? `<ul class="gap-list">${agent.information_gaps.map((g) => `<li>${esc(g)}</li>`).join("")}</ul>`
    : "";

  return `
    <div class="section agent-panel">
      <h3>Recommendation details</h3>
      <p class="agent-monitoring"><span class="label">Monitoring</span> ${esc(agent.monitoring_plan || "—")}</p>

      <h4>Key evidence</h4>
      ${evidence}
      ${gaps ? `<h4>Information gaps</h4>${gaps}` : ""}
    </div>`;
}

function renderAuditFor(records) {
  if (!records.length) return "";
  const items = records.slice(-8).reverse().map((r) => `
    <li>
      <span class="t">#${r.record_id}</span>
      <span>${esc(r.action)}
        <span class="pid">${esc(r.actor)}${r.final_priority != null ? `, final P${r.final_priority}` : ""}</span>
      </span>
    </li>`).join("");
  return `<div class="section"><h3>Recent audit</h3><ul class="events">${items}</ul></div>`;
}

function renderDetail(detail) {
  const { row, result, audit, agent } = detail;
  const { interpreter: interp, adjudicator: adj, patient } = result;
  const lvl = Number.isFinite(row.acuity) ? Math.min(5, Math.max(1, row.acuity)) : 3;

  $("#detail").innerHTML = `
    <div class="detail-scroll">
    <div class="detail-head lvl-${lvl}">
      <div class="dh-main">
        <div class="dh-title-row">
          <h2>${esc(row.display_name || "Walk-in")}</h2>
          ${esiPill(row.acuity)}
        </div>
        <div class="sub">${esc(row.patient_id)} &middot; ${esc(sexLabel(row.sex))} &middot; ${row.age_years}y ${esc(row.age_band)} &middot; arrived t+${row.arrival_epoch_min}m</div>
        ${workflowStatusBanner(row, agent, audit)}
        ${renderDecisionBanner(agent, row, adj, audit)}
      </div>
    </div>

    <div class="section">
      <h3>Presenting complaint</h3>
      <p>${esc(patient.chief_complaint)}</p>
    </div>

    ${renderHistory(patient)}

    ${renderAgentPanel(agent, row)}
    ${renderHitl(agent, row, audit)}

    <div class="section">
      <h3>Plain-language read</h3>
      <div id="explain-slot"><p class="muted">Loading...</p></div>
    </div>

    ${renderClocks(adj.time_critical_clocks)}
    ${renderVitals(interp.vital_flags)}
    ${renderAuditFor(audit)}
    </div>
  `;

  $$("#hitl-actions button").forEach((btn) => {
    btn.addEventListener("click", () => submitHitl(row.patient_id, btn.dataset.hitl));
  });

  const btnUpdate = $("#btn-update-patient");
  if (btnUpdate) {
    btnUpdate.addEventListener("click", () => submitPatientUpdate(row.patient_id));
  }
}

async function submitPatientUpdate(pid) {
  const note = ($("#update-note") && $("#update-note").value.trim()) || "";
  const msg = $("#update-msg");
  if (note.length < 3) {
    if (msg) {
      msg.className = "form-msg err";
      msg.textContent = "Please provide some information to submit.";
    }
    return;
  }
  
  const btn = $("#btn-update-patient");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Analysing with Gemini...";
  }

  try {
    const body = await api(`/api/patients/${encodeURIComponent(pid)}/update`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note, confirm: false }),
    });
    renderUpdatePreview(pid, note, body);
  } catch (err) {
    if (msg) {
      msg.className = "form-msg err";
      msg.textContent = `Could not get prediction: ${err.message}`;
    }
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Submit Information";
    }
  }
}

function renderUpdatePreview(pid, note, body) {
  const adj = body.result.adjudicator;
  const shown = body.live_priority != null ? body.live_priority : adj.acuity;
  const fields = body.parsed.fields_found.length
    ? body.parsed.fields_found.map((f) => `<span class="tag">${esc(f.replace(/_/g, " "))}</span>`).join(" ")
    : '<span class="muted">nothing structured could be extracted</span>';
  const gaps = (body.information_gaps || []).length
    ? `<p class="io-drivers"><strong>Still needed:</strong> ${body.information_gaps.map(esc).join("; ")}</p>`
    : "";

  const whyBits = (adj.top_drivers || [])
    .filter((d) => d && !/expected resource/i.test(d) && !/^decision\s+[a-d]/i.test(d))
    .slice(0, 3);
  const whyLine = whyBits.length
    ? `<p class="io-drivers"><strong>Why this level:</strong> ${whyBits.map(esc).join("; ")}</p>`
    : "";

  const parseSource = body.parsed.source || "rule-based";
  const explainSource = body.explanation.source || "template";
  const isGemini = body.decision_source === "agent"
    || parseSource === "gemini"
    || explainSource === "gemini"
    || explainSource === "agent";
  const sourceBadge = isGemini
    ? sourceTag("agent")
    : sourceTag("deterministic_fallback");

  const host = $("#detail .pending-info");
  if (!host) return;

  host.innerHTML = `
    <h3>New Assessment Preview</h3>
    <div class="update-preview">
      <div class="io-head">
        ${esiChip(shown, true)}
        <div class="io-meta">
          <h3>ESI ${shown} &middot; ${esc(adj.placement)}</h3>
          <div class="sub">Reassessment with new information &middot; ${sourceBadge}</div>
        </div>
      </div>
      <div class="io-explain">${esc(body.explanation.text)}</div>
      <div class="io-fields">${fields}</div>
      ${whyLine}
      ${gaps}
    </div>
    <p class="muted" style="margin-top:12px;">Is this assessment correct?</p>
    <div class="update-confirm-actions">
      <button type="button" class="primary" id="btn-confirm-update">Yes, confirm and apply</button>
      <button type="button" class="ghost" id="btn-reject-update">No, add more info</button>
    </div>
    <p class="form-msg" id="update-msg"></p>
  `;

  $("#btn-confirm-update").addEventListener("click", () => confirmUpdate(pid, note));
  $("#btn-reject-update").addEventListener("click", () => rejectUpdate(pid));
}

async function confirmUpdate(pid, note) {
  const btn = $("#btn-confirm-update");
  const msg = $("#update-msg");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Applying...";
  }

  try {
    const body = await api(`/api/patients/${encodeURIComponent(pid)}/update`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note, confirm: true }),
    });
    await loadBoard();
    if (body.detail) {
      renderDetail(body.detail);
    } else {
      selectPatient(pid);
    }
    loadAudit();
    loadExplanation(pid);
  } catch (err) {
    if (msg) {
      msg.className = "form-msg err";
      msg.textContent = `Could not apply update: ${err.message}`;
    }
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Yes, confirm and apply";
    }
  }
}

function rejectUpdate(pid) {
  const host = $("#detail .pending-info");
  if (!host) return;
  host.innerHTML = `
    <h3>Requested Information Pending</h3>
    <p class="muted">The clinician requested more information before finalizing triage.</p>
    <div class="field">
      <label for="update-note">Provide information</label>
      <textarea id="update-note" rows="3" placeholder="e.g. ECG normal, BP rechecked at 120/80..."></textarea>
    </div>
    <button type="button" class="primary" id="btn-update-patient">Submit Information</button>
    <p class="form-msg" id="update-msg"></p>
  `;
  $("#btn-update-patient").addEventListener("click", () => submitPatientUpdate(pid));
}

async function submitHitl(pid, action) {
  const msg = $("#hitl-msg");
  const reason = ($("#hitl-reason") && $("#hitl-reason").value.trim()) || "";
  const priority = $("#hitl-priority") ? Number($("#hitl-priority").value) : null;
  const actor = ($("#hitl-actor") && $("#hitl-actor").value.trim());

  if (!actor) {
    msg.className = "form-msg err";
    msg.textContent = "Your name is required.";
    return;
  }

  if ((action === "modify" || action === "override") && reason.length < 3) {
    msg.className = "form-msg err";
    msg.textContent = "A reason is required for modify.";
    return;
  }
  if ((action === "modify" || action === "override") && !priority) {
    msg.className = "form-msg err";
    msg.textContent = "Choose a priority for modify.";
    return;
  }
  if (action === "request_more_information" && reason.length < 3) {
    msg.className = "form-msg err";
    msg.textContent = "A reason is required for this action.";
    return;
  }

  const payload = {
    patient_id: pid,
    action,
    actor,
    actor_role: "triage nurse",
    reason: reason || (action === "accept" ? "Accepted clinical assessment." : reason),
    active_priority: (action === "modify" || action === "override") ? priority : null,
  };

  try {
    const detail = await api("/api/hitl", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    rememberClinicianAction(pid, action);
    applyClinicianActionToRow(detail.row);
    await loadBoard();
    renderDetail(detail);
    const note = $("#hitl-msg");
    if (note) {
      note.className = "form-msg ok";
      note.textContent = `${hitlLabel(action)} recorded.`;
    }
    loadAudit();
    loadExplanation(pid);
  } catch (err) {
    if (msg) {
      msg.className = "form-msg err";
      msg.textContent = `Could not record: ${err.message}`;
    }
  }
}

async function selectPatient(pid) {
  state.selected = pid;
  $$("#board-body tr").forEach((tr) => tr.classList.toggle("is-selected", tr.dataset.id === pid));
  const detail = await api(`/api/patients/${encodeURIComponent(pid)}`);
  rememberClinicianAction(pid, resolveClinicianAction(detail.row, detail.audit));
  applyClinicianActionToRow(detail.row);
  if (state.board) {
    const boardRow = state.board.rows.find((r) => r.patient_id === pid);
    applyClinicianActionToRow(boardRow);
    renderBoard(state.board);
  }
  renderDetail(detail);
  loadExplanation(pid);
}

async function loadExplanation(pid) {
  try {
    const ex = await api(`/api/patients/${encodeURIComponent(pid)}/explanation`);
    if (state.selected !== pid) return;
    const host = $("#explain-slot");
    if (host) {
      host.innerHTML = `<div class="explain-box">${esc(ex.text)}</div>`;
    }
  } catch (_) { /* explanation is optional */ }
}

async function submitOverride(pid) {
  const msg = $("#ov-msg");
  const payload = {
    patient_id: pid,
    new_acuity: Number($("#ov-acuity").value),
    reason: $("#ov-reason").value.trim(),
    actor: $("#ov-actor").value.trim() || "unnamed clinician",
    actor_role: $("#ov-role").value.trim() || "triage nurse",
  };
  if (payload.reason.length < 3) {
    msg.className = "form-msg err";
    msg.textContent = "A reason is required before an override can be recorded.";
    return;
  }
  try {
    const detail = await api("/api/overrides", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await loadBoard();
    renderDetail(detail);
    const note = $("#ov-msg");
    note.className = "form-msg ok";
    note.textContent = "Override recorded to the audit trail.";
    loadAudit();
  } catch (err) {
    msg.className = "form-msg err";
    msg.textContent = `Could not record override: ${err.message}`;
  }
}

/* ---------- audit ---------- */

async function loadAudit() {
  const records = await api("/api/audit");
  if (!records.length) {
    $("#audit-body").innerHTML =
      '<tr><td colspan="8" class="muted">Nothing recorded yet. Change a patient\'s acuity to see the trail.</td></tr>';
    return;
  }
  $("#audit-body").innerHTML = records.slice().reverse().map((r) => {
    const dirClass = r.direction === "escalate" ? "up" : r.direction === "de-escalate" ? "down" : "";
    const change = r.before_acuity == null
      ? NIL
      : `${r.before_acuity} to ${r.after_acuity} <span class="tag ${dirClass}">${esc(r.direction)}</span>`;
    return `
      <tr>
        <td class="mono">${r.record_id}</td>
        <td class="mono">${esc(r.timestamp_utc)}</td>
        <td class="mono">${esc(r.patient_id)}</td>
        <td>${esc(r.action)}</td>
        <td>${change}</td>
        <td>${esc(r.actor)}<div class="pid">${esc(r.actor_role)}</div></td>
        <td class="reason">${esc(r.reason)}</td>
        <td class="mono">v${esc(r.model_version)}</td>
      </tr>`;
  }).join("");
}

/* ---------- free-text intake ---------- */

const INTAKE_CHIP_META = {
  history: { input: "#intake-history-input", list: "#intake-history-chips" },
  medications: { input: "#intake-meds-input", list: "#intake-meds-chips" },
  allergies: { input: "#intake-allergies-input", list: "#intake-allergies-chips" },
};

function renderIntakeChips(kind) {
  const meta = INTAKE_CHIP_META[kind];
  if (!meta) return;
  const host = $(meta.list);
  const items = state.intakeChips[kind] || [];
  host.innerHTML = items.map((item, idx) => `
    <span class="intake-chip">
      ${esc(item)}
      <button type="button" data-chip-kind="${esc(kind)}" data-chip-idx="${idx}" aria-label="Remove ${esc(item)}">&times;</button>
    </span>`).join("");
  host.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      const k = btn.dataset.chipKind;
      const i = Number(btn.dataset.chipIdx);
      state.intakeChips[k].splice(i, 1);
      renderIntakeChips(k);
      syncPriorFromChips();
    });
  });
}

function addIntakeChip(kind) {
  const meta = INTAKE_CHIP_META[kind];
  if (!meta) return;
  const input = $(meta.input);
  const value = (input.value || "").trim();
  if (!value) return;
  const list = state.intakeChips[kind];
  if (!list.some((x) => x.toLowerCase() === value.toLowerCase())) {
    list.push(value);
    renderIntakeChips(kind);
  }
  input.value = "";
  input.focus();
  syncPriorFromChips();
}

function syncPriorFromChips() {
  const prior = $("#intake-prior");
  if (!prior) return;
  const any = Object.values(state.intakeChips).some((arr) => arr.length);
  if (any) {
    prior.checked = true;
    setIntakeHistoryOpen(true);
  }
}

function setIntakeHistoryOpen(open) {
  const panel = $("#intake-history-panel");
  const body = $("#intake-history-body");
  const btn = $("#intake-history-toggle");
  if (!panel || !body || !btn) return;
  state.intakeHistoryOpen = open;
  panel.classList.toggle("is-open", open);
  body.hidden = !open;
  btn.setAttribute("aria-expanded", String(open));
}

function toggleIntakeHistory() {
  setIntakeHistoryOpen(!state.intakeHistoryOpen);
}

const INTAKE_VITAL_FIELDS = [
  "#intake-vital-hr",
  "#intake-vital-bp",
  "#intake-vital-spo2",
  "#intake-vital-rr",
  "#intake-vital-temp",
  "#intake-vital-other",
];

function formatBpValue(raw) {
  const bp = raw.replace(/\s+/g, "");
  if (!bp) return "";
  if (bp.includes("/")) return `BP ${bp}`;
  if (/^\d+$/.test(bp)) return `BP ${bp}`;
  return `BP ${raw.trim()}`;
}

function composeVitalsLine() {
  const hr = ($("#intake-vital-hr").value || "").trim();
  const bp = ($("#intake-vital-bp").value || "").trim();
  const spo2 = ($("#intake-vital-spo2").value || "").trim();
  const rr = ($("#intake-vital-rr").value || "").trim();
  const temp = ($("#intake-vital-temp").value || "").trim();
  const other = ($("#intake-vital-other").value || "").trim();
  const parts = [];
  if (hr) parts.push(`HR ${hr}`);
  if (bp) parts.push(formatBpValue(bp));
  if (spo2) {
    const pct = spo2.endsWith("%") ? spo2 : `${spo2}%`;
    parts.push(`SpO2 ${pct}`);
  }
  if (rr) parts.push(`RR ${rr}`);
  if (temp) {
    const unit = state.intakeTempUnit === "F" ? "F" : "C";
    parts.push(`temp ${temp}°${unit}`);
  }
  if (other) parts.push(other);
  return parts.join(", ");
}

function setIntakeTempUnit(unit, button) {
  state.intakeTempUnit = unit === "F" ? "F" : "C";
  $$(".unit-switch button").forEach((b) => b.classList.toggle("is-active", b === button));
  const input = $("#intake-vital-temp");
  if (input) input.placeholder = state.intakeTempUnit === "F" ? "e.g. 98.6" : "e.g. 37.2";
}

function composeIntakeText() {
  const name = ($("#intake-name").value || "").trim();
  const age = ($("#intake-age").value || "").trim();
  const sex = ($("#intake-sex").value || "").trim();
  const vitals = composeVitalsLine();
  const note = ($("#intake-text").value || "").trim();
  const prior = $("#intake-prior") && $("#intake-prior").checked;
  const history = state.intakeChips.history;
  const meds = state.intakeChips.medications;
  const allergies = state.intakeChips.allergies;
  const parts = [];
  if (name) parts.push(`Patient Name: ${name}`);
  if (age) parts.push(`Age: ${age}`);
  if (sex) parts.push(`Sex: ${sex}`);
  if (vitals) parts.push(`Vitals: ${vitals}`);
  if (note) parts.push(`Note: ${note}`);
  if (prior || history.length || meds.length || allergies.length) {
    parts.push(`Prior record: ${prior || history.length || meds.length || allergies.length ? "yes" : "no"}`);
  }
  if (history.length) parts.push(`History: ${history.join(", ")}`);
  if (meds.length) parts.push(`Medications: ${meds.join(", ")}`);
  if (allergies.length) parts.push(`Allergies: ${allergies.join(", ")}`);
  return parts.join("\n");
}

function clearIntakeForm() {
  $("#intake-name").value = "";
  $("#intake-age").value = "";
  $("#intake-sex").value = "";
  INTAKE_VITAL_FIELDS.forEach((sel) => { if ($(sel)) $(sel).value = ""; });
  const tempUnitDefault = $(".unit-switch button[data-temp-unit='C']");
  if (tempUnitDefault) setIntakeTempUnit("C", tempUnitDefault);
  $("#intake-text").value = "";
  if ($("#intake-prior")) $("#intake-prior").checked = false;
  setIntakeHistoryOpen(false);
  state.intakeChips = { history: [], medications: [], allergies: [] };
  Object.keys(INTAKE_CHIP_META).forEach(renderIntakeChips);
  ["#intake-history-input", "#intake-meds-input", "#intake-allergies-input"].forEach((sel) => {
    if ($(sel)) $(sel).value = "";
  });
  state.lastIntakeText = null;
  state.lastIntakePreviewId = null;
}

function renderIntake(body) {
  const {
    parsed,
    result,
    explanation,
    added_patient_id,
    needs_more_information,
    information_gaps,
    live_priority,
    decision_source,
  } = body;
  const adj = result.adjudicator;
  const fields = parsed.fields_found.length
    ? parsed.fields_found.map((f) => `<span class="tag">${esc(f.replace(/_/g, " "))}</span>`).join(" ")
    : '<span class="muted">nothing structured could be extracted</span>';
  const note = parsed.note ? `<p class="io-note">${esc(parsed.note)}</p>` : "";
  const gaps = (information_gaps || []).length
    ? `<p class="io-drivers"><strong>Still needed:</strong> ${information_gaps.map(esc).join("; ")}</p>`
    : "";

  const addBtn = added_patient_id
    ? `<span class="tag override">added as ${esc(added_patient_id)}</span>`
    : `<button type="button" class="primary" id="intake-add-board">Add to live board</button>`;

  const sourceBadge = sourceTag(decision_source);

  if (needs_more_information) {
    const ref = live_priority != null ? live_priority : adj.acuity;
    $("#intake-output").innerHTML = `
      <div class="io-head">
        <div class="esi esi-unknown" title="Not a firm triage decision">?</div>
        <div class="io-meta">
          <h3>Need more information</h3>
          <div class="sub">Assessment complete &middot; ${sourceBadge}</div>
        </div>
      </div>
      <div class="io-explain">${esc(explanation.text)}</div>
      <div class="io-fields">${fields}</div>
      ${note}
      ${gaps}
      <p class="io-drivers muted">Reference only: ESI ${ref} · ${esc(adj.placement)}</p>
      <div class="intake-followup">${addBtn}</div>`;
  } else {
    const shown = live_priority != null ? live_priority : adj.acuity;
    const whyBits = (adj.top_drivers || [])
      .filter((d) => d && !/expected resource/i.test(d) && !/^decision\s+[a-d]/i.test(d))
      .slice(0, 3);
    const whyLine = whyBits.length
      ? `<p class="io-drivers"><strong>Why this level:</strong> ${whyBits.map(esc).join("; ")}</p>`
      : "";
    $("#intake-output").innerHTML = `
      <div class="io-head">
        ${esiChip(shown, true)}
        <div class="io-meta">
          <h3>ESI ${shown} &middot; ${esc(adj.placement)}</h3>
          <div class="sub">Assessment complete &middot; ${sourceBadge}</div>
        </div>
      </div>
      <div class="io-explain">${esc(explanation.text)}</div>
      <div class="io-fields">${fields}</div>
      ${note}
      ${whyLine}
      <div class="intake-followup">${addBtn}</div>`;
  }

  const follow = $("#intake-add-board");
  if (follow) follow.addEventListener("click", addIntakeToBoard);
}

async function runIntake() {
  const text = composeIntakeText();
  const btn = $("#intake-run");
  if (text.length < 2) {
    $("#intake-output").innerHTML = '<p class="form-msg err">Enter a name, age, vitals, or note first.</p>';
    return;
  }
  btn.disabled = true;
  btn.textContent = "Working...";
  try {
    const body = await api("/api/intake", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, add_to_board: false }),
    });
    state.lastIntakeText = text;
    state.lastIntakePreviewId = body.preview_patient_id;
    renderIntake(body);
  } catch (err) {
    state.lastIntakeText = null;
    state.lastIntakePreviewId = null;
    $("#intake-output").innerHTML = `<p class="form-msg err">Intake failed: ${esc(err.message)}</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Parse and score";
  }
}

async function addIntakeToBoard() {
  const text = composeIntakeText();
  const btn = $("#intake-add-board");
  if (!text || text.length < 2) {
    $("#intake-output").innerHTML = '<p class="form-msg err">Assess the patient first, then add to the board.</p>';
    return;
  }
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Adding...";
  }
  try {
    const payload = { text, add_to_board: true };
    if (state.lastIntakePreviewId) {
      payload.preview_patient_id = state.lastIntakePreviewId;
    }
    const body = await api("/api/intake", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderIntake(body);
    if (body.added_patient_id) {
      clearIntakeForm();
      await loadBoard();
      selectPatient(body.added_patient_id);
      loadAudit();
    }
  } catch (err) {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Add to live board";
    }
    const host = $("#intake-output");
    if (host) {
      host.insertAdjacentHTML(
        "beforeend",
        `<p class="form-msg err">Could not add: ${esc(err.message)}</p>`
      );
    }
  }
}

/* ---------- watcher simulation ---------- */

function sparkline(metrics) {
  const W = 600, H = 140, pad = 6;
  const maxWait = Math.max(1, ...metrics.map((m) => m.waiting));
  const maxUnsafe = Math.max(1, ...metrics.map((m) => m.longest_unsafe_wait_min));
  const x = (i) => pad + (i / Math.max(1, metrics.length - 1)) * (W - pad * 2);
  const y = (v, max) => H - pad - (v / max) * (H - pad * 2);
  const pts = (key, max) => metrics.map((m, i) => `${x(i).toFixed(1)},${y(m[key], max).toFixed(1)}`).join(" ");
  const grid = [0.25, 0.5, 0.75].map((f) =>
    `<line x1="${pad}" y1="${(H * f).toFixed(1)}" x2="${W - pad}" y2="${(H * f).toFixed(1)}"
           stroke="#efeff3" stroke-width="1" />`).join("");

  return `
    <div class="legend">
      <span><i class="l-wait"></i>waiting (peak ${maxWait})</span>
      <span><i class="l-unsafe"></i>longest unsafe wait (peak ${maxUnsafe} min)</span>
    </div>
    <svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img"
         aria-label="Waiting-room load and longest unsafe wait across the run">
      ${grid}
      <polygon points="${x(0).toFixed(1)},${H - pad} ${pts("waiting", maxWait)} ${x(metrics.length - 1).toFixed(1)},${H - pad}"
               fill="#a100ff" fill-opacity="0.07" />
      <polyline points="${pts("waiting", maxWait)}" fill="none" stroke="#a100ff"
                stroke-width="2" vector-effect="non-scaling-stroke" />
      <polyline points="${pts("longest_unsafe_wait_min", maxUnsafe)}" fill="none" stroke="#d9002b"
                stroke-width="2" vector-effect="non-scaling-stroke" />
    </svg>`;
}

function renderSim(report) {
  const cards = [
    { label: "Patients", value: report.total_patients, sub: report.label },
    { label: "Peak load", value: report.peak_waiting, sub: "waiting at once" },
    { label: "Longest unsafe wait", value: `${report.peak_unsafe_wait_min}m`, sub: "past safe limit", cls: report.peak_unsafe_wait_min > 60 ? "danger" : "warn" },
    { label: "Caught deteriorating", value: report.total_ratchets, sub: "escalated while waiting", cls: report.total_ratchets ? "brand" : "" },
    { label: "Backstop alerts", value: report.total_backstops, sub: "safe wait breached", cls: "warn" },
    { label: "De-escalations", value: report.down_ratchets, sub: "must be zero" },
  ];
  const events = report.events.length
    ? report.events.slice(0, 40).map((e) => `
        <li>
          <span class="t">t+${e.time_min}m</span>
          <span class="who">${esc(e.patient_id)}</span>
          <span><span class="k-${esc(e.kind)}">${e.kind === "ratchet" ? `ESI ${e.before_acuity} to ${e.after_acuity}` : "unsafe wait"}</span>
          &middot; ${esc(e.detail)}</span>
        </li>`).join("")
    : '<li class="muted">No notable events.</li>';

  $("#sim-output").innerHTML = `
    <div class="sim-grid">${cards.map((c) => `
      <div class="kpi">
        <div class="label">${esc(c.label)}</div>
        <div class="value ${c.cls || ""}">${c.value}</div>
        <div class="sub">${esc(c.sub)}</div>
      </div>`).join("")}</div>
    <div class="sim-chart">
      <h3>${esc(report.label)}, ${report.ticks} five-minute ticks</h3>
      ${sparkline(report.metrics)}
    </div>
    <div class="section-label">Watcher events</div>
    <ul class="events">${events}</ul>`;
}

async function runSim(factor, button) {
  const buttons = $$(".sim-actions button");
  const original = button.innerHTML;
  buttons.forEach((b) => (b.disabled = true));
  button.textContent = "Running...";
  try {
    renderSim(await api(`/api/simulation?surge_factor=${factor}`));
  } catch (err) {
    $("#sim-output").innerHTML = `<p class="form-msg err">Simulation failed: ${esc(err.message)}</p>`;
  } finally {
    buttons.forEach((b) => (b.disabled = false));
    button.innerHTML = original;
  }
}

/* ---------- wiring ---------- */

async function setSurge(factor, button) {
  const buttons = $$(".topbar-actions .switch button");
  const prev = button.textContent;
  state.surge = factor;
  buttons.forEach((b) => {
    b.classList.toggle("is-active", b === button);
    b.disabled = true;
  });
  button.textContent = factor === 3 ? "Loading 3×…" : "Loading…";
  state.selected = null;
  try {
    renderBoard(await api(`/api/board/reset?surge_factor=${factor}`, { method: "POST" }));
    if (state.board && state.board.rows.length) {
      selectPatient(state.board.rows[0].patient_id);
    }
    loadAudit();
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
    button.textContent = prev;
  }
}

function selectTab(name) {
  ["audit", "sim"].forEach((t) =>
    $(`#tab-${t}`).classList.toggle("is-hidden", t !== name));
}

function init() {
  $$(".topbar-actions .switch button").forEach((b) =>
    b.addEventListener("click", () => setSurge(Number(b.dataset.surge), b)));
  $$(".unit-switch button").forEach((b) =>
    b.addEventListener("click", () => setIntakeTempUnit(b.dataset.tempUnit, b)));
  $$(".sim-actions button").forEach((b) =>
    b.addEventListener("click", () => runSim(Number(b.dataset.sim), b)));
  $$(".tabs button").forEach((b) => b.addEventListener("click", () => {
    $$(".tabs button").forEach((o) => {
      o.classList.toggle("is-active", o === b);
      o.setAttribute("aria-selected", String(o === b));
    });
    selectTab(b.dataset.tab);
  }));

  $("#intake-run").addEventListener("click", runIntake);
  $("#intake-history-toggle")?.addEventListener("click", toggleIntakeHistory);
  $("#intake-prior")?.addEventListener("change", (ev) => {
    if (ev.target.checked) setIntakeHistoryOpen(true);
  });

  Object.keys(INTAKE_CHIP_META).forEach((kind) => {
    renderIntakeChips(kind);
    const meta = INTAKE_CHIP_META[kind];
    const input = $(meta.input);
    if (input) {
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          addIntakeChip(kind);
        }
      });
    }
  });
  $$("[data-chip-add]").forEach((btn) => {
    btn.addEventListener("click", () => addIntakeChip(btn.dataset.chipAdd));
  });

  loadBoard();
  loadAudit();
}

document.addEventListener("DOMContentLoaded", init);
