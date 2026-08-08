"use strict";

const TOKEN = document.querySelector('meta[name="tarel-token"]').content;
const state = {
  data: null,
  selectedId: null,
  reviewId: null,
  objectKind: "all",
  reviewFilter: "pending",
  canvasMode: "space",
  scopeFilters: null,
  trace: null,
  traceOnCanvas: false,
  cy: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

async function api(path, payload) {
  const options = payload === undefined ? {} : {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Tarel-Token": TOKEN},
    body: JSON.stringify(payload),
  };
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error?.message || `Request failed (${response.status})`);
  return body;
}

async function load() {
  setFooter("Loading local artifacts…");
  state.data = await api("/api/bootstrap");
  initializeScopeFilters();
  const params = new URLSearchParams(window.location.search);
  if (params.get("mode") === "lineage") state.canvasMode = "lineage";
  if (!state.selectedId && state.data.objects.length) state.selectedId = mostConnectedObject();
  if (!state.reviewId) state.reviewId = nextReview()?.id || null;
  renderAll();
  $$('[data-canvas-mode]').forEach(button => button.classList.toggle("is-active", button.dataset.canvasMode === state.canvasMode));
  if (params.get("view") === "review") switchView("review");
  if (params.get("trace")) {
    await trace(params.get("trace"));
    if (state.canvasMode === "lineage" && state.trace) showTraceOnCanvas();
  }
  setFooter("Ready");
}

function renderAll() {
  const data = state.data;
  $("#graph-name").textContent = data.title || data.graph;
  $("#revision").textContent = data.revision.slice(0, 12);
  $("#revision").title = `Graph revision ${data.revision}`;
  $("#mode").textContent = data.editable ? "Edit enabled" : "Read only";
  $("#mode").className = `mode-badge${data.editable ? " edit" : ""}`;
  $("#review-badge").textContent = String(data.review.filter(item => ["draft", "review_required", "deferred"].includes(item.state)).length);
  renderObjectList();
  renderScopeFilters();
  renderGraph();
  renderInspector();
  renderZones();
  renderReview();
  renderManualLineage();
}

function renderObjectList() {
  const needle = $("#object-search").value.trim().toLowerCase();
  const objects = visibleObjects().filter(item =>
    (state.objectKind === "all" || item.type === state.objectKind) &&
    (!needle || item.label.toLowerCase().includes(needle) || annotationText(item).includes(needle))
  );
  $("#object-list").innerHTML = objects.map(item => `
    <button class="object-row${item.id === state.selectedId ? " is-active" : ""}" data-object="${escapeAttr(item.id)}" draggable="true">
      <span class="kind-icon">${item.type === "view" ? "V" : "T"}</span>
      <span class="object-copy"><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.graph)} · ${escapeHtml(item.namespace)} · ${item.fields.length} fields</small></span>
      <i class="state-dot ${escapeAttr(item.annotation?.state || "missing")}" title="${escapeAttr(item.annotation?.state || "missing")}"></i>
    </button>`).join("") || '<div class="empty-state"><p>No matching objects.</p></div>';
  $$(".object-row").forEach(row => {
    row.addEventListener("click", () => selectObject(row.dataset.object));
    row.addEventListener("dragstart", event => event.dataTransfer.setData("text/tarel-object", row.dataset.object));
  });
}

function initializeScopeFilters() {
  const facets = {
    systems: unique(state.data.objects.map(item => item.system).filter(Boolean)),
    areas: unique(state.data.objects.map(item => item.area_ref).filter(Boolean)),
    graphs: unique(state.data.objects.map(item => item.graph)),
    schemas: unique(state.data.objects.map(item => item.schema_ref)),
    zones: unique(state.data.objects.flatMap(item => item.zones || [])),
  };
  if (!state.scopeFilters) {
    state.scopeFilters = Object.fromEntries(
      Object.entries(facets).map(([name, values]) => [name, new Set(values)]),
    );
  } else {
    for (const [name, values] of Object.entries(facets)) {
      const current = state.scopeFilters[name] || new Set();
      state.scopeFilters[name] = new Set(values.filter(value => current.has(value)));
    }
  }
  state.scopeFacets = facets;
}

function renderScopeFilters() {
  const labels = {systems: "Systems", areas: "Areas", graphs: "Graphs", schemas: "Schemas", zones: "Zones"};
  $("#scope-filters").innerHTML = Object.entries(state.scopeFacets)
    .filter(([, values]) => values.length)
    .map(([facet, values]) => `<section class="scope-group"><header><strong>${labels[facet]}</strong><button data-scope-all="${facet}">All</button></header>${values.map(value => `<label><input type="checkbox" data-scope-facet="${facet}" value="${escapeAttr(value)}" ${state.scopeFilters[facet].has(value) ? "checked" : ""}/><span>${escapeHtml(value)}</span></label>`).join("")}</section>`)
    .join("");
  $$('[data-scope-facet]').forEach(input => input.addEventListener("change", () => {
    const selected = state.scopeFilters[input.dataset.scopeFacet];
    input.checked ? selected.add(input.value) : selected.delete(input.value);
    applyVisualScope();
  }));
  $$('[data-scope-all]').forEach(button => button.addEventListener("click", () => {
    const facet = button.dataset.scopeAll;
    state.scopeFilters[facet] = new Set(state.scopeFacets[facet]);
    applyVisualScope();
  }));
  $("#scope-count").textContent = `${visibleObjects().length}/${state.data.objects.length}`;
}

function applyVisualScope() {
  if (state.selectedId && !visibleObjects().some(item => item.id === state.selectedId)) {
    state.selectedId = visibleObjects()[0]?.id || null;
  }
  renderScopeFilters();
  renderObjectList();
  renderGraph();
  renderInspector();
  renderZones();
  renderReview();
}

function visibleObjects() {
  const zoneNarrowed = state.scopeFilters.zones.size < state.scopeFacets.zones.length;
  return state.data.objects.filter(item =>
    (!item.system || state.scopeFilters.systems.has(item.system)) &&
    (!item.area_ref || state.scopeFilters.areas.has(item.area_ref)) &&
    state.scopeFilters.graphs.has(item.graph) &&
    state.scopeFilters.schemas.has(item.schema_ref) &&
    (!zoneNarrowed || (item.zones || []).some(zone => state.scopeFilters.zones.has(zone)))
  );
}

function unique(values) { return [...new Set(values)].sort((left, right) => left.localeCompare(right)); }

function renderGraph() {
  const data = state.data;
  const scopedObjects = visibleObjects();
  const connectedObjects = lineageObjectIds();
  const objects = state.canvasMode === "lineage" && connectedObjects.size
    ? scopedObjects.filter(item => connectedObjects.has(item.id))
    : scopedObjects;
  const objectIds = new Set(objects.map(item => item.id));
  const lanes = unique(objects.map(item => `${item.system || "Standalone"} · ${item.area || item.graph} · ${item.namespace}`));
  const counts = new Map();
  const elements = objects.map(item => {
    const laneName = `${item.system || "Standalone"} · ${item.area || item.graph} · ${item.namespace}`;
    const lane = lanes.indexOf(laneName);
    const index = counts.get(laneName) || 0;
    counts.set(laneName, index + 1);
    return {data: {id: item.id, label: item.name, type: item.type, state: item.annotation?.state || "missing", namespace: item.namespace, graph: item.graph, parent: state.canvasMode === "space" ? spaceGroupIds(item).schema : undefined}, position: {x: lane * 300 + (index % 2) * 130, y: Math.floor(index / 2) * 82}};
  });
  if (state.canvasMode === "space") {
    elements.unshift(...spaceGroupElements(objects));
    elements.push(...data.edges.filter(edge => objectIds.has(edge.source) && objectIds.has(edge.target)).map(edge => ({data: {id: edge.id, source: edge.source, target: edge.target, type: edge.type, state: edge.metadata.state || "declared"}})));
  } else {
    elements.push(...lineageElements(objectIds));
  }
  if (state.cy) state.cy.destroy();
  state.cy = cytoscape({
    container: $("#graph-canvas"), elements,
    layout: state.canvasMode === "space" ? {name: "preset", fit: true, padding: 75} : {name: "breadthfirst", directed: true, fit: true, padding: 75, spacingFactor: 1.25, animate: !state.traceOnCanvas, animationDuration: 260},
    minZoom: .15, maxZoom: 2.3, wheelSensitivity: .18,
    style: [
      {selector: "node", style: {"background-color": "#181818", "border-color": "#4a4a4a", "border-width": 1, "color": "#ddd", "font-family": "Inter, sans-serif", "font-size": 10, "label": "data(label)", "shape": "round-rectangle", "text-max-width": 104, "text-wrap": "ellipsis", "text-valign": "center", "width": 112, "height": 42}},
      {selector: 'node[type = "view"]', style: {"border-color": "#22d3ee"}},
      {selector: 'node[type = "asset"]', style: {"background-color": "#10202a", "border-color": "#22d3ee", "shape": "diamond"}},
      {selector: 'node[type = "procedure"], node[type = "query"], node[type = "script"]', style: {"background-color": "#251b38", "border-color": "#a78bfa", "shape": "hexagon"}},
      {selector: 'node[type = "group-system"]', style: {"background-opacity": .05, "border-color": "#6366f1", "border-style": "solid", "border-width": 2, "label": "data(label)", "text-valign": "top", "text-halign": "center", "padding": 34, "shape": "round-rectangle", "font-size": 12}},
      {selector: 'node[type = "group-area"]', style: {"background-opacity": .035, "border-color": "#52525b", "border-style": "dashed", "label": "data(label)", "text-valign": "top", "padding": 25, "shape": "round-rectangle", "font-size": 10}},
      {selector: 'node[type = "group-schema"]', style: {"background-opacity": .02, "border-color": "#303038", "border-style": "dotted", "label": "data(label)", "text-valign": "top", "padding": 18, "shape": "round-rectangle", "font-size": 9}},
      {selector: 'node[state = "draft"], node[state = "review_required"]', style: {"border-color": "#f59e0b", "border-width": 2}},
      {selector: 'node[state = "validated"]', style: {"border-color": "#10b981", "border-width": 2}},
      {selector: "node:selected", style: {"background-color": "#24243a", "border-color": "#818cf8", "border-width": 3}},
      {selector: "edge", style: {"curve-style": "bezier", "line-color": "#3f3f46", "target-arrow-color": "#3f3f46", "target-arrow-shape": "triangle", "width": 1, "opacity": .75}},
      {selector: 'edge[type = "relationship_candidate"]', style: {"line-style": "dashed", "line-color": "#f59e0b", "target-arrow-color": "#f59e0b"}},
      {selector: 'edge[type = "lineage"]', style: {"line-color": "#6366f1", "target-arrow-color": "#818cf8", "width": 2}},
      {selector: 'edge[type = "process"]', style: {"line-style": "dashed", "line-color": "#22d3ee", "target-arrow-color": "#22d3ee"}},
      {selector: ".hidden", style: {"display": "none"}},
      {selector: ".dimmed", style: {"opacity": .1}},
      {selector: ".zone-focus", style: {"background-color": "#252547", "border-color": "#818cf8", "opacity": 1}},
      {selector: ".trace-focus", style: {"opacity": 1, "border-color": "#10b981", "border-width": 3}},
    ],
  });
  state.cy.on("tap", 'node[type = "table"], node[type = "view"]', event => selectObject(event.target.id()));
  state.cy.on("tap", 'node[type = "asset"], node[type = "procedure"], node[type = "query"], node[type = "script"]', event => {
    const reference = event.target.data("reference");
    if (!reference) return;
    $("#lineage-drawer").hidden = false;
    $("#lineage-reference").value = reference;
    $("#lineage-status").className = "notice";
    $("#lineage-status").textContent = "Trace this lineage asset to show its upstream path.";
  });
  if (state.selectedId && state.cy.$id(state.selectedId).length) state.cy.$id(state.selectedId).select();
  if (state.traceOnCanvas && state.trace) setTimeout(focusTrace, 0);
  $("#canvas-title").textContent = state.canvasMode === "space" ? "Information space" : "Data & process lineage";
  $("#canvas-subtitle").textContent = state.canvasMode === "space"
    ? `${objects.length} objects · ${data.edges.filter(edge => objectIds.has(edge.source) && objectIds.has(edge.target)).length} relationships · ${lanes.length} schema spaces`
    : `${objects.length} graph objects · ${data.lineage_flows.edges.length} lineage edges · ${data.lineages.length} documents`;
}

function lineageObjectIds() {
  const objectIds = new Set(state.data.objects.map(item => item.id));
  const connected = new Set();
  for (const edge of state.data.lineage_flows?.edges || []) {
    if (objectIds.has(edge.source)) connected.add(edge.source);
    if (objectIds.has(edge.target)) connected.add(edge.target);
  }
  if (state.traceOnCanvas && state.trace) {
    for (const hop of state.trace.hops) {
      for (const reference of [hop.source.reference, hop.target.reference]) {
        const id = resolveReference(reference);
        if (id && objectIds.has(id)) connected.add(id);
      }
    }
  }
  return connected;
}

function spaceGroupIds(item) {
  const systemName = item.system || "Standalone";
  const areaName = item.area || item.graph;
  return {
    system: `space-system::${systemName}`,
    area: `space-area::${systemName}::${areaName}`,
    schema: `space-schema::${systemName}::${areaName}::${item.graph}::${item.namespace}`,
  };
}

function spaceGroupElements(objects) {
  const groups = new Map();
  for (const item of objects) {
    const ids = spaceGroupIds(item);
    groups.set(ids.system, {data: {id: ids.system, label: item.system || "Standalone graphs", type: "group-system"}});
    groups.set(ids.area, {data: {id: ids.area, label: item.area || item.graph, type: "group-area", parent: ids.system}});
    groups.set(ids.schema, {data: {id: ids.schema, label: `${item.graph} · ${item.namespace}`, type: "group-schema", parent: ids.area}});
  }
  return [...groups.values()];
}

function lineageElements(visibleObjectIds) {
  const flows = state.data.lineage_flows || {nodes: [], edges: []};
  const trace = state.traceOnCanvas && state.trace ? traceElements(state.trace) : {nodes: [], edges: []};
  const allEdges = [...flows.edges.map(item => ({...item, type: item.type || "lineage"})), ...trace.edges];
  const connected = new Set();
  for (const edge of allEdges) { connected.add(edge.source); connected.add(edge.target); }
  const nodes = [...flows.nodes, ...trace.nodes]
    .filter((item, index, values) => values.findIndex(candidate => candidate.id === item.id) === index)
    .filter(item => connected.has(item.id))
    .map(item => ({data: {id: item.id, label: item.label || item.reference, reference: item.reference, type: item.kind || "asset", state: item.state || "observed"}}));
  const known = new Set([...visibleObjectIds, ...nodes.map(item => item.data.id)]);
  const edges = allEdges
    .filter(item => known.has(item.source) && known.has(item.target))
    .map(item => ({data: {id: item.id, source: item.source, target: item.target, type: item.type, state: item.state, relation: item.relation}}));
  return [...nodes, ...edges];
}

function traceElements(trace) {
  const references = new Map();
  const nodes = [];
  const resolve = item => {
    const existing = resolveReference(item.reference);
    if (existing) return existing;
    if (!references.has(item.id)) {
      const id = `trace-ref::${references.size}`;
      references.set(item.id, id);
      nodes.push({id, label: item.name || item.reference, reference: item.reference, kind: item.kind || "asset", state: item.annotation_state || "observed"});
    }
    return references.get(item.id);
  };
  const edges = trace.hops.map(hop => ({id: `trace-edge::${hop.id}`, source: resolve(hop.source), target: resolve(hop.target), type: "lineage", state: hop.state, relation: hop.relation}));
  resolve(trace.start);
  trace.origins.forEach(resolve);
  return {nodes, edges};
}

function resolveReference(reference) {
  const needle = String(reference || "").toLowerCase();
  const objects = state.data.objects.filter(item => [item.id, item.object_id, item.label, item.reference].some(value => String(value || "").toLowerCase() === needle));
  if (objects.length === 1) return objects[0].id;
  const lineage = (state.data.lineage_flows.nodes || []).filter(item => String(item.reference || "").toLowerCase() === needle);
  return lineage.length === 1 ? lineage[0].id : null;
}

function focusTrace() {
  const traceIds = new Set(state.trace.hops.flatMap(hop => [resolveReference(hop.source.reference), resolveReference(hop.target.reference)]).filter(Boolean));
  const traceEdges = state.cy.edges('[id ^= "trace-edge::"]');
  traceEdges.forEach(edge => { traceIds.add(edge.source().id()); traceIds.add(edge.target().id()); });
  state.cy.elements().addClass("dimmed");
  traceEdges.removeClass("dimmed").addClass("trace-focus");
  traceIds.forEach(id => state.cy.$id(id).removeClass("dimmed").addClass("trace-focus"));
  const focus = state.cy.elements(".trace-focus");
  if (focus.length) state.cy.fit(focus, 95);
}

function selectObject(id) {
  state.selectedId = id;
  renderObjectList();
  renderInspector();
  if (state.cy) { state.cy.nodes().unselect(); state.cy.$id(id).select(); focusSelected(); }
}

function focusSelected() {
  const selected = state.cy.$id(state.selectedId);
  if (!selected.length) return;
  const focus = selected.closedNeighborhood();
  state.cy.elements().removeClass("hidden dimmed zone-focus");
  state.cy.elements().difference(focus).addClass("hidden");
  focus.layout({
    name: "concentric",
    animate: false,
    avoidOverlap: true,
    concentric: node => node.id() === state.selectedId ? 10 : 1,
    levelWidth: () => 1,
    minNodeSpacing: 42,
    startAngle: -Math.PI / 2,
    sweep: 2 * Math.PI,
  }).run();
  state.cy.fit(focus, 115);
  $("#canvas-title").textContent = `Focus · ${selectedObject()?.name || "object"}`;
}

function selectedObject() { return state.data.objects.find(item => item.id === state.selectedId); }

function renderInspector() {
  const item = selectedObject();
  if (!item) return;
  const annotation = item.annotation;
  const relationships = state.data.edges.filter(edge => edge.source === item.id || edge.target === item.id);
  $("#inspector").innerHTML = `
    <div class="inspector-head"><p class="eyebrow">${escapeHtml(item.type)} · ${escapeHtml(item.namespace)}</p><h2>${escapeHtml(item.name)}</h2><p class="mono">${escapeHtml(item.label)}</p></div>
    <section class="detail-section"><h3>Semantic description</h3><p class="description">${escapeHtml(annotation?.description || "No semantic proposal yet.")}</p></section>
    <section class="detail-section"><div class="fact-grid">
      ${fact("State", annotation?.state || "missing")}${fact("Role", annotation?.role || "—")}${fact("Grain", item.grain || "—")}${fact("Confidence", annotation?.confidence == null ? "—" : `${Math.round(annotation.confidence * 100)}%`)}
      ${fact("Primary key", item.primary_key.join(", ") || "—")}${fact("Relationships", String(relationships.length))}
    </div></section>
    <section class="detail-section"><h3>Fields · ${item.fields.length}</h3><table class="fields-table"><thead><tr><th>Name</th><th>Type</th><th>Semantic</th></tr></thead><tbody>${item.fields.map(field => `<tr><td>${escapeHtml(field.label)}</td><td class="mono">${escapeHtml(field.data_type || "—")}</td><td>${field.semantic_type ? `<span class="semantic-pill">${escapeHtml(field.semantic_type)}</span>` : "—"}</td></tr>`).join("")}</tbody></table></section>
    ${annotation?.warnings?.length ? `<section class="detail-section"><h3>Warnings</h3><p class="description">${annotation.warnings.map(escapeHtml).join(" · ")}</p></section>` : ""}`;
}

function renderZones() {
  const contexts = [];
  for (const workspace of state.data.workspaces) for (const system of workspace.systems) for (const zone of system.zones) contexts.push({workspace, system, zone});
  $("#zone-list").innerHTML = contexts.map((context, index) => `
    <article class="zone-card" data-zone-index="${index}"><header><strong>${escapeHtml(context.zone.name)}</strong><small>${context.zone.members.length}</small></header><p>${escapeHtml(context.zone.description || `${context.workspace.name} · ${context.system.name}`)}</p><div class="zone-members">${context.zone.members.map(member => { const object = objectForZoneMember(member); return `<button class="zone-member" data-focus-object="${escapeAttr(object?.id || "")}">${escapeHtml(object?.name || `${member.graph}:${member.object_id}`)}</button>`; }).join("")}</div></article>`).join("") || '<div class="zone-card"><strong>No zones yet</strong><p>Create one to group objects across schemas.</p></div>';
  $$(".zone-member").forEach(button => button.addEventListener("click", () => selectObject(button.dataset.focusObject)));
  $$(".zone-card[data-zone-index]").forEach(card => {
    const context = contexts[Number(card.dataset.zoneIndex)];
    card.addEventListener("click", event => { if (!event.target.closest(".zone-member")) highlightZone(context.zone); });
    card.addEventListener("dragover", event => { event.preventDefault(); card.classList.add("is-over"); });
    card.addEventListener("dragleave", () => card.classList.remove("is-over"));
    card.addEventListener("drop", async event => {
      event.preventDefault(); card.classList.remove("is-over");
      const objectId = event.dataTransfer.getData("text/tarel-object");
      if (!objectId) return;
      const members = context.zone.members.map(item => ({graph: item.graph, object_id: item.object_id}));
      const selected = state.data.objects.find(item => item.id === objectId);
      if (selected && !members.some(item => item.graph === selected.graph && item.object_id === selected.object_id)) members.push({graph: selected.graph, object_id: selected.object_id});
      await saveZone(context.workspace, context.system, context.zone, members);
    });
  });
}

function highlightZone(zone) {
  if (!state.cy) return;
  state.cy.elements().removeClass("hidden dimmed zone-focus");
  state.cy.elements().addClass("dimmed");
  const members = zone.members.map(objectForZoneMember).filter(Boolean).map(item => state.cy.$id(item.id));
  members.forEach(node => { node.removeClass("dimmed").addClass("zone-focus"); node.connectedEdges().removeClass("dimmed"); });
  state.cy.fit(state.cy.collection(members), 100);
  $("#canvas-title").textContent = `Zone · ${zone.name}`;
}

function objectForZoneMember(member) {
  return state.data.objects.find(item => item.graph === member.graph && item.object_id === member.object_id);
}

async function saveZone(workspace, system, zone, members) {
  if (!state.data.editable) return toast("Restart with --edit to change zones.");
  try {
    await api("/api/zone/save", {workspace: workspace.name, workspace_revision: workspace.revision, system: system.name, area: system.areas[0]?.name || "discovered", zone: zone.name, description: zone.description, members});
    toast(`Zone ${zone.name} updated.`); await load();
  } catch (error) { toast(error.message); }
}

function renderReview() {
  const visibleIds = new Set(visibleObjects().map(item => item.id));
  const scopedReview = state.data.review.filter(item => visibleIds.has(item.id));
  const pending = scopedReview.filter(item => ["draft", "review_required", "deferred"].includes(item.state));
  const reviewed = scopedReview.filter(item => ["validated", "rejected"].includes(item.state)).length;
  const annotated = scopedReview.filter(item => item.state !== "missing").length;
  $("#review-progress").textContent = `${reviewed} of ${annotated} annotated tables decided · ${scopedReview.length - annotated} missing`;
  $("#review-progress-bar").style.width = `${annotated ? Math.round(reviewed / annotated * 100) : 0}%`;
  const records = state.reviewFilter === "pending" ? pending : scopedReview;
  if (!records.some(item => item.id === state.reviewId)) state.reviewId = records[0]?.id || null;
  $("#review-list").innerHTML = records.map(item => `
    <button class="review-item${item.id === state.reviewId ? " is-active" : ""}" data-review="${escapeAttr(item.id)}"><i class="state-dot ${escapeAttr(item.state)}"></i><span><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.annotation?.description || "No semantic description")}</small></span><span class="state-badge">${stateLabel(item.state)}</span></button>`).join("") || '<div class="empty-state"><p>No proposals in this filter.</p></div>';
  $$(".review-item").forEach(button => button.addEventListener("click", () => { state.reviewId = button.dataset.review; renderReview(); }));
  renderReviewEditor();
}

function currentReview() { return state.data.review.find(item => item.id === state.reviewId); }
function nextReview() { return state.data?.review.find(item => ["draft", "review_required", "deferred"].includes(item.state)); }

function renderReviewEditor() {
  const record = currentReview();
  if (!record) { $("#review-editor").innerHTML = '<div class="empty-state"><h2>Queue complete</h2><p>No table-level proposals are waiting.</p></div>'; renderEvidence(null); return; }
  const annotation = record.annotation;
  const disabled = !state.data.editable || !annotation;
  $("#review-editor").innerHTML = `
    <div class="review-title"><div><p class="eyebrow">${escapeHtml(record.type)} annotation</p><h2>${escapeHtml(record.label)}</h2><p>${record.field_count} fields · ${stateLabel(record.state)}</p></div><span class="state-badge">${stateLabel(record.state)}</span></div>
    <div class="step-strip"><div class="step"><strong>1 · Read</strong>Understand the proposal</div><div class="step"><strong>2 · Check</strong>Compare evidence</div><div class="step"><strong>3 · Edit</strong>Correct meaning</div><div class="step"><strong>4 · Decide</strong>Approve or reject</div></div>
    ${!annotation ? `<div class="missing-callout"><strong>No provider proposal exists for this table.</strong><p>Generate one with the configured provider or coding agent, then return here for review.</p><code>tarel annotation next ${escapeHtml(state.data.graph)} --samples 10</code></div>` : `
    <form id="annotation-form" class="editor-form">
      <label><span>Business description</span><textarea name="description" required ${disabled ? "disabled" : ""}>${escapeHtml(annotation.description)}</textarea></label>
      <div class="field-grid"><label><span>Business role</span><input name="role" value="${escapeAttr(annotation.role || "")}" ${disabled ? "disabled" : ""} /></label><label><span>Grain</span><input name="grain" value="${escapeAttr(record.grain || "")}" ${disabled ? "disabled" : ""} /></label></div>
      <label><span>Synonyms · one per line</span><textarea class="short-textarea" name="synonyms" ${disabled ? "disabled" : ""}>${escapeHtml((annotation.synonyms || []).join("\n"))}</textarea></label>
      <label><span>Warnings · one per line</span><textarea class="short-textarea" name="warnings" ${disabled ? "disabled" : ""}>${escapeHtml((annotation.warnings || []).join("\n"))}</textarea></label>
      <label><span>Human review reason</span><input name="reason" value="Reviewed in the local TAREL UI." required ${disabled ? "disabled" : ""} /></label>
      <label class="checkbox"><input name="include_fields" type="checkbox" ${disabled ? "disabled" : ""} /><span>Apply the final decision to all ${record.field_count} field proposals too</span></label>
    </form>
    <div class="editor-actions"><button class="danger-button" data-review-action="reject" ${disabled ? "disabled" : ""}>Reject</button><button class="quiet-button" data-review-action="later">Later</button><button class="quiet-button" data-review-action="save" ${disabled ? "disabled" : ""}>Save edits</button><span class="spacer"></span><button class="primary-button" data-review-action="approve" ${disabled ? "disabled" : ""}>Approve &amp; next</button></div>`}`;
  $$('[data-review-action]').forEach(button => button.addEventListener("click", () => reviewAction(button.dataset.reviewAction)));
  renderEvidence(record);
}

async function reviewAction(action) {
  const record = currentReview();
  if (!record) return;
  if (action === "later") { advanceReview(record.id); return; }
  const form = $("#annotation-form");
  if (!form?.reportValidity()) return;
  const values = new FormData(form);
  const patch = {description: values.get("description"), role: emptyToNull(values.get("role")), grain: emptyToNull(values.get("grain")), synonyms: lines(values.get("synonyms")), warnings: lines(values.get("warnings"))};
  const reason = values.get("reason");
  try {
    if (action === "save" || changed(record, patch)) {
      const result = await api("/api/annotation/edit", {graph: record.graph, reference: record.label, patch, reason, revision: state.data.revisions[record.graph]});
      state.data.revisions[record.graph] = result.revision;
    }
    if (action === "approve" || action === "reject") {
      await api("/api/annotation/decision", {graph: record.graph, reference: record.label, state: action === "approve" ? "validated" : "rejected", reason, include_fields: values.get("include_fields") === "on", revision: state.data.revisions[record.graph]});
    }
    toast(action === "approve" ? "Annotation approved." : action === "reject" ? "Annotation rejected." : "Edits saved as a draft.");
    const previous = record.id; await load(); if (action !== "save") advanceReview(previous);
  } catch (error) { toast(error.message); }
}

function advanceReview(previousId) {
  const pending = state.data.review.filter(item => ["draft", "review_required", "deferred"].includes(item.state) && item.id !== previousId);
  state.reviewId = pending[0]?.id || null; renderReview();
}

function renderEvidence(record) {
  if (!record?.annotation) { $("#review-evidence").innerHTML = '<div class="empty-state"><h2>No evidence</h2><p>This object has no semantic proposal yet.</p></div>'; return; }
  const annotation = record.annotation;
  $("#review-evidence").innerHTML = `<p class="eyebrow">Proposal context</p><h2>Evidence</h2>${annotation.evidence.map(item => `<article class="evidence-card"><strong>${escapeHtml(item.source)}</strong><span>${escapeHtml(item.reference)}</span>${item.value ? `<small>${escapeHtml(item.value)}</small>` : ""}${item.reason ? `<small>${escapeHtml(item.reason)}</small>` : ""}</article>`).join("") || '<p class="guidance">No explicit evidence was recorded.</p>'}<div class="provenance"><h3>Provenance</h3><p>${escapeHtml(annotation.provenance?.source || "unknown")}${annotation.provenance?.provider ? ` · ${escapeHtml(annotation.provenance.provider)}` : ""}${annotation.provenance?.model ? ` · ${escapeHtml(annotation.provenance.model)}` : ""}</p>${annotation.confidence_reason ? `<p>${escapeHtml(annotation.confidence_reason)}</p>` : ""}</div>`;
}

function manualDocuments() {
  return state.data.lineage_documents.filter(item => item.manual);
}

function renderManualLineage() {
  const documents = manualDocuments();
  const container = $("#manual-lineage-list");
  if (!documents.length) {
    container.innerHTML = '<div class="empty-state compact"><p>No manual lineage overlay yet.</p><small>Add a job first, then connect a source and target through it.</small></div>';
  } else {
    container.innerHTML = documents.map(document => `
      <article class="manual-document">
        <header><span><strong>${escapeHtml(document.name)}</strong><small>${document.jobs.length} jobs · ${document.hops.length} hops</small></span><code>${escapeHtml(document.revision.slice(0, 10))}</code></header>
        <div class="manual-jobs">${document.jobs.map(job => `<div class="manual-job"><span class="kind-icon">${job.kind === "procedure" ? "SP" : "S"}</span><span><strong>${escapeHtml(job.qualified_name)}</strong><small>${escapeHtml(job.description || job.source_reference)}</small></span></div>`).join("")}</div>
        <div class="manual-hops">${document.hops.map(hop => `<div class="manual-hop"><span><strong>${escapeHtml(hop.source)}</strong><small>${escapeHtml(hop.role)}</small></span><span class="hop-relation">${escapeHtml(hop.operation)} →</span><span><strong>${escapeHtml(hop.target)}</strong><small>via ${escapeHtml(hop.job)}</small></span><span class="state-badge">${stateLabel(hop.state)}</span>${hop.state === "draft" && state.data.editable ? `<span class="manual-review-actions"><button class="quiet-button" data-lineage-decision="reject" data-lineage="${escapeAttr(document.name)}" data-item="${escapeAttr(hop.item_id)}">Reject</button><button class="primary-button" data-lineage-decision="validate" data-lineage="${escapeAttr(document.name)}" data-item="${escapeAttr(hop.item_id)}">Approve</button></span>` : ""}</div>`).join("") || '<p class="guidance">No data-flow hops yet.</p>'}</div>
      </article>`).join("");
  }
  $$('[data-lineage-decision]').forEach(button => button.addEventListener("click", () => decideManualHop(button)));
}

async function decideManualHop(button) {
  const document = state.data.lineage_documents.find(item => item.name === button.dataset.lineage);
  const decision = button.dataset.lineageDecision;
  const reason = window.prompt(
    decision === "validate" ? "Why is this lineage hop correct?" : "Why should this hop be rejected?",
    decision === "validate" ? "Reviewed in the local TAREL UI." : "Rejected in the local TAREL UI.",
  );
  if (!reason) return;
  try {
    await api("/api/lineage/decision", {lineage: document.name, item_id: button.dataset.item, decision, reason, revision: document.revision});
    toast(decision === "validate" ? "Lineage hop approved." : "Lineage hop rejected.");
    await load();
  } catch (error) { toast(error.message); }
}

function selectLineageForm(name) {
  $$('[data-lineage-form]').forEach(button => button.classList.toggle("is-active", button.dataset.lineageForm === name));
  $("#manual-job-form").hidden = name !== "job";
  $("#manual-hop-form").hidden = name !== "hop";
}

function populateManualForms() {
  const documents = manualDocuments();
  const defaultName = `${state.data.graph}-manual`;
  $("#manual-overlay-names").innerHTML = documents.map(item => `<option value="${escapeAttr(item.name)}"></option>`).join("");
  $("#graph-object-names").innerHTML = state.data.objects.map(item => `<option value="${escapeAttr(item.label)}"></option>`).join("");
  const jobOverlay = $("#manual-job-form [name=lineage]");
  if (!jobOverlay.value) jobOverlay.value = documents[0]?.name || defaultName;
  const hopOverlay = $("#manual-hop-form [name=lineage]");
  hopOverlay.innerHTML = documents.map(item => `<option value="${escapeAttr(item.name)}">${escapeHtml(item.name)}</option>`).join("");
  populateJobChoices();
}

function populateJobChoices() {
  const overlay = $("#manual-hop-form [name=lineage]").value;
  const document = manualDocuments().find(item => item.name === overlay);
  $("#manual-hop-form [name=job]").innerHTML = (document?.jobs || []).map(item => `<option value="${escapeAttr(item.qualified_name)}">${escapeHtml(item.qualified_name)}</option>`).join("");
}

function openLineageDialog(form = "job") {
  if (!state.data.editable) return toast("Restart with --edit to add manual lineage.");
  populateManualForms();
  if (form === "hop" && !manualDocuments().some(item => item.jobs.length)) {
    form = "job";
    toast("Add a job before creating a lineage hop.");
  }
  selectLineageForm(form);
  $("#lineage-dialog").showModal();
}

async function createManualJob(event) {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(event.currentTarget));
  const existing = state.data.lineage_documents.find(item => item.name === values.lineage);
  try {
    await api("/api/manual/job", {...values, revision: existing?.revision || null});
    toast(`Job ${values.qualified_name} added.`);
    await load();
    populateManualForms();
    selectLineageForm("hop");
  } catch (error) { toast(error.message); }
}

async function createManualHop(event) {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(event.currentTarget));
  const document = state.data.lineage_documents.find(item => item.name === values.lineage);
  try {
    await api("/api/manual/hop", {...values, line_start: Number(values.line_start), line_end: Number(values.line_end), revision: document.revision});
    $("#lineage-dialog").close();
    $("#lineage-drawer").hidden = false;
    toast(`Draft lineage hop added through ${values.job}.`);
    await load();
  } catch (error) { toast(error.message); }
}

async function trace(reference) {
  if (!state.data.lineages.length) { $("#lineage-drawer").hidden = false; $("#lineage-status").className = "notice error"; $("#lineage-status").textContent = "Restart with one or more --lineage options."; return; }
  $("#lineage-drawer").hidden = false; $("#lineage-reference").value = reference; $("#lineage-status").className = "notice"; $("#lineage-status").textContent = "Resolving selected lineage documents…";
  try {
    const result = await api("/api/lineage/upstream", {reference, lineages: state.data.lineages, max_hops: 12, states: ["draft", "review_required", "validated"]});
    state.trace = result;
    state.traceOnCanvas = false;
    $("#show-trace-canvas").hidden = false;
    $("#lineage-title").textContent = result.start.reference;
    $("#lineage-status").textContent = `${result.hops.length} hops · ${result.origins.length} origins${result.truncated ? " · truncated" : ""}${result.warnings.length ? ` · ${result.warnings.join(" ")}` : ""}`;
    $("#lineage-origins").innerHTML = result.origins.map(item => `<span class="origin">Origin · ${escapeHtml(item.reference)}</span>`).join("");
    $("#lineage-hops").innerHTML = result.hops.map(hop => `<article class="hop"><span class="hop-depth">${hop.depth}</span><span><strong>${escapeHtml(hop.source.reference)}</strong><small>${escapeHtml(hop.source.kind)}</small></span><span class="hop-relation">${escapeHtml(hop.relation)} →</span><span><strong>${escapeHtml(hop.target.reference)}</strong><small>${escapeHtml(hop.state)}${hop.via_definition ? ` · via ${escapeHtml(hop.via_definition)}` : ""}</small></span></article>`).join("") || '<div class="empty-state"><p>No upstream hops found.</p></div>';
  } catch (error) { state.trace = null; state.traceOnCanvas = false; $("#show-trace-canvas").hidden = true; $("#lineage-status").className = "notice error"; $("#lineage-status").textContent = error.message; $("#lineage-origins").innerHTML = ""; $("#lineage-hops").innerHTML = ""; }
}

function showTraceOnCanvas() {
  if (!state.trace) return;
  state.canvasMode = "lineage";
  state.traceOnCanvas = true;
  $$('[data-canvas-mode]').forEach(button => button.classList.toggle("is-active", button.dataset.canvasMode === "lineage"));
  $("#lineage-drawer").hidden = true;
  renderGraph();
}

function openZoneDialog() {
  if (!state.data.editable) return toast("Restart with --edit to create zones.");
  const selected = selectedObject(); $("#zone-selection").textContent = selected ? `Initial member: ${selected.label}` : "Select an object first."; $("#zone-dialog").showModal();
}

async function createZone(event) {
  event.preventDefault(); const selected = selectedObject(); if (!selected) return toast("Select an object first.");
  const values = Object.fromEntries(new FormData(event.currentTarget));
  try { await api("/api/zone/save", {...values, members: [{graph: selected.graph, object_id: selected.object_id}]}); $("#zone-dialog").close(); toast(`Zone ${values.zone} created.`); await load(); } catch (error) { toast(error.message); }
}

function switchView(view) {
  $$(".tab").forEach(button => button.classList.toggle("is-active", button.dataset.view === view));
  $$(".view").forEach(section => section.classList.toggle("is-active", section.id === `${view}-view`));
  if (view === "graph" && state.cy) setTimeout(() => { state.cy.resize(); state.cy.fit(undefined, 70); }, 0);
}

function fact(label, value) { return `<div class="fact"><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong></div>`; }
function mostConnectedObject() {
  const degree = new Map(state.data.objects.map(item => [item.id, 0]));
  state.data.edges.forEach(edge => { degree.set(edge.source, (degree.get(edge.source) || 0) + 1); degree.set(edge.target, (degree.get(edge.target) || 0) + 1); });
  return [...degree].sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))[0]?.[0] || state.data.objects[0]?.id;
}
function annotationText(item) { return `${item.annotation?.description || ""} ${(item.annotation?.synonyms || []).join(" ")}`.toLowerCase(); }
function stateLabel(value) { return ({draft: "Draft", review_required: "Review required", deferred: "Deferred", validated: "Approved", rejected: "Removed", missing: "Missing"})[value] || value; }
function lines(value) { return String(value || "").split("\n").map(item => item.trim()).filter(Boolean); }
function emptyToNull(value) { const clean = String(value || "").trim(); return clean || null; }
function changed(record, patch) { const a = record.annotation; return patch.description !== a.description || patch.role !== (a.role || null) || patch.grain !== (record.grain || null) || JSON.stringify(patch.synonyms) !== JSON.stringify(a.synonyms || []) || JSON.stringify(patch.warnings) !== JSON.stringify(a.warnings || []); }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[character]); }
function escapeAttr(value) { return escapeHtml(value); }
function setFooter(value) { $("#footer-status").textContent = value; }
function toast(value) { const element = $("#toast"); element.textContent = value; element.hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => { element.hidden = true; }, 4200); }

$("#object-search").addEventListener("input", renderObjectList);
$$('[data-kind]').forEach(button => button.addEventListener("click", () => { state.objectKind = button.dataset.kind; $$('[data-kind]').forEach(item => item.classList.toggle("is-active", item === button)); renderObjectList(); }));
$$('.tab').forEach(button => button.addEventListener("click", () => switchView(button.dataset.view)));
$$('.review-filter').forEach(button => button.addEventListener("click", () => { state.reviewFilter = button.dataset.reviewFilter; $$('.review-filter').forEach(item => item.classList.toggle("is-active", item === button)); renderReview(); }));
$("#fit-graph").addEventListener("click", focusSelected);
$("#show-all").addEventListener("click", () => { state.traceOnCanvas = false; state.cy.elements().removeClass("hidden dimmed zone-focus trace-focus"); state.cy.fit(undefined, 70); $("#canvas-title").textContent = state.canvasMode === "space" ? "Information space · all objects" : "Lineage · all selected documents"; });
$("#trace-selected").addEventListener("click", () => trace(selectedObject()?.reference || ""));
$("#show-trace-canvas").addEventListener("click", showTraceOnCanvas);
$$('[data-canvas-mode]').forEach(button => button.addEventListener("click", () => {
  state.canvasMode = button.dataset.canvasMode;
  state.traceOnCanvas = state.canvasMode === "lineage" && state.traceOnCanvas;
  $$('[data-canvas-mode]').forEach(item => item.classList.toggle("is-active", item === button));
  renderGraph();
}));
$("#close-lineage").addEventListener("click", () => { $("#lineage-drawer").hidden = true; });
$("#lineage-form").addEventListener("submit", event => { event.preventDefault(); trace($("#lineage-reference").value); });
$("#add-lineage").addEventListener("click", () => openLineageDialog("job"));
$("#add-lineage-drawer").addEventListener("click", () => openLineageDialog("job"));
$$('[data-lineage-form]').forEach(button => button.addEventListener("click", () => selectLineageForm(button.dataset.lineageForm)));
$("#manual-hop-form [name=lineage]").addEventListener("change", populateJobChoices);
$("#manual-job-form").addEventListener("submit", createManualJob);
$("#manual-hop-form").addEventListener("submit", createManualHop);
$("#close-lineage-dialog").addEventListener("click", () => $("#lineage-dialog").close());
$("#new-zone").addEventListener("click", openZoneDialog);
$("#zone-form").addEventListener("submit", createZone);
$("#close-zone").addEventListener("click", () => $("#zone-dialog").close());
$("#cancel-zone").addEventListener("click", () => $("#zone-dialog").close());

load().catch(error => { setFooter("Failed"); document.body.innerHTML = `<div class="empty-state"><h1>TAREL UI could not start</h1><p>${escapeHtml(error.message)}</p></div>`; });
