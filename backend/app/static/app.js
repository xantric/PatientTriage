"use strict";

const state = { surge: 1, selected: null, board: null, llm: null };

const EXAMPLES = [
  "78F by ambulance, feeling unwell and short of breath, sweaty. HR 104, BP 118/70, SpO2 93%, hx of diabetes and CAD",
  "3yo high fever and very drowsy since morning, HR 158, RR 42, temp 39.8",
  "34M needs a refill for his blood pressure tablets, otherwise well",
  "feels a bit off",
];

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
    { label: "Nurse review", value: summary.routed_to_nurse, sub: "low confidence", cls: summary.routed_to_nurse ? "warn" : "", icon: "eye" },
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

function renderBoard(board) {
  state.board = board;
  renderKpis(board.summary);

  $("#board-body").innerHTML = board.rows.map((r) => {
    const flags = [];
    if (r.routed_to_nurse) flags.push('<span class="tag nurse">nurse review</span>');
    if (r.escalated_for_uncertainty) flags.push('<span class="tag up">escalated</span>');
    if (r.red_flag_count) flags.push(`<span class="tag flag">${r.red_flag_count} red</span>`);
    if (r.overridden) {
      const arrow = r.override_direction === "escalate" ? "up" : r.override_direction === "de-escalate" ? "down" : "";
      flags.push(`<span class="tag override">override ${arrow}</span>`);
    }
    const clocks = r.clocks.length
      ? r.clocks.map((c) => `<span class="tag clock${c.includes("missed") ? " missed" : ""}">${esc(c)}</span>`).join(" ")
      : NIL;

    return `
      <tr data-id="${esc(r.patient_id)}" class="${state.selected === r.patient_id ? "is-selected" : ""}">
        <td>${esiChip(r.acuity)}</td>
        <td>
          <div class="pname">${esc(r.display_name || "Walk-in")}</div>
          <div class="pid">${esc(r.patient_id)}</div>
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

async function loadBoard() {
  renderBoard(await api("/api/board"));
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

function renderOverrideForm(row) {
  /* kept for compatibility; HITL panel is primary */
  return "";
}

function renderHitl(agent, row) {
  if (!agent) return "";
  const options = [1, 2, 3, 4, 5].map((l) =>
    `<option value="${l}" ${l === (agent.priority || row.acuity) ? "selected" : ""}>P${l}</option>`
  ).join("");
  
  const needsReview = agent.human_review_required;
  const acceptBtn = needsReview ? '<button type="button" class="primary" data-hitl="accept">Accept</button>' : '';
  const escalateBtn = needsReview ? '<button type="button" class="ghost danger-btn" data-hitl="escalate">Escalate</button>' : '';

  return `
    <div class="section agent-hitl">
      <h3>Human-in-the-loop</h3>
      <p class="muted">Clinician has final authority. Modify and override require a reason.</p>
      <div class="hitl-actions" id="hitl-actions">
        ${acceptBtn}
        <button type="button" class="ghost" data-hitl="modify">Modify</button>
        <button type="button" class="ghost" data-hitl="override">Override</button>
        ${escalateBtn}
      </div>
      <div class="hitl-form" id="hitl-form">
        <div class="field-row">
          <div class="field">
            <label for="hitl-priority">Priority (modify / override)</label>
            <select id="hitl-priority">${options}</select>
          </div>
          <div class="field">
            <label for="hitl-actor">Clinician</label>
            <input type="text" id="hitl-actor" value="A. Nurse" />
          </div>
        </div>
        <div class="field">
          <label for="hitl-reason">Reason</label>
          <textarea id="hitl-reason" placeholder="Required for modify and override"></textarea>
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
    : `<p class="muted">No material gaps flagged.</p>`;
  const agree = agent.agreement == null
    ? "n/a"
    : agent.agreement ? "YES" : "NO";
  const disagree = agent.disagreement_reason
    ? `<p class="disagree">${esc(agent.disagreement_reason)}</p>`
    : "";
  const timeline = (agent.timeline || []).map((t) =>
    `<li><span class="t">${esc(t.kind)}</span><span>${esc(t.label)}</span></li>`
  ).join("");

  return `
    <div class="section agent-panel">
      <h3>Agent assessment</h3>
      <div class="agent-status">
        <span class="status-pill">${esc(agent.status_label)}</span>
        <span class="tag">${esc(agent.decision_source.replace(/_/g, " "))}</span>
        ${agent.baseline_used ? '<span class="tag">baseline consulted</span>' : ""}
      </div>

      <div class="agent-grid">
        <div>
          <div class="label">Priority</div>
          <div class="value">${agent.priority != null ? "P" + agent.priority : "—"}</div>
        </div>
        <div>
          <div class="label">Urgency</div>
          <div class="value sm">${esc(agent.urgency || "—")}</div>
        </div>
        <div>
          <div class="label">Confidence</div>
          <div class="value sm">${agent.confidence != null ? agent.confidence.toFixed(2) : "—"}</div>
        </div>
      </div>
      <p><strong>Care pathway:</strong> ${esc(agent.care_pathway || "—")}</p>
      <p><strong>Monitoring plan:</strong> ${esc(agent.monitoring_plan || "—")}</p>
      ${agent.reason_summary ? `<p class="whatif">${esc(agent.reason_summary)}</p>` : ""}

      <h4>Key evidence</h4>
      ${evidence}

      <h4>Information gaps</h4>
      ${gaps}

      <h4>Baseline comparison</h4>
      <div class="baseline-box">
        <div>Agent recommendation: <strong>P${agent.agent_priority ?? "—"}</strong></div>
        <div>Deterministic baseline: <strong>P${agent.baseline_priority ?? "—"}</strong></div>
        <div>Agreement: <strong class="${agent.agreement === false ? "bad" : ""}">${agree}</strong></div>
        ${disagree}
      </div>

      <h4>Agent timeline</h4>
      <ul class="events timeline">${timeline || '<li class="muted">No operational events yet.</li>'}</ul>
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

  const overrideNote = row.overridden
    ? `<div class="engine-said">Agent/baseline on file. Clinician set P${row.acuity} (${esc(row.override_direction || "set")}).</div>`
    : "";

  const confNote = row.decision_source === "agent"
    ? "Agent recommendation awaiting or completed clinician review."
    : "Deterministic fallback recommendation (Gemini unavailable or incomplete).";

  $("#detail").innerHTML = `
    <div class="detail-head">
      ${esiChip(row.acuity, true)}
      <div class="dh-main">
        <h2>${esc(row.display_name || "Walk-in")}</h2>
        <div class="sub">${esc(row.patient_id)} &middot; ${row.age_years}y ${esc(row.age_band)} &middot; arrived t+${row.arrival_epoch_min}m</div>
        ${overrideNote}
        <div class="placement">${esc((agent && agent.care_pathway) || adj.placement)} &middot; ${esc(row.decision_source.replace(/_/g, " "))}</div>
        <div class="dh-conf">${confMeter(row.confidence, row.confidence_band)}<span class="dh-conf-note">${esc(confNote)}</span></div>
      </div>
    </div>

    <div class="section">
      <h3>Presenting complaint</h3>
      <p>${esc(patient.chief_complaint)}</p>
    </div>

    ${renderAgentPanel(agent, row)}
    ${renderHitl(agent, row)}

    <div class="section">
      <h3>Plain-language read</h3>
      <div id="explain-slot"><p class="muted">Generating explanation...</p></div>
    </div>

    ${renderClocks(adj.time_critical_clocks)}
    ${renderVitals(interp.vital_flags)}
    ${renderAuditFor(audit)}
  `;

  $$("#hitl-actions button").forEach((btn) => {
    btn.addEventListener("click", () => submitHitl(row.patient_id, btn.dataset.hitl));
  });
}

async function submitHitl(pid, action) {
  const msg = $("#hitl-msg");
  const reason = ($("#hitl-reason") && $("#hitl-reason").value.trim()) || "";
  const priority = $("#hitl-priority") ? Number($("#hitl-priority").value) : null;
  const actor = ($("#hitl-actor") && $("#hitl-actor").value.trim()) || "A. Nurse";

  if ((action === "modify" || action === "override") && reason.length < 3) {
    msg.className = "form-msg err";
    msg.textContent = "A reason is required for modify and override.";
    return;
  }
  if ((action === "modify" || action === "override") && !priority) {
    msg.className = "form-msg err";
    msg.textContent = "Choose a priority for modify/override.";
    return;
  }
  if ((action === "escalate" || action === "request_more_information") && reason.length < 3) {
    msg.className = "form-msg err";
    msg.textContent = "A reason is required for this action.";
    return;
  }

  const payload = {
    patient_id: pid,
    action,
    actor,
    actor_role: "triage nurse",
    reason: reason || (action === "accept" ? "Accepted agent recommendation." : reason),
    active_priority: (action === "modify" || action === "override" || action === "accept")
      ? (action === "accept" ? null : priority)
      : null,
  };

  try {
    const detail = await api("/api/hitl", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await loadBoard();
    renderDetail(detail);
    const note = $("#hitl-msg");
    if (note) {
      note.className = "form-msg ok";
      note.textContent = `Recorded ${action.replace(/_/g, " ")}.`;
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
  renderDetail(await api(`/api/patients/${encodeURIComponent(pid)}`));
  loadExplanation(pid);
}

async function loadExplanation(pid) {
  try {
    const ex = await api(`/api/patients/${encodeURIComponent(pid)}/explanation`);
    if (state.selected !== pid) return;
    const host = $("#explain-slot");
    if (host) {
      const src =
        ex.source === "gemini"
          ? "Gemini, level verified"
          : ex.source === "ollama"
            ? "Ollama, level verified"
            : "template";
      host.innerHTML = `<div class="explain-box">${esc(ex.text)}<span class="src">${esc(src)}</span></div>`;
    }
    loadTelemetry();
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

async function runIntake() {
  const name = ($("#intake-name").value || "").trim();
  const vitals = ($("#intake-vitals").value || "").trim();
  const note = ($("#intake-text").value || "").trim();
  
  const parts = [];
  if (name) parts.push(`Patient Name: ${name}`);
  if (vitals) parts.push(`Vitals: ${vitals}`);
  if (note) parts.push(`Note: ${note}`);
  const text = parts.join(", ");
  
  const btn = $("#intake-run");
  if (text.length < 2) {
    btn.textContent = 'Type a note first.';
    setTimeout(() => btn.textContent = "Parse, Score & Add to Board", 2000);
    return;
  }
  btn.disabled = true;
  btn.textContent = "Working...";
  try {
    const body = await api("/api/intake", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, add_to_board: true }),
    });
    
    if (body.added_patient_id) {
      $("#intake-name").value = "";
      $("#intake-vitals").value = "";
      $("#intake-text").value = "";
      await loadBoard();
      selectPatient(body.added_patient_id);
    }
    loadTelemetry();
  } catch (err) {
    btn.textContent = `Failed: ${esc(err.message)}`;
    setTimeout(() => {
        btn.disabled = false;
        btn.textContent = "Parse, Score & Add to Board";
    }, 3000);
    return;
  }
  btn.disabled = false;
  btn.textContent = "Parse, Score & Add to Board";
}

/* ---------- llm status + telemetry ---------- */

function applyLlmStatus(status) {
  state.llm = status;
  const badge = $("#ai-badge");
  const chip = $("#intake-mode");
  // The model name already starts with "gemini-", so don't prefix it again.
  const label = status.live ? (status.model || "Gemini") : "rule-based";
  badge.textContent = status.live ? "AI: Gemini" : "AI: rule-based";
  badge.classList.toggle("live", status.live);
  badge.title = status.live
    ? `Live: ${status.model}`
    : status.key_present ? "Key present but package missing" : "No key set, running the deterministic fallback";
  if (chip) {
    chip.textContent = label;
    chip.classList.toggle("live", status.live);
  }
}

async function loadLlmStatus() {
  try { applyLlmStatus(await api("/api/llm/status")); } catch (_) { /* badge stays default */ }
}

function renderTelemetry(t) {
  const s = t.status;
  const cards = [
    { label: "Calls", value: t.total_calls },
    { label: "Cache hits", value: t.cache_hits },
    { label: "Fallbacks", value: t.fallbacks, cls: t.fallbacks ? "warn" : "" },
    { label: "Errors", value: t.errors, cls: t.errors ? "danger" : "" },
    { label: "Avg latency", value: `${t.avg_latency_ms}ms` },
    { label: "Est. cost", value: `$${t.est_cost_usd.toFixed(4)}`, cls: "brand" },
  ];
  const rows = t.recent.length
    ? t.recent.map((c) => `
        <tr>
          <td class="mono">${esc(c.timestamp_utc.slice(11, 19))}</td>
          <td>${esc(c.task)}</td>
          <td>${esc(c.provider)}${c.cached ? " (cached)" : ""}</td>
          <td class="mono">${c.input_tokens}/${c.output_tokens}</td>
          <td class="mono">${c.latency_ms}ms</td>
          <td class="${!c.ok ? "bad" : c.fell_back ? "fell" : "ok"}">${!c.ok ? "error" : c.fell_back ? "fell back" : "ok"}</td>
        </tr>`).join("")
    : '<tr><td colspan="6" class="muted">No calls yet. Run a free-text intake or open a patient.</td></tr>';

  const mode = s.live
    ? `<span class="live">live on ${esc(s.model)}</span>`
    : `rule-based (${s.key_present ? "key present, package missing" : "no key set"})`;

  $("#telemetry-output").innerHTML = `
    <p class="tele-status">Mode: <b>${mode}</b> &middot; configured <b>${esc(s.configured_mode)}</b></p>
    <div class="tele-grid">${cards.map((c) => `
      <div class="kpi">
        <div class="label">${esc(c.label)}</div>
        <div class="value ${c.cls || ""}">${c.value}</div>
      </div>`).join("")}</div>
    <div class="section-label">Recent calls</div>
    <div class="table-wrap">
      <table class="tele-table">
        <thead><tr><th>Time</th><th>Task</th><th>Provider</th><th>Tokens in/out</th><th>Latency</th><th>Result</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

async function loadTelemetry() {
  try { renderTelemetry(await api("/api/telemetry")); } catch (_) { /* optional */ }
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
  state.surge = factor;
  $$(".switch button").forEach((b) => b.classList.toggle("is-active", b === button));
  state.selected = null;
  renderBoard(await api(`/api/board/reset?surge_factor=${factor}`, { method: "POST" }));
  if (state.board && state.board.rows.length) {
    selectPatient(state.board.rows[0].patient_id);
  }
  loadAudit();
}

function selectTab(name) {
  ["audit", "sim", "telemetry"].forEach((t) =>
    $(`#tab-${t}`).classList.toggle("is-hidden", t !== name));
  if (name === "telemetry") loadTelemetry();
}

function init() {
  $$(".switch button").forEach((b) =>
    b.addEventListener("click", () => setSurge(Number(b.dataset.surge), b)));
  $("#refresh").addEventListener("click", loadBoard);
  $$(".sim-actions button").forEach((b) =>
    b.addEventListener("click", () => runSim(Number(b.dataset.sim), b)));
  $$(".tabs button").forEach((b) => b.addEventListener("click", () => {
    $$(".tabs button").forEach((o) => {
      o.classList.toggle("is-active", o === b);
      o.setAttribute("aria-selected", String(o === b));
    });
    selectTab(b.dataset.tab);
  }));

  // Free-text intake panel.
  $("#intake-run").addEventListener("click", runIntake);
  $("#intake-examples").innerHTML = EXAMPLES.map((ex, i) =>
    `<button type="button" data-ex="${i}">${esc(ex.slice(0, 34))}...</button>`).join("");
  $$("#intake-examples button").forEach((b) =>
    b.addEventListener("click", () => { $("#intake-text").value = EXAMPLES[Number(b.dataset.ex)]; }));

  loadLlmStatus();
  loadBoard();
  loadAudit();
}

document.addEventListener("DOMContentLoaded", init);
