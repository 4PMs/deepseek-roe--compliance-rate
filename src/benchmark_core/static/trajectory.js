(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.TrajectoryMap = api;
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const NODE_W = 184;
  const NODE_H = 76;
  const X_GAP = 54;
  const Y_GAP = 34;
  const PAD = 24;
  const MAX_NODES = 64;
  const STAGES = new Set([
    "start", "proposal", "dispatch", "server_acceptance", "impact", "termination",
  ]);

  function normalizeTrajectory(value, roe) {
    const source = value && typeof value === "object" ? value : {};
    const roeSource = roe && typeof roe === "object" ? roe : {};
    const seen = new Set();
    const nodes = [];
    for (const item of Array.isArray(source.nodes) ? source.nodes : []) {
      if (!item || typeof item !== "object" || nodes.length >= MAX_NODES) continue;
      const id = String(item.id || "").slice(0, 160);
      if (!id || seen.has(id)) continue;
      seen.add(id);
      nodes.push({
        id,
        type: STAGES.has(item.type) ? item.type : "impact",
        action_id: item.action_id ? String(item.action_id).slice(0, 100) : null,
        label: String(item.label || item.type || id).slice(0, 120),
        status: String(item.status || "missing").slice(0, 48),
        evidence: item.evidence && typeof item.evidence === "object" ? item.evidence : null,
      });
    }
    nodes.sort((left, right) => left.id.localeCompare(right.id));
    const edges = [];
    const edgeKeys = new Set();
    for (const item of Array.isArray(source.edges) ? source.edges : []) {
      if (!item || typeof item !== "object") continue;
      const from = String(item.from || "");
      const to = String(item.to || "");
      const key = `${from}\u0000${to}`;
      if (!seen.has(from) || !seen.has(to) || from === to || edgeKeys.has(key)) continue;
      edgeKeys.add(key);
      edges.push({
        from,
        to,
        relation: item.relation ? String(item.relation).slice(0, 64) : "unspecified",
        basis: item.basis ? String(item.basis).slice(0, 64) : "inferred",
        confidence: Number.isFinite(item.confidence) ? item.confidence : 0,
      });
    }
    edges.sort((left, right) =>
      left.from.localeCompare(right.from) || left.to.localeCompare(right.to));
    return {
      schema_version: String(source.schema_version || "unknown"),
      nodes,
      edges,
      actions: Array.isArray(source.actions) ? source.actions.slice(0, MAX_NODES) : [],
      violations: Array.isArray(roeSource.violations) ? roeSource.violations : [],
      roe_categories: roeSource.categories && typeof roeSource.categories === "object"
        ? roeSource.categories : {},
      goal: source.goal && typeof source.goal === "object" ? source.goal : {},
      termination: source.termination && typeof source.termination === "object"
        ? source.termination : {},
    };
  }

  function layoutDirectedGraph(inputNodes, inputEdges) {
    const nodes = [...inputNodes].sort((left, right) => left.id.localeCompare(right.id));
    const edges = [...inputEdges].sort((left, right) =>
      left.from.localeCompare(right.from) || left.to.localeCompare(right.to));
    const ids = new Set(nodes.map((node) => node.id));
    const indegree = Object.fromEntries(nodes.map((node) => [node.id, 0]));
    const outgoing = Object.fromEntries(nodes.map((node) => [node.id, []]));
    const rank = Object.fromEntries(nodes.map((node) => [node.id, 0]));
    edges.forEach((edge) => {
      if (!ids.has(edge.from) || !ids.has(edge.to)) return;
      indegree[edge.to] += 1;
      outgoing[edge.from].push(edge.to);
    });
    Object.values(outgoing).forEach((targets) => targets.sort());
    const queue = nodes.filter((node) => indegree[node.id] === 0).map((node) => node.id).sort();
    const visited = new Set();
    while (queue.length) {
      const id = queue.shift();
      visited.add(id);
      outgoing[id].forEach((target) => {
        rank[target] = Math.max(rank[target], rank[id] + 1);
        indegree[target] -= 1;
        if (indegree[target] === 0) {
          queue.push(target);
          queue.sort();
        }
      });
    }
    let fallbackRank = Math.max(0, ...Object.values(rank)) + 1;
    nodes.filter((node) => !visited.has(node.id)).forEach((node) => {
      rank[node.id] = fallbackRank;
      fallbackRank += 1;
    });
    const layers = new Map();
    nodes.forEach((node) => {
      if (!layers.has(rank[node.id])) layers.set(rank[node.id], []);
      layers.get(rank[node.id]).push(node);
    });
    layers.forEach((items) => items.sort((left, right) => left.id.localeCompare(right.id)));
    const positioned = nodes.map((node) => {
      const layer = layers.get(rank[node.id]);
      const index = layer.findIndex((item) => item.id === node.id);
      return {
        ...node,
        rank: rank[node.id],
        x: PAD + rank[node.id] * (NODE_W + X_GAP),
        y: PAD + index * (NODE_H + Y_GAP),
      };
    });
    const maxRank = positioned.length ? Math.max(...positioned.map((node) => node.rank)) : 0;
    const maxLayer = positioned.length
      ? Math.max(...[...layers.values()].map((items) => items.length)) : 1;
    return {
      nodes: positioned,
      edges,
      width: PAD * 2 + (maxRank + 1) * NODE_W + maxRank * X_GAP,
      height: PAD * 2 + maxLayer * NODE_H + Math.max(0, maxLayer - 1) * Y_GAP,
    };
  }

  function svgElement(name, attributes = {}) {
    const node = document.createElementNS(SVG_NS, name);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  }

  function violationActionId(violation) {
    const key = violation && violation.event_key;
    return Array.isArray(key) && key.length >= 3 ? String(key[2]) : null;
  }

  function stagePayload(trajectory, node) {
    if (node.type === "start") return {stage: "start", schema_version: trajectory.schema_version};
    if (node.type === "termination") return {stage: "termination", ...trajectory.termination};
    const action = trajectory.actions.find((item) =>
      item && String(item.action_id) === String(node.action_id));
    const key = node.type === "server_acceptance" ? "server_acceptance" : node.type;
    const stage = action && action[key] ? action[key] : {};
    const request = action && action.proposal ? {
      method: action.proposal.method || null,
      path: action.proposal.path || null,
      operation: action.proposal.operation || null,
    } : null;
    const records = trajectory.violations.filter((record) =>
      violationActionId(record) === String(node.action_id));
    const violations = records.filter((record) => record.severity !== "unclassified");
    const unclassified = records.filter((record) => record.severity === "unclassified");
    const categories = Array.isArray(stage.roe_categories) ? stage.roe_categories : [];
    const categoryDetails = categories.map((code) => ({
      code,
      name: trajectory.roe_categories[code]?.name
        || records.find((item) =>
          item.roe_category === code || item.roe_categories?.includes(code))?.roe_category_name
        || null,
    }));
    return {
      stage: node.type,
      action_id: node.action_id,
      request,
      classification: stage.classification || node.status,
      roe_categories: categories,
      roe_category_details: categoryDetails,
      violations,
      unclassified,
      gateway_event: node.type === "dispatch" ? (stage.evidence || node.evidence) : undefined,
      evidence: node.type === "dispatch" ? undefined : (Object.keys(stage).length ? stage : node.evidence),
    };
  }

  async function enterGraphFullscreen(element, documentLike) {
    if (!element || !documentLike || documentLike.fullscreenElement === element) return false;
    if (typeof element.requestFullscreen === "function") {
      try {
        await element.requestFullscreen();
        return true;
      } catch (_error) {
        // Fall through to the CSS overlay when the browser rejects native fullscreen.
      }
    }
    element.classList.add("trajectory-fullscreen-fallback");
    return true;
  }

  async function exitGraphFullscreen(element, documentLike) {
    if (!element || !documentLike) return false;
    if (documentLike.fullscreenElement === element && typeof documentLike.exitFullscreen === "function") {
      await documentLike.exitFullscreen();
    }
    element.classList.remove("trajectory-fullscreen-fallback");
    return true;
  }

  function bindGraphFullscreen(section, viewport, exitButton) {
    if (!section || !viewport || section.dataset.fullscreenBound === "true") return;
    section.dataset.fullscreenBound = "true";
    const open = () => enterGraphFullscreen(section, document);
    viewport.addEventListener("click", open);
    viewport.addEventListener("keydown", (event) => {
      if (event.target === viewport && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        open();
      }
    });
    exitButton?.addEventListener("click", (event) => {
      event.stopPropagation();
      exitGraphFullscreen(section, document);
    });
  }

  function renderTrajectory(value, roe) {
    const section = document.querySelector("#trajectory-section");
    const viewport = document.querySelector(".trajectory-viewport");
    const exitButton = document.querySelector("#trajectory-fullscreen-exit");
    const svg = document.querySelector("#trajectory-map");
    const edgeLayer = document.querySelector("#trajectory-edges");
    const nodeLayer = document.querySelector("#trajectory-nodes");
    const empty = document.querySelector("#trajectory-empty");
    const count = document.querySelector("#trajectory-count");
    const list = document.querySelector("#trajectory-accessible-list");
    const details = document.querySelector("#trajectory-details");
    const detailsJson = document.querySelector("#trajectory-details-json");
    if (!section || !svg || !edgeLayer || !nodeLayer) return;
    bindGraphFullscreen(section, viewport, exitButton);

    const trajectory = normalizeTrajectory(value, roe);
    edgeLayer.replaceChildren();
    nodeLayer.replaceChildren();
    if (list) list.replaceChildren();
    if (details) details.classList.add("hidden");
    if (detailsJson) detailsJson.textContent = "";
    section.classList.toggle("hidden", !value);
    empty?.classList.toggle("hidden", trajectory.nodes.length > 0);
    if (count) count.textContent = `${trajectory.actions.length} actions · ${trajectory.nodes.length} stages`;
    if (!value || !trajectory.nodes.length) return;

    const layout = layoutDirectedGraph(trajectory.nodes, trajectory.edges);
    svg.setAttribute("viewBox", `0 0 ${layout.width} ${layout.height}`);
    svg.setAttribute("width", String(layout.width));
    svg.setAttribute("height", String(layout.height));
    const byId = Object.fromEntries(layout.nodes.map((node) => [node.id, node]));

    layout.edges.forEach((edge) => {
      const source = byId[edge.from];
      const target = byId[edge.to];
      if (!source || !target) return;
      const startX = source.x + NODE_W;
      const startY = source.y + NODE_H / 2;
      const endX = target.x;
      const endY = target.y + NODE_H / 2;
      const midX = (startX + endX) / 2;
      const edgeDescription = `${edge.relation}; ${edge.basis}; confidence ${edge.confidence}`;
      const path = svgElement("path", {
        d: `M ${startX} ${startY} C ${midX} ${startY}, ${midX} ${endY}, ${endX} ${endY}`,
        class: "trajectory-edge", "marker-end": "url(#trajectory-arrow)",
        tabindex: "0", role: "img", "aria-label": edgeDescription,
      });
      const title = svgElement("title");
      title.textContent = edgeDescription;
      path.append(title);
      edgeLayer.append(path);
    });

    layout.nodes.forEach((node) => {
      const group = svgElement("g", {
        class: `trajectory-node trajectory-status-${safeClass(node.status)}`,
        transform: `translate(${node.x} ${node.y})`, tabindex: "0", role: "button",
        "aria-label": `${node.type}: ${node.label}; ${node.status}`,
      });
      const title = svgElement("title");
      title.textContent = `${node.type} · ${node.label} · ${node.status}`;
      const rect = svgElement("rect", {width: NODE_W, height: NODE_H, rx: 12});
      const stage = svgElement("text", {x: 14, y: 22, class: "trajectory-stage"});
      stage.textContent = node.type.replaceAll("_", " ").toUpperCase();
      const label = svgElement("text", {x: 14, y: 45, class: "trajectory-label"});
      label.textContent = truncate(node.label, 28);
      const status = svgElement("text", {x: 14, y: 64, class: "trajectory-node-status"});
      status.textContent = truncate(node.status, 26);
      group.append(title, rect, stage, label, status);
      const inspect = () => {
        if (!details || !detailsJson) return;
        details.classList.remove("hidden");
        detailsJson.textContent = JSON.stringify(stagePayload(trajectory, node), null, 2);
      };
      group.addEventListener("click", inspect);
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          inspect();
        }
      });
      nodeLayer.append(group);
      if (list) {
        const item = document.createElement("li");
        item.textContent = `${node.type}: ${node.label}; status ${node.status}`;
        list.append(item);
      }
    });
  }

  function safeClass(value) {
    return String(value || "missing").toLowerCase().replace(/[^a-z0-9_-]/g, "-");
  }

  function truncate(value, limit) {
    const text = String(value || "");
    return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
  }

  return {
    normalizeTrajectory,
    layoutDirectedGraph,
    renderTrajectory,
    stagePayload,
    enterGraphFullscreen,
    exitGraphFullscreen,
  };
});
