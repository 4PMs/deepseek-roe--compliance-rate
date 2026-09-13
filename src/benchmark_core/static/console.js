const state = { options: null, scenario: null, condition: null, generatedRunId: null, jobId: null, poll: null };
const $ = (selector) => document.querySelector(selector);
const form = $("#run-form");

function escapeLabel(value) {
  return String(value || "unspecified").replaceAll("_", " ");
}

function selectedScenario() {
  return state.options?.scenarios.find((item) => item.id === state.scenario);
}

function renderScenarios() {
  const container = $("#scenario-options");
  container.replaceChildren();
  state.options.scenarios.forEach((scenario, index) => {
    const label = document.createElement("label");
    label.className = "option-card";
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "scenario";
    input.value = scenario.id;
    input.checked = index === 0;
    const title = document.createElement("strong");
    title.textContent = scenario.id;
    const detail = document.createElement("small");
    detail.textContent = `${escapeLabel(scenario.role)} · ${escapeLabel(scenario.eligibility)}`;
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = scenario.status;
    label.append(input, title, detail, tag);
    container.append(label);
    input.addEventListener("change", () => {
      state.scenario = scenario.id;
      if (!$("#run-id").value.trim()) state.generatedRunId = null;
      updateScenario();
    });
  });
  state.scenario = state.options.scenarios[0]?.id || null;
  updateScenario();
}

function updateScenario() {
  document.querySelectorAll('#scenario-options .option-card').forEach((card) => {
    card.classList.toggle("selected", card.querySelector("input").checked);
  });
  const scenario = selectedScenario();
  const warning = $("#scenario-warning");
  const incomplete = scenario && (scenario.eligibility !== "eligible" || !scenario.experiment_enabled);
  warning.classList.toggle("hidden", !incomplete);
  if (incomplete) {
    const requirements = [...scenario.remaining_requirements, ...scenario.blockers];
    warning.textContent = `현재 ${scenario.eligibility} 상태입니다. ${requirements.length ? `남은 항목: ${requirements.join(", ")}` : "진단 실행만 허용됩니다."}`;
  }
  $("#max-steps").placeholder = scenario?.defaults.max_steps ?? "scenario 기본값";
  $("#timeout").placeholder = scenario?.defaults.timeout ?? "scenario 기본값";
  renderConditions();
  updateRunButton();
  requestPreview();
}

function renderConditions() {
  const container = $("#condition-options");
  container.replaceChildren();
  const conditions = selectedScenario()?.conditions || [];
  conditions.forEach((condition, index) => {
    const label = document.createElement("label");
    label.className = "option-card";
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "condition";
    input.value = condition.id;
    input.checked = condition.id === "neutral" || (index === 0 && !conditions.some((x) => x.id === "neutral"));
    const title = document.createElement("strong");
    title.textContent = condition.id;
    const group = document.createElement("small");
    group.textContent = escapeLabel(condition.group);
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = escapeLabel(condition.requested_operation);
    label.append(input, title, group, tag);
    container.append(label);
    input.addEventListener("change", () => {
      state.condition = condition.id;
      updateCondition();
    });
  });
  state.condition = conditions.find((item) => item.id === "neutral")?.id || conditions[0]?.id || null;
  updateCondition();
}

function updateCondition() {
  document.querySelectorAll('#condition-options .option-card').forEach((card) => {
    card.classList.toggle("selected", card.querySelector("input").checked);
  });
  const condition = selectedScenario()?.conditions.find((item) => item.id === state.condition);
  const detail = $("#condition-detail");
  detail.classList.toggle("hidden", !condition);
  if (condition) {
    detail.querySelector(".detail-meta").textContent = `${condition.delivery_phase} · ${condition.requested_operation}${condition.target_resource ? ` · ${condition.target_resource}` : ""}`;
    detail.querySelector("p").textContent = condition.instruction;
  }
  requestPreview();
}

function payload() {
  const data = new FormData(form);
  const result = {
    scenario: state.scenario,
    condition: state.condition,
    provider: data.get("provider"),
    model: data.get("model"),
    reset_target: $("#reset-target").checked,
    enforce_policy: $("#enforce-policy").checked,
    acknowledge_incomplete: $("#acknowledge-incomplete").checked,
  };
  ["run_id", "upstream", "temperature", "seed", "repetition", "max_steps", "timeout", "model_version", "agent_version"].forEach((name) => {
    const value = data.get(name);
    if (value !== null && String(value).trim() !== "") result[name] = String(value).trim();
  });
  if (!result.run_id && state.generatedRunId) result.run_id = state.generatedRunId;
  return result;
}

let previewTimer;
function requestPreview() {
  clearTimeout(previewTimer);
  if (!state.scenario || !state.condition) return;
  previewTimer = setTimeout(async () => {
    try {
      const response = await fetch("/api/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload()) });
      const body = await response.json();
      if (response.ok && !$("#run-id").value.trim()) state.generatedRunId = body.run_id;
      $("#command-preview").textContent = response.ok ? body.command : body.error;
      $("#preview-state").textContent = response.ok ? "ready" : "invalid";
    } catch (error) {
      $("#command-preview").textContent = `미리보기 실패: ${error.message}`;
    }
  }, 180);
}

function updateRunButton() {
  const scenario = selectedScenario();
  const incomplete = scenario && (scenario.eligibility !== "eligible" || !scenario.experiment_enabled);
  const acknowledged = $("#acknowledge-incomplete").checked;
  $("#run-button").disabled = !state.scenario || !state.condition || (incomplete && !acknowledged) || state.jobId !== null;
}

function setError(message = "") {
  const node = $("#form-error");
  node.textContent = message;
  node.classList.toggle("hidden", !message);
}

function setJobStatus(status) {
  const badge = $("#job-status");
  badge.textContent = status.toUpperCase();
  badge.className = `status ${status}`;
}

function renderResult(result) {
  const section = $("#result-summary");
  section.classList.toggle("hidden", !result);
  window.TrajectoryMap?.renderTrajectory(result?.trajectory);
  if (!result) return;
  $("#result-status").textContent = result.status ?? "—";
  $("#result-goal").textContent = result.goal?.success === true ? "achieved" : result.goal?.success === false ? "not achieved" : "—";
  $("#result-roe").textContent = result.validity?.valid === false ? "not evaluated" : result.roe?.compliant === true ? "compliant" : result.roe?.compliant === false ? "violation" : "—";
  $("#result-termination").textContent = result.termination?.reason ?? "—";
}

async function pollJob() {
  if (!state.jobId) return;
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(state.jobId)}`);
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || "상태 조회 실패");
    setJobStatus(job.status);
    $("#active-run-id").textContent = job.run_id || "—";
    $("#artifact-path").textContent = job.artifact_path || "—";
    const log = $("#live-log");
    log.textContent = job.log || "프로세스가 시작되는 중입니다…";
    log.scrollTop = log.scrollHeight;
    renderResult(job.result);
    if (["completed", "failed"].includes(job.status)) {
      clearInterval(state.poll);
      state.poll = null;
      state.jobId = null;
      updateRunButton();
      if (!$("#run-id").value.trim()) requestPreview();
    }
  } catch (error) {
    setError(error.message);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setError();
  $("#live-log").textContent = "실행 요청을 전송하는 중입니다…";
  renderResult(null);
  try {
    const response = await fetch("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload()) });
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || "실행 요청 실패");
    state.jobId = job.job_id;
    state.generatedRunId = null;
    $("#active-run-id").textContent = job.run_id;
    setJobStatus(job.status);
    updateRunButton();
    await pollJob();
    if (state.jobId) state.poll = setInterval(pollJob, 1000);
  } catch (error) {
    setError(error.message);
    setJobStatus("failed");
    state.jobId = null;
    updateRunButton();
    if (!$("#run-id").value.trim()) {
      state.generatedRunId = null;
      requestPreview();
    }
  }
});

form.addEventListener("input", (event) => {
  if (event.target.id === "run-id") state.generatedRunId = null;
  updateRunButton();
  requestPreview();
});
$("#acknowledge-incomplete").addEventListener("change", updateRunButton);

async function init() {
  try {
    const response = await fetch("/api/options");
    const options = await response.json();
    if (!response.ok) throw new Error(options.error || "옵션 조회 실패");
    state.options = options;
    $("#model").value = options.defaults.model;
    renderScenarios();
  } catch (error) {
    setError(`콘솔 초기화 실패: ${error.message}`);
    $("#scenario-options").textContent = "옵션을 불러오지 못했습니다.";
  }
}

init();
