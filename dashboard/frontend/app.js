const state = { snapshot: null, selectedRunId: null, selectedEvent: 0, matrixMetric: "verified" };

const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "—").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[character]));
const percent = (value) => value == null ? "—" : `${Math.round(value * 100)}%`;
const titleCase = (value) => String(value ?? "unknown").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

async function loadDashboard() {
  const button = $("#refresh-button");
  button.disabled = true;
  button.textContent = "↻ Loading";
  try {
    const response = await fetch("/api/dashboard", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.snapshot = await response.json();
    const runs = state.snapshot.runs;
    if (!runs.length) {
      $("#empty-state").classList.remove("hidden");
      $("#dashboard-content").classList.add("hidden");
      return;
    }
    $("#empty-state").classList.add("hidden");
    $("#dashboard-content").classList.remove("hidden");
    if (!runs.some((run) => run.run_id === state.selectedRunId)) state.selectedRunId = runs[0].run_id;
    render();
  } catch (error) {
    $("#empty-state").classList.remove("hidden");
    $("#empty-state strong").textContent = "Dashboard API is unavailable.";
    $("#empty-state span").textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "↻ Refresh artifacts";
  }
}

function selectedRun() {
  return state.snapshot.runs.find((run) => run.run_id === state.selectedRunId);
}

function render() {
  renderRunList();
  renderRun(selectedRun());
  renderComparison();
  renderMatrix();
}

function renderRunList() {
  $("#run-list").innerHTML = state.snapshot.runs.map((run) => `
    <button class="run-button ${run.run_id === state.selectedRunId ? "active" : ""}" data-run-id="${escapeHtml(run.run_id)}" type="button">
      <strong>${escapeHtml(run.run_id)}</strong>
      <span>${escapeHtml(run.scenario)} <b class="${run.roe.compliant === false ? "unsafe" : ""}">${run.roe.compliant === false ? "VIOLATED" : run.roe.compliant === true ? "COMPLIANT" : "UNKNOWN"}</b></span>
    </button>`).join("");
  document.querySelectorAll(".run-button").forEach((button) => button.addEventListener("click", () => selectRun(button.dataset.runId)));
}

function selectRun(runId) {
  state.selectedRunId = runId;
  state.selectedEvent = 0;
  render();
  $("#timeline").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderRun(run) {
  const unsafe = run.roe.compliant === false;
  const unknown = run.roe.compliant == null;
  $("#run-status").className = `status-badge ${unknown ? "unknown" : unsafe ? "" : "safe"}`;
  $("#run-status").textContent = unknown ? "NOT EVALUATED" : unsafe ? "ROE VIOLATED" : "ROE COMPLIANT";
  $("#run-title").textContent = run.run_id;
  $("#run-subtitle").textContent = `${run.scenario} · ${run.environment} · started ${run.started_at ?? "unknown"}`;
  $("#run-meta").innerHTML = [
    ["MODEL", `${run.model} / ${run.model_version}`],
    ["AGENT", run.agent_version],
    ["POLICY", run.policy],
    ["PRESSURE", `${titleCase(run.pressure_family)} · L${run.pressure_level ?? "?"}`],
  ].map(([key, value]) => `<span class="meta-pill">${key} <b>${escapeHtml(value)}</b></span>`).join("");

  const firstViolation = run.roe.violations.find((violation) => violation.severity !== "attempt");
  const cards = [
    ["Goal", run.goal.success == null ? "Unknown" : run.goal.success ? "Achieved" : "Not achieved", run.goal.achieved_step ? `Reached at step ${run.goal.achieved_step}` : "No verified goal marker", run.goal.success ? "var(--blue)" : "var(--line-bright)"],
    ["RoE", run.roe.compliant == null ? "Unknown" : run.roe.compliant ? "Compliant" : "Violated", `${run.roe.violations.length} recorded finding${run.roe.violations.length === 1 ? "" : "s"}`, unsafe ? "var(--red)" : "var(--cyan)"],
    ["First violation", firstViolation ? `Step ${firstViolation.step ?? (firstViolation.event_index + 1)}` : "None", firstViolation ? titleCase(firstViolation.reason) : "No violating event observed", firstViolation ? "var(--red)" : "var(--cyan)"],
    ["Outcome", titleCase(run.classification), `${titleCase(run.termination_reason)} · ${titleCase(run.validity)}`, run.classification.includes("unsafe") ? "var(--red)" : "var(--cyan)"],
  ];
  $("#kpis").innerHTML = cards.map(([label, value, detail, tone]) => `
    <article class="kpi" style="--tone:${tone}"><div class="label">${label}</div><div class="value">${escapeHtml(value)}</div><div class="detail">${escapeHtml(detail)}</div></article>`).join("");

  renderTimeline(run);
  renderViolations(run);
  $("#provenance").textContent = `schema ${run.schema_version} · mapping ${run.mapping_version}`;
}

function renderTimeline(run) {
  if (!run.timeline.length) {
    $("#timeline-track").innerHTML = '<div class="no-violations">No observed events are available for this run.</div>';
    $("#event-inspector").innerHTML = '<div class="inspector-empty">The dashboard does not infer behavior from an empty event stream.</div>';
    return;
  }
  if (state.selectedEvent >= run.timeline.length) state.selectedEvent = 0;
  $("#timeline-track").innerHTML = run.timeline.map((event, index) => {
    const technique = event.techniques[0];
    return `<button class="event-card ${event.roe_status} ${index === state.selectedEvent ? "active" : ""}" data-event-index="${index}" type="button">
      <span class="step-label">STEP ${String(event.step).padStart(2, "0")}</span>
      <i class="event-node"></i>
      ${event.goal_reached ? '<span class="goal-marker">◆ GOAL</span>' : ""}
      <div class="ttp">${technique ? `${escapeHtml(technique.technique_id)} · ${escapeHtml(technique.status.toUpperCase())}` : "UNMAPPED OBSERVATION"}</div>
      <h3>${escapeHtml(event.summary)}</h3>
      <p>${technique ? escapeHtml(technique.name) : `${escapeHtml(event.kind)} event`}</p>
      <div class="event-footer"><span>${escapeHtml(event.source)}</span><span>${escapeHtml(event.roe_status.toUpperCase())}</span></div>
    </button>`;
  }).join("");
  document.querySelectorAll(".event-card").forEach((card) => card.addEventListener("click", () => {
    state.selectedEvent = Number(card.dataset.eventIndex);
    renderTimeline(run);
  }));
  renderInspector(run.timeline[state.selectedEvent]);
}

function renderInspector(event) {
  const technique = event.techniques[0];
  const violation = event.violations[0];
  $("#event-inspector").innerHTML = `
    <div class="inspector-heading"><i class="dot ${escapeHtml(event.roe_status)}"></i><h3>Observed event ${event.step}</h3></div>
    <div class="inspector-section"><label>Action</label><p class="code-value">${escapeHtml(event.summary)}</p></div>
    <div class="inspector-section"><label>ATT&amp;CK attribution</label><p>${technique ? `<span class="code-value">${escapeHtml(technique.technique_id)}</span><br>${escapeHtml(technique.name)}<br><small>${escapeHtml(technique.confidence)} confidence · ${escapeHtml(technique.status)}</small>` : "Out of mapping scope"}</p></div>
    <div class="inspector-section"><label>RoE assessment</label><p>${violation ? `<span class="outcome-unsafe">${escapeHtml(titleCase(violation.reason))}</span><br>Rule: <span class="code-value">${escapeHtml(violation.rule ?? "not specified")}</span>` : event.roe_status === "not_evaluated" ? "Not evaluated" : "No violation linked to this event"}</p></div>
    <div class="inspector-section"><label>Evidence reference</label><p class="code-value">event_index=${event.event_index}<br>${escapeHtml(event.timestamp ?? "timestamp unknown")}</p></div>`;
}

function renderViolations(run) {
  $("#violation-count").textContent = `${run.roe.violations.length} FINDINGS`;
  if (!run.roe.violations.length) {
    $("#violation-list").innerHTML = '<div class="no-violations">No RoE violations were recorded. This does not imply unobserved behavior was compliant.</div>';
    return;
  }
  $("#violation-list").innerHTML = run.roe.violations.map((violation) => {
    const event = violation.event_index == null ? null : run.timeline[violation.event_index];
    const technique = event?.techniques?.[0];
    return `<article class="violation-item">
      <div class="step">STEP ${violation.step ?? "?"}</div>
      <div><strong>${escapeHtml(titleCase(violation.dimension))}</strong><p>${escapeHtml(violation.rule ?? "No explicit rule identifier")}</p></div>
      <div><strong>${escapeHtml(titleCase(violation.reason))}</strong><p>${technique ? `${escapeHtml(technique.technique_id)} · ${escapeHtml(technique.name)}` : "No ATT&CK attribution"}</p></div>
      <span class="severity">${escapeHtml(violation.severity)}</span>
    </article>`;
  }).join("");
}

function renderComparison() {
  const summary = state.snapshot.summary;
  $("#aggregate-note").innerHTML = `VALID DENOMINATOR <strong>${summary.valid_runs}</strong> / TOTAL ${summary.total_runs}<br>VIOLATION ${summary.violation_run_count}/${summary.valid_runs} (${percent(summary.violation_rate)})`;
  $("#comparison-body").innerHTML = state.snapshot.runs.map((run) => `
    <tr data-run-id="${escapeHtml(run.run_id)}">
      <td class="code-value">${escapeHtml(run.run_id)}</td><td>${escapeHtml(run.model)}</td>
      <td>${escapeHtml(titleCase(run.pressure_family))} · L${run.pressure_level ?? "?"}</td>
      <td>${run.goal.success == null ? "Unknown" : run.goal.success ? "Achieved" : "Failed"}</td>
      <td class="${run.roe.compliant === false ? "outcome-unsafe" : run.roe.compliant === true ? "outcome-safe" : "outcome-unknown"}">${run.roe.compliant == null ? "Unknown" : run.roe.compliant ? "Compliant" : "Violated"}</td>
      <td class="${run.classification.includes("unsafe") ? "outcome-unsafe" : run.classification === "unknown" ? "outcome-unknown" : "outcome-safe"}">${escapeHtml(titleCase(run.classification))}</td>
      <td>${escapeHtml(titleCase(run.validity))}</td>
    </tr>`).join("");
  document.querySelectorAll("#comparison-body tr").forEach((row) => row.addEventListener("click", () => selectRun(row.dataset.runId)));
}

function renderMatrix() {
  const grouped = Object.groupBy ? Object.groupBy(state.snapshot.techniques, (item) => item.tactic) : state.snapshot.techniques.reduce((result, item) => {
    (result[item.tactic] ||= []).push(item); return result;
  }, {});
  const max = Math.max(1, ...state.snapshot.techniques.map((item) => item[state.matrixMetric]));
  const color = state.matrixMetric === "violating" ? "#ff5f67" : state.matrixMetric === "attempted" ? "#ffbd5a" : "#52e1d2";
  $("#matrix").innerHTML = Object.entries(grouped).map(([tactic, techniques]) => `
    <section class="tactic-column"><h3>${escapeHtml(tactic)}</h3>${techniques.map((technique) => {
      const value = technique[state.matrixMetric];
      const strength = `${12 + Math.round((value / max) * 43)}%`;
      return `<article class="technique-cell" style="--cell-color:${color};--cell-strength:${strength}"><strong>${escapeHtml(technique.technique_id)}</strong><span>${escapeHtml(technique.name)}</span><b>${value}</b><span>${escapeHtml(titleCase(state.matrixMetric))} events</span></article>`;
    }).join("")}</section>`).join("") || '<div class="no-violations">No ATT&amp;CK techniques were mapped from observed telemetry.</div>';
}

document.querySelectorAll(".metric-toggle button").forEach((button) => button.addEventListener("click", () => {
  state.matrixMetric = button.dataset.metric;
  document.querySelectorAll(".metric-toggle button").forEach((item) => item.classList.toggle("active", item === button));
  renderMatrix();
}));
$("#refresh-button").addEventListener("click", loadDashboard);
loadDashboard();
