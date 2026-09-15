(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.TrajectoryMap = api;
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";

  // ?�?� layoutDirectedGraph constants (unchanged ??tests depend on these) ?�?�?�?�?�?�
  const NODE_W = 184;
  const NODE_H = 76;
  const X_GAP  = 54;
  const Y_GAP  = 34;
  const PAD    = 24;

  // ?�?� Compact stacked-row layout constants ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
  // 3 fixed columns: PROPOSAL | DISPATCH | ACCEPTANCE/IMPACT
  // SVGW = SPAD + NW + NXG + NW + NXG + NW + SPAD  = 12+134+12+134+12+134+12 = 450
  const NW   = 134;  // node width
  const NH   = 52;   // node height
  const NXG  = 12;   // horizontal gap between columns
  const NIY  = 8;    // gap between dispatch row and impact row within one action
  const NOY  = 40;   // vertical gap between action rows (inter-action connector runs here)
  const SPAD = 12;   // SVG outer padding
  const CW   = 88;   // context node (START/END) width
  const CH   = 20;   // context node height
  const CGAP = 6;    // gap between START and first action row

  const COL  = [SPAD, SPAD + NW + NXG, SPAD + 2 * (NW + NXG)];
  const SVGW = SPAD + 3 * NW + 2 * NXG + SPAD; // 450

  const MAX_NODES = 64;
  const STAGES = new Set([
    "start", "proposal", "dispatch", "server_acceptance", "impact", "termination",
  ]);

  // ?�?� normalizeTrajectory (unchanged ??tests verify) ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
  function normalizeTrajectory(value, roe) {
    const source    = value && typeof value === "object" ? value : {};
    const roeSource = roe   && typeof roe   === "object" ? roe   : {};
    const seen = new Set(), nodes = [];
    for (const item of Array.isArray(source.nodes) ? source.nodes : []) {
      if (!item || typeof item !== "object" || nodes.length >= MAX_NODES) continue;
      const id = String(item.id || "").slice(0, 160);
      if (!id || seen.has(id)) continue;
      seen.add(id);
      nodes.push({
        id,
        type:      STAGES.has(item.type) ? item.type : "impact",
        action_id: item.action_id ? String(item.action_id).slice(0, 100) : null,
        label:     String(item.label || item.type || id).slice(0, 120),
        status:    String(item.status || "missing").slice(0, 48),
        evidence:  item.evidence && typeof item.evidence === "object" ? item.evidence : null,
      });
    }
    nodes.sort((a, b) => a.id.localeCompare(b.id));
    const edges = [], edgeKeys = new Set();
    for (const item of Array.isArray(source.edges) ? source.edges : []) {
      if (!item || typeof item !== "object") continue;
      const from = String(item.from || ""), to = String(item.to || "");
      const key  = `${from}\u0000${to}`;
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
    edges.sort((a, b) => a.from.localeCompare(b.from) || a.to.localeCompare(b.to));
    return {
      schema_version: String(source.schema_version || "unknown"),
      nodes, edges,
      actions:        Array.isArray(source.actions) ? source.actions.slice(0, MAX_NODES) : [],
      violations:     Array.isArray(roeSource.violations) ? roeSource.violations : [],
      roe_categories: roeSource.categories && typeof roeSource.categories === "object"
                        ? roeSource.categories : {},
      goal:        source.goal        && typeof source.goal        === "object" ? source.goal        : {},
      termination: source.termination && typeof source.termination === "object" ? source.termination : {},
    };
  }

  // ?�?� layoutDirectedGraph (unchanged ??tests verify) ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
  function layoutDirectedGraph(inputNodes, inputEdges) {
    const nodes = [...inputNodes].sort((a, b) => a.id.localeCompare(b.id));
    const edges = [...inputEdges].sort((a, b) =>
      a.from.localeCompare(b.from) || a.to.localeCompare(b.to));
    const ids      = new Set(nodes.map(n => n.id));
    const indegree = Object.fromEntries(nodes.map(n => [n.id, 0]));
    const outgoing = Object.fromEntries(nodes.map(n => [n.id, []]));
    const rank     = Object.fromEntries(nodes.map(n => [n.id, 0]));
    edges.forEach(e => {
      if (!ids.has(e.from) || !ids.has(e.to)) return;
      indegree[e.to] += 1;
      outgoing[e.from].push(e.to);
    });
    Object.values(outgoing).forEach(t => t.sort());
    const queue = nodes.filter(n => indegree[n.id] === 0).map(n => n.id).sort();
    const visited = new Set();
    while (queue.length) {
      const id = queue.shift();
      visited.add(id);
      outgoing[id].forEach(tgt => {
        rank[tgt] = Math.max(rank[tgt], rank[id] + 1);
        if (--indegree[tgt] === 0) { queue.push(tgt); queue.sort(); }
      });
    }
    let fb = Math.max(0, ...Object.values(rank)) + 1;
    nodes.filter(n => !visited.has(n.id)).forEach(n => { rank[n.id] = fb++; });
    const layers = new Map();
    nodes.forEach(n => {
      if (!layers.has(rank[n.id])) layers.set(rank[n.id], []);
      layers.get(rank[n.id]).push(n);
    });
    layers.forEach(items => items.sort((a, b) => a.id.localeCompare(b.id)));
    const positioned = nodes.map(n => {
      const layer = layers.get(rank[n.id]);
      const idx   = layer.findIndex(item => item.id === n.id);
      return { ...n, rank: rank[n.id],
        x: PAD + rank[n.id] * (NODE_W + X_GAP),
        y: PAD + idx * (NODE_H + Y_GAP) };
    });
    const maxRank  = positioned.length ? Math.max(...positioned.map(n => n.rank)) : 0;
    const maxLayer = positioned.length
      ? Math.max(...[...layers.values()].map(items => items.length)) : 1;
    return {
      nodes: positioned, edges,
      width:  PAD * 2 + (maxRank + 1) * NODE_W + maxRank  * X_GAP,
      height: PAD * 2 + maxLayer * NODE_H + Math.max(0, maxLayer - 1) * Y_GAP,
    };
  }

  // ?�?� DOM helpers ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
  function svgElement(name, attrs = {}) {
    const el = document.createElementNS(SVG_NS, name);
    Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, String(v)));
    return el;
  }

  function safeClass(v) {
    return String(v || "missing").toLowerCase().replace(/[^a-z0-9_-]/g, "-");
  }

  function truncate(v, limit) {
    const t = String(v || "");
    return t.length > limit ? `${t.slice(0, limit - 1)}\u2026` : t;
  }

  // ?�?� stagePayload (unchanged ??tests verify) ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
  function violationActionId(violation) {
    const key = violation && violation.event_key;
    return Array.isArray(key) && key.length >= 3 ? String(key[2]) : null;
  }

  function stagePayload(trajectory, node) {
    if (node.type === "start")       return {stage: "start", schema_version: trajectory.schema_version};
    if (node.type === "termination") return {stage: "termination", ...trajectory.termination};
    const action = trajectory.actions.find(
      item => item && String(item.action_id) === String(node.action_id));
    const key   = node.type === "server_acceptance" ? "server_acceptance" : node.type;
    const stage = action && action[key] ? action[key] : {};
    const request = action && action.proposal ? {
      method:    action.proposal.method    || null,
      path:      action.proposal.path      || null,
      operation: action.proposal.operation || null,
    } : null;
    const records      = trajectory.violations.filter(r => violationActionId(r) === String(node.action_id));
    const violations   = records.filter(r => r.severity !== "unclassified");
    const unclassified = records.filter(r => r.severity === "unclassified");
    const categories   = Array.isArray(stage.roe_categories) ? stage.roe_categories : [];
    const categoryDetails = categories.map(code => ({
      code,
      name: trajectory.roe_categories[code]?.name
        || records.find(item =>
          item.roe_category === code || item.roe_categories?.includes(code))?.roe_category_name
        || null,
    }));
    return {
      stage: node.type, action_id: node.action_id, request,
      classification: stage.classification || node.status,
      roe_categories: categories, roe_category_details: categoryDetails,
      violations, unclassified,
      gateway_event: node.type === "dispatch" ? (stage.evidence || node.evidence) : undefined,
      evidence: node.type === "dispatch"
        ? undefined
        : (Object.keys(stage).length ? stage : node.evidence),
    };
  }

  // ?�?� Fullscreen helpers (unchanged ??tests verify) ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
  async function enterGraphFullscreen(element, documentLike) {
    if (!element || !documentLike || documentLike.fullscreenElement === element) return false;
    if (typeof element.requestFullscreen === "function") {
      try { await element.requestFullscreen(); return true; } catch (_e) { /* fall through */ }
    }
    element.classList.add("trajectory-fullscreen-fallback");
    return true;
  }

  async function exitGraphFullscreen(element, documentLike) {
    if (!element || !documentLike) return false;
    if (documentLike.fullscreenElement === element
        && typeof documentLike.exitFullscreen === "function") {
      await documentLike.exitFullscreen();
    }
    element.classList.remove("trajectory-fullscreen-fallback");
    return true;
  }

  function bindGraphFullscreen(section, viewport, exitButton, expandButton) {
    if (!section || section.dataset.fullscreenBound === "true") return;
    section.dataset.fullscreenBound = "true";
    const open  = () => enterGraphFullscreen(section, document);
    const close = () => exitGraphFullscreen(section, document);
    if (viewport) {
      viewport.addEventListener("click", open);
      viewport.addEventListener("keydown", ev => {
        if (ev.target === viewport && (ev.key === "Enter" || ev.key === " ")) {
          ev.preventDefault(); open();
        }
      });
    }
    expandButton?.addEventListener("click", ev => { ev.stopPropagation(); open(); });
    exitButton?.addEventListener("click",   ev => { ev.stopPropagation(); close(); });
    document.addEventListener("keydown", ev => {
      if (ev.key === "Escape" && section.classList.contains("trajectory-fullscreen-fallback")) close();
    });
  }

  // ?�?� Action metadata helpers ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
  function groupNodesByAction(trajectory) {
    const groups = new Map();
    for (const n of trajectory.nodes) {
      if (n.type === "start" || n.type === "termination") continue;
      const aid = n.action_id || "__unknown__";
      if (!groups.has(aid)) groups.set(aid, {nodes: [], edges: []});
      groups.get(aid).nodes.push(n);
    }
    const n2a = new Map(
      trajectory.nodes.filter(n => n.action_id).map(n => [n.id, n.action_id])
    );
    for (const e of trajectory.edges) {
      const fa = n2a.get(e.from), ta = n2a.get(e.to);
      if (fa && fa === ta && groups.has(fa)) groups.get(fa).edges.push(e);
    }
    return groups;
  }

  function selectDefaultAction(trajectory) {
    const vAids = new Set(
      trajectory.nodes.filter(n => n.status === "violation")
        .map(n => n.action_id).filter(Boolean)
    );
    const first = trajectory.actions.find(a => vAids.has(String(a.action_id)));
    if (first) return String(first.action_id);
    const fb = trajectory.actions[0];
    return fb ? String(fb.action_id) : null;
  }

  function actionOverallStatus(trajectory, actionId) {
    const ss = trajectory.nodes.filter(n => n.action_id === actionId).map(n => n.status);
    for (const p of ["violation", "unclassified", "not_evaluated", "missing"]) {
      if (ss.includes(p)) return p;
    }
    if (ss.some(s => ["compliant", "accepted", "observed"].includes(s))) return "compliant";
    return ss[0] || "missing";
  }

  function actionInfoText(trajectory, actionId) {
    const action = trajectory.actions.find(a => String(a.action_id) === actionId);
    const method = action?.proposal?.method || "";
    const path   = action?.proposal?.path   || "";
    const status = actionOverallStatus(trajectory, actionId);
    const cats   = [...new Set(
      trajectory.violations
        .filter(v => violationActionId(v) === actionId)
        .map(v => v.roe_category).filter(Boolean)
    )];
    const parts = [actionId];
    if (method || path) parts.push([method, path].filter(Boolean).join(" "));
    parts.push(status);
    if (cats.length) parts.push(cats.join(" "));
    return parts.join(" \u00b7 ");
  }

  // ?�?� layoutActionTree (kept for backward compatibility) ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
  function layoutActionTree(actionNodes, actionEdges, ctxOptions) {
    const CTX_TW = 104, CTX_TH = 28, TW = 130, TH = 66, TXG = 22, TYG = 34, TPAD = 18;
    const isFirst = ctxOptions?.isFirst ?? true, isLast = ctxOptions?.isLast ?? true;
    const byType = {};
    for (const n of actionNodes) { (byType[n.type] = byType[n.type] || []).push(n); }
    const contentRows = [];
    if (byType.proposal) contentRows.push(byType.proposal);
    if (byType.dispatch) contentRows.push(byType.dispatch);
    const branch = [...(byType.server_acceptance || []), ...(byType.impact || [])];
    if (branch.length) contentRows.push(branch);
    const handled = new Set(["proposal", "dispatch", "server_acceptance", "impact"]);
    for (const [t, ns] of Object.entries(byType)) { if (!handled.has(t)) contentRows.push(ns); }
    const maxCols = contentRows.length ? Math.max(...contentRows.map(r => r.length)) : 0;
    const totW    = TPAD * 2 + Math.max(maxCols * TW + Math.max(0, maxCols - 1) * TXG, CTX_TW, 1);
    const ctxX    = (totW - CTX_TW) / 2;
    const pos = [];
    let cy = TPAD;
    pos.push({ id: "__ctx_top__", type: "__ctx__", isContext: true, label: isFirst ? "START" : "PREV",
      status: "context", action_id: null, x: ctxX, y: cy, _nodeW: CTX_TW, _nodeH: CTX_TH });
    cy += CTX_TH + TYG;
    for (const row of contentRows) {
      const rW = row.length * TW + Math.max(0, row.length - 1) * TXG;
      const rx = (totW - rW) / 2;
      row.forEach((n, ci) => {
        pos.push({ ...n, x: rx + ci * (TW + TXG), y: cy, _nodeW: TW, _nodeH: TH });
      });
      cy += TH + TYG;
    }
    pos.push({ id: "__ctx_bot__", type: "__ctx__", isContext: true, label: isLast ? "END" : "NEXT",
      status: "context", action_id: null, x: ctxX, y: cy, _nodeW: CTX_TW, _nodeH: CTX_TH });
    cy += CTX_TH;
    const byId = Object.fromEntries(pos.map(n => [n.id, n]));
    const t2n = {};
    for (const n of pos) { if (!n.isContext) t2n[n.type] = n; }
    const valid = (actionEdges || []).filter(e => byId[e.from] && byId[e.to]);
    const used  = new Set(valid.map(e => `${e.from}|${e.to}`));
    const synth = [];
    const addE  = (fid, tid) => {
      const k = `${fid}|${tid}`;
      if (!used.has(k) && byId[fid] && byId[tid]) { synth.push({from: fid, to: tid}); used.add(k); }
    };
    const fr = t2n.proposal || t2n.dispatch || pos.find(n => !n.isContext);
    if (fr) addE("__ctx_top__", fr.id);
    if (t2n.proposal && t2n.dispatch)          addE(t2n.proposal.id, t2n.dispatch.id);
    if (t2n.dispatch && t2n.server_acceptance) addE(t2n.dispatch.id, t2n.server_acceptance.id);
    if (t2n.dispatch && t2n.impact)            addE(t2n.dispatch.id, t2n.impact.id);
    const ends = [t2n.server_acceptance, t2n.impact, t2n.dispatch, t2n.proposal]
      .filter(Boolean).slice(0, 2);
    if (!ends.length) { const lr = pos.filter(n => !n.isContext).pop(); if (lr) ends.push(lr); }
    for (const n of ends) addE(n.id, "__ctx_bot__");
    return { nodes: pos, edges: [...valid, ...synth], width: totW, height: cy + TPAD };
  }

  // ?�?� layoutFullTrajectory ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
  /**
   * All actions stacked vertically, 3-column layout.
   * Each edge is tagged with 'edgeType':
   *   'h'      ??horizontal same-row (proposal?�dispatch, dispatch?�acceptance)
   *   'branch' ??dispatch ??impact (one row down, same col[2])
   *   'inter'  ??between action rows (exit?�entry), carries exitBottom/entryTop
   *   'ctx'    ??context node connection
   *
   * Returns { nodes, edges, width, height, actionYMap }
   */
  function layoutFullTrajectory(trajectory, orderedActionIds) {
    const ctxCx    = (SVGW - CW) / 2;
    const ctxNodeX = ctxCx;
    const pos      = [];
    const edges    = [];
    const actionYMap = {};

    let cy = SPAD;

    // ?�?� START ??aligned above the PROPOSAL column (col[0]) for a straight ??arrow ?�?�
    const startId = "__ctx_start__";
    // Center START horizontally over PROPOSAL column: COL[0] + (NW - CW) / 2
    const startX = COL[0] + Math.round((NW - CW) / 2);
    pos.push({ id: startId, type: "__ctx__", isContext: true,
      label: "START", status: "context", action_id: null,
      x: startX, y: cy, _nodeW: CW, _nodeH: CH });
    cy += CH + CGAP;

    // prevExits: array of { id, cx, bottom } for inter-action edges
    // cx = COL[0] + NW/2 = proposal column center
    let prevExits = [{ id: startId, cx: startX + CW / 2, bottom: SPAD + CH }];

    for (const aid of orderedActionIds) {
      const actionNodes = trajectory.nodes.filter(
        n => n.action_id === aid && n.type !== "start" && n.type !== "termination"
      );
      if (!actionNodes.length) continue;

      const byType = {};
      for (const n of actionNodes) { if (!byType[n.type]) byType[n.type] = n; }
      const proposal   = byType.proposal;
      const dispatch   = byType.dispatch;
      const acceptance = byType.server_acceptance;
      const impact     = byType.impact;

      actionYMap[aid] = cy;
      const rowY = cy;

      // Place nodes
      if (proposal)   pos.push({ ...proposal,   x: COL[0], y: rowY,                 _nodeW: NW, _nodeH: NH });
      if (dispatch)   pos.push({ ...dispatch,   x: COL[1], y: rowY,                 _nodeW: NW, _nodeH: NH });
      if (acceptance) pos.push({ ...acceptance, x: COL[2], y: rowY,                 _nodeW: NW, _nodeH: NH });
      if (impact)     pos.push({ ...impact,     x: COL[2], y: rowY + NH + NIY,      _nodeW: NW, _nodeH: NH });

      // ?�?� Intra-action edges ?�?�
      const nodeIds  = new Set(actionNodes.map(n => n.id));
      const usedKeys = new Set();

      // Use real edges where both endpoints are in this action
      for (const e of trajectory.edges) {
        if (nodeIds.has(e.from) && nodeIds.has(e.to)) {
          const k = `${e.from}|${e.to}`;
          if (!usedKeys.has(k)) {
            // Determine type from geometry
            const fnd = actionNodes.find(n => n.id === e.from);
            const tnd = actionNodes.find(n => n.id === e.to);
            if (fnd && tnd) {
              // acceptance or impact in col[2], proposal/dispatch in col[0..1]
              const tgtIsImpact = tnd.type === "impact";
              edges.push({ from: e.from, to: e.to, edgeType: tgtIsImpact ? "branch" : "h" });
              usedKeys.add(k);
            }
          }
        }
      }

      // Synthesise missing canonical edges
      const addIntra = (fn, tn, etype) => {
        if (!fn || !tn) return;
        const k = `${fn.id}|${tn.id}`;
        if (!usedKeys.has(k)) { edges.push({ from: fn.id, to: tn.id, edgeType: etype }); usedKeys.add(k); }
      };
      addIntra(proposal, dispatch, "h");
      addIntra(dispatch, acceptance, "h");
      addIntra(dispatch, impact, "branch");

      // ?�?� Inter-action edge: prevExits ??this action entry ?�?�
      const entryNode = proposal || dispatch;
      if (entryNode) {
        const entryCx  = entryNode.x + NW / 2;
        const entryTop = rowY;
        for (const exit of prevExits) {
          edges.push({
            from: exit.id, to: entryNode.id,
            edgeType: "inter",
            exitCx:    exit.cx,    exitBottom: exit.bottom,
            entryCx:   entryCx,   entryTop,
          });
        }
      }

      // ?�?� Determine exits for next inter-action edge ?�?�
      const actionBottom = rowY + NH + (impact ? NIY + NH : 0);
      const newExits = [];
      if (impact)      newExits.push({ id: impact.id,      cx: COL[2] + NW / 2, bottom: actionBottom });
      if (acceptance && !impact)
                       newExits.push({ id: acceptance.id,  cx: COL[2] + NW / 2, bottom: rowY + NH });
      if (!newExits.length) {
        const fallback = dispatch || proposal;
        if (fallback)  newExits.push({ id: fallback.id, cx: fallback.x + NW / 2, bottom: rowY + NH });
      }
      prevExits = newExits;

      cy = actionBottom + NOY;
    }

    // ?�?� END ??aligned below the exit column of the last action ?�?�
    // Default: below the impact/acceptance column (col[2]); fall back to col[0]
    const endId = "__ctx_end__";
    const lastExitCx = prevExits.length > 0 ? prevExits[0].cx : COL[2] + NW / 2;
    const endX = Math.round(lastExitCx - CW / 2);
    pos.push({ id: endId, type: "__ctx__", isContext: true,
      label: "END", status: "context", action_id: null,
      x: endX, y: cy, _nodeW: CW, _nodeH: CH });

    const endCx  = lastExitCx;
    const endTop = cy;
    for (const exit of prevExits) {
      edges.push({
        from: exit.id, to: endId,
        edgeType: "inter",
        exitCx: exit.cx, exitBottom: exit.bottom,
        entryCx: endCx, entryTop: endTop,
      });
    }

    cy += CH + SPAD;

    return { nodes: pos, edges, width: SVGW, height: cy, actionYMap };
  }

  // ?�?� Edge path builder ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
  /**
   * Build an SVG path string for a single edge using orthogonal routing.
   *
   * edgeType 'h':      horizontal mid-to-mid (same row)
   * edgeType 'branch': dispatch?�impact orthogonal elbow
   * edgeType 'inter':  inter-action elbow (down ??across ??down)
   * edgeType 'ctx':    fallback straight diagonal
   */
  function edgePath(edge, byId) {
    const src = byId[edge.from];
    const tgt = byId[edge.to];
    if (!src || !tgt) return null;
    const sw = src._nodeW || NW, sh = src._nodeH || NH;
    const tw = tgt._nodeW || NW, th = tgt._nodeH || NH;

    if (edge.edgeType === "h") {
      // Right-mid of src ??left-mid of tgt (same y)
      const y = src.y + sh / 2;
      return `M ${src.x + sw} ${y} H ${tgt.x}`;
    }

    if (edge.edgeType === "branch") {
      // dispatch (col[1]) ??impact (col[2], lower y)
      // Path: right-mid of dispatch ??stub right ??down to impact mid-y ??right to impact left-mid
      const sx = src.x + sw, sy = src.y + sh / 2;
      const ex = tgt.x,      ey = tgt.y + th / 2;
      const stub = NXG / 2;  // 6px stub
      return `M ${sx} ${sy} h ${stub} V ${ey} H ${ex}`;
    }

    if (edge.edgeType === "inter") {
      // Orthogonal elbow: from exitCx/exitBottom, down to midpoint, across, up to entryTop
      const x1 = edge.exitCx,   y1 = edge.exitBottom;
      const x2 = edge.entryCx,  y2 = edge.entryTop;
      const mid = y1 + (y2 - y1) / 2;
      if (Math.abs(x1 - x2) < 2) {
        // Same column: straight vertical
        return `M ${x1} ${y1} V ${y2}`;
      }
      return `M ${x1} ${y1} V ${mid} H ${x2} V ${y2}`;
    }

    // Fallback ??straight diagonal
    return `M ${src.x + sw / 2} ${src.y + sh} L ${tgt.x + tw / 2} ${tgt.y}`;
  }

  // ?�?� renderFullGraph ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
  function renderFullGraph(trajectory, domRefs, orderedActionIds, highlightActionId) {
    const { svg, edgeLayer, nodeLayer, accessibleList, details, detailsJson } = domRefs;

    edgeLayer.replaceChildren();
    nodeLayer.replaceChildren();
    if (accessibleList) accessibleList.replaceChildren();

    if (!orderedActionIds.length && !trajectory.nodes.length) return { actionYMap: {} };

    const layout = layoutFullTrajectory(trajectory, orderedActionIds);
    // Set explicit pixel dimensions ??CSS must NOT scale these up
    svg.setAttribute("viewBox", `0 0 ${layout.width} ${layout.height}`);
    svg.setAttribute("width",  String(layout.width));
    svg.setAttribute("height", String(layout.height));

    const byId = Object.fromEntries(layout.nodes.map(n => [n.id, n]));

    // ?�?� Edges ?�?�
    layout.edges.forEach(edge => {
      const d = edgePath(edge, byId);
      if (!d) return;
      const cls = ["trajectory-edge"];
      if (edge.edgeType === "inter") cls.push("trajectory-edge-inter");
      edgeLayer.append(svgElement("path", {
        d, class: cls.join(" "), "marker-end": "url(#trajectory-arrow)",
      }));
    });

    // ?�?� Nodes ?�?�
    layout.nodes.forEach(node => {
      const isCtx = !!node.isContext;
      const nw = node._nodeW, nh = node._nodeH;
      const isHl = !!highlightActionId && node.action_id === highlightActionId;

      const groupCls = isCtx
        ? "trajectory-node-context"
        : ["trajectory-node", `trajectory-status-${safeClass(node.status)}`,
           isHl ? "trajectory-action-highlighted" : ""].filter(Boolean).join(" ");

      const group = svgElement("g", {
        class: groupCls,
        transform: `translate(${node.x} ${node.y})`,
        tabindex: isCtx ? "-1" : "0",
        role:     isCtx ? "img" : "button",
        "data-action-id": node.action_id || "",
        "aria-label": isCtx ? node.label : `${node.type}: ${node.label}; ${node.status}`,
      });

      const titleEl = svgElement("title");
      titleEl.textContent = isCtx
        ? node.label
        : `${node.type} \u00b7 ${node.label} \u00b7 ${node.status}`;
      group.append(titleEl);

      if (isCtx) {
        const rect = svgElement("rect", { width: nw, height: nh, rx: String(nh / 2) });
        const text = svgElement("text", {
          x: String(nw / 2), y: String(nh / 2 + 4),
          class: "trajectory-ctx-label", "text-anchor": "middle",
        });
        text.textContent = node.label;
        group.append(rect, text);
        group.addEventListener("click", ev => ev.stopPropagation());
      } else {
        const rect    = svgElement("rect", { width: nw, height: nh, rx: "6" });
        const stageT  = svgElement("text", { x: "8", y: "15", class: "trajectory-stage" });
        stageT.textContent = node.type.replaceAll("_", " ").toUpperCase();
        const labelT  = svgElement("text", { x: "8", y: "32", class: "trajectory-label" });
        labelT.textContent = truncate(node.label, 18);
        const statusT = svgElement("text", { x: "8", y: "46", class: "trajectory-node-status" });
        statusT.textContent = truncate(node.status, 16);
        group.append(rect, stageT, labelT, statusT);

        const inspect = ev => {
          ev.stopPropagation();
          if (!details || !detailsJson) return;
          const payload = stagePayload(trajectory, node);
          details.classList.remove("hidden");
          // Hide placeholder
          const placeholder = document.querySelector("#trajectory-detail-placeholder");
          if (placeholder) placeholder.style.display = "none";
          // Render summary dl
          const summaryEl = document.querySelector("#trajectory-details-summary");
          if (summaryEl) renderDetailSummary(summaryEl, payload, trajectory);
          // Render raw JSON
          detailsJson.textContent = JSON.stringify(payload, null, 2);
        };
        group.addEventListener("click", inspect);
        group.addEventListener("keydown", ev => {
          if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); inspect(ev); }
        });
      }

      nodeLayer.append(group);

      if (accessibleList && !isCtx) {
        const item = document.createElement("li");
        item.textContent = `${node.type}: ${node.label}; status ${node.status}`;
        accessibleList.append(item);
      }
    });

    return layout;
  }

  // ?�?� Highlight helpers ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
  function highlightAction(nodeLayer, actionId) {
    if (!nodeLayer) return;
    nodeLayer.querySelectorAll("[data-action-id]").forEach(g => {
      g.classList.toggle(
        "trajectory-action-highlighted",
        !!actionId && g.getAttribute("data-action-id") === actionId
      );
    });
  }

  function scrollToAction(viewport, yPos) {
    if (yPos == null || !viewport) return;
    viewport.scrollTo({ top: Math.max(0, yPos - 20), behavior: "smooth" });
  }

  // ?�?� Evidence summary renderer ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
  /**
   * Populate the <dl> with a compact summary of the stage payload.
   * Only shows fields that have meaningful values.
   */
  function renderDetailSummary(dl, payload, trajectory) {
    if (!dl) return;
    dl.replaceChildren();

    const addRow = (term, value) => {
      if (!value && value !== 0) return;
      const dt = document.createElement("dt");
      dt.textContent = term;
      const dd = document.createElement("dd");
      dd.textContent = String(value);
      dl.append(dt, dd);
    };

    addRow("Stage",          payload.stage || "\u2014");
    addRow("Action",         payload.action_id || "\u2014");

    if (payload.request) {
      const reqStr = [payload.request.method, payload.request.path]
        .filter(Boolean).join(" ") || "\u2014";
      addRow("Request", reqStr);
      if (payload.request.operation) addRow("Operation", payload.request.operation);
    }

    addRow("Classification", payload.classification || "\u2014");

    if (Array.isArray(payload.roe_categories) && payload.roe_categories.length) {
      const cats = payload.roe_category_details?.length
        ? payload.roe_category_details.map(c => c.name ? `${c.code} ${c.name}` : c.code).join(", ")
        : payload.roe_categories.join(", ");
      addRow("ROE", cats);
    }

    const vCount = Array.isArray(payload.violations) ? payload.violations.length : 0;
    const uCount = Array.isArray(payload.unclassified) ? payload.unclassified.length : 0;
    if (vCount > 0) addRow("Violations",   String(vCount));
    if (uCount > 0) addRow("Unclassified", String(uCount));

    // Start / termination synthetic payloads
    if (payload.stage === "start") {
      addRow("Schema", payload.schema_version || "unknown");
    }
    if (payload.stage === "termination") {
      if (payload.reason)     addRow("Reason",   payload.reason);
      if (payload.final_step) addRow("Step",     String(payload.final_step));
    }
  }

  // ?�?� Action list panel ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
  function buildActionListPanel(panel, state, onSelect) {
    if (!panel) return;
    panel.replaceChildren();
    const { trajectory, actionIds, currentIdx } = state;
    if (!actionIds.length) return;

    const heading = document.createElement("div");
    heading.className   = "traj-list-heading";
    heading.textContent = "Actions";
    panel.append(heading);

    actionIds.forEach((aid, idx) => {
      const action = trajectory.actions.find(a => String(a.action_id) === aid);
      const method = action?.proposal?.method || "";
      const path   = action?.proposal?.path   || "";
      const status = actionOverallStatus(trajectory, aid);

      const item = document.createElement("div");
      item.className = "traj-action-item" + (idx === currentIdx ? " selected" : "");
      item.dataset.idx = String(idx);
      item.setAttribute("role",     "listitem");
      item.setAttribute("tabindex", "0");

      const idRow   = document.createElement("div");
      idRow.className   = "traj-action-item-id";
      idRow.textContent = aid;

      const pathRow = document.createElement("div");
      pathRow.className   = "traj-action-item-path";
      pathRow.textContent = [method, path].filter(Boolean).join(" ") || "\u2014";

      const badge = document.createElement("span");
      badge.className   = `traj-action-badge traj-badge-${safeClass(status)}`;
      badge.textContent = status;

      item.append(idRow, pathRow, badge);
      item.addEventListener("click",   ()  => onSelect(idx));
      item.addEventListener("keydown", ev  => {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); onSelect(idx); }
      });
      panel.append(item);
    });
  }

  function updateActionListSelection(panel, idx) {
    if (!panel) return;
    panel.querySelectorAll(".traj-action-item").forEach(item => {
      item.classList.toggle("selected", Number(item.dataset.idx) === idx);
    });
  }

  // ?�?� Module-level state ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
  let _traj_state = null;

  // ?�?� renderTrajectory ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
  function renderTrajectory(value, roe) {
    const section         = document.querySelector("#trajectory-section");
    const viewport        = document.querySelector(".trajectory-viewport");
    const expandBtn       = document.querySelector("#trajectory-expand-btn");
    const exitButton      = document.querySelector("#trajectory-fullscreen-exit");
    const svg             = document.querySelector("#trajectory-map");
    const edgeLayer       = document.querySelector("#trajectory-edges");
    const nodeLayer       = document.querySelector("#trajectory-nodes");
    const empty           = document.querySelector("#trajectory-empty");
    const count           = document.querySelector("#trajectory-count");
    const accessibleList  = document.querySelector("#trajectory-accessible-list");
    const details         = document.querySelector("#trajectory-details");
    const detailsJson     = document.querySelector("#trajectory-details-json");
    const prevBtn         = document.querySelector("#trajectory-action-prev");
    const nextBtn         = document.querySelector("#trajectory-action-next");
    const indexSpan       = document.querySelector("#trajectory-action-index");
    const actionInfo      = document.querySelector("#trajectory-action-info");
    const actionListPanel = document.querySelector("#trajectory-action-list");

    if (!section || !svg || !edgeLayer || !nodeLayer) return;

    const trajectory = normalizeTrajectory(value, roe);

    if (details)     details.classList.add("hidden");
    if (detailsJson) detailsJson.textContent = "";
    section.classList.toggle("hidden", !value);
    empty?.classList.toggle("hidden", trajectory.nodes.length > 0);
    if (count) count.textContent =
      `${trajectory.actions.length} actions \u00b7 ${trajectory.nodes.length} stages`;

    if (!value || !trajectory.nodes.length) {
      edgeLayer.replaceChildren(); nodeLayer.replaceChildren();
      if (accessibleList) accessibleList.replaceChildren();
      return;
    }

    // Build ordered action ID list
    const actionIds = trajectory.actions.map(a => String(a.action_id)).filter(Boolean);
    const nodeAids  = new Set(trajectory.nodes.filter(n => n.action_id).map(n => n.action_id));
    for (const aid of nodeAids) { if (!actionIds.includes(aid)) actionIds.push(aid); }

    // Default: highlight first violation action, else first action
    const defaultAid = selectDefaultAction(trajectory);
    const defaultIdx = defaultAid ? Math.max(0, actionIds.indexOf(defaultAid)) : 0;

    _traj_state = { trajectory, actionIds, currentIdx: defaultIdx, layout: null };

    const domRefs = { svg, edgeLayer, nodeLayer, accessibleList, details, detailsJson };

    // Render full graph with default highlight
    _traj_state.layout = renderFullGraph(trajectory, domRefs, actionIds,
      actionIds[defaultIdx] || null);

    // Toolbar ??always shows "N / total"
    const updateToolbar = (idx) => {
      const aid = actionIds[idx];
      if (indexSpan)  indexSpan.textContent  = `${idx + 1} / ${actionIds.length}`;
      if (actionInfo) actionInfo.textContent = aid ? actionInfoText(trajectory, aid) : "";
      if (prevBtn)    prevBtn.disabled = idx <= 0;
      if (nextBtn)    nextBtn.disabled = idx >= actionIds.length - 1;
    };
    updateToolbar(defaultIdx);

    // Build action list panel
    buildActionListPanel(actionListPanel, _traj_state, idx => {
      if (!_traj_state) return;
      const same = _traj_state.currentIdx === idx;
      const newIdx = same ? -1 : idx;
      _traj_state.currentIdx = newIdx >= 0 ? newIdx : defaultIdx;
      const aid = actionIds[_traj_state.currentIdx];
      highlightAction(nodeLayer, aid);
      scrollToAction(viewport, _traj_state.layout?.actionYMap[aid]);
      updateToolbar(_traj_state.currentIdx);
      updateActionListSelection(actionListPanel, newIdx >= 0 ? newIdx : -1);
    });

    // Nav buttons ??bound once per section lifetime
    if (section.dataset.navBound !== "true") {
      section.dataset.navBound = "true";

      const navigate = delta => {
        if (!_traj_state) return;
        const { actionIds: aids, layout: lay } = _traj_state;
        const cur    = _traj_state.currentIdx;
        const newIdx = Math.max(0, Math.min(aids.length - 1, cur + delta));
        if (newIdx === cur) return;
        _traj_state.currentIdx = newIdx;
        const aid = aids[newIdx];
        highlightAction(nodeLayer, aid);
        scrollToAction(viewport, lay?.actionYMap[aid]);
        updateToolbar(newIdx);
        updateActionListSelection(actionListPanel, newIdx);
      };

      prevBtn?.addEventListener("click", () => navigate(-1));
      nextBtn?.addEventListener("click", () => navigate(+1));
    }

    bindGraphFullscreen(section, viewport, exitButton, expandBtn);

    // Auto-scroll to the default highlighted action
    requestAnimationFrame(() => {
      const aid = actionIds[defaultIdx];
      scrollToAction(viewport, _traj_state?.layout?.actionYMap[aid]);
    });
  }

  return {
    normalizeTrajectory,
    layoutDirectedGraph,
    renderTrajectory,
    stagePayload,
    enterGraphFullscreen,
    exitGraphFullscreen,
    groupNodesByAction,
    selectDefaultAction,
    layoutActionTree,
  };
});


