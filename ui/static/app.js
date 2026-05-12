// Role Collector dashboard — vanilla JS.
// Page-specific bootstrap dispatched by data-page on <body>.

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => [...(root || document).querySelectorAll(sel)];

async function fetchJSON(url, options) {
  const resp = await fetch(url, options);
  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error(`${resp.status}: ${txt}`);
  }
  return resp.json();
}

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function fmtDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toISOString().replace("T", " ").slice(0, 19);
  } catch {
    return iso;
  }
}

function applyLink(j) {
  // Prefer apply_url (the ATS form); fall back to canonical_url (the posting).
  const url = j.apply_url || j.canonical_url;
  if (!url) return '<span class="text-mute">—</span>';
  return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener" class="apply-link">Apply ↗</a>`;
}

function badge(label) {
  const cls = {
    new: "new",
    possible_duplicate: "possible",
    duplicate: "duplicate",
  }[label] || "";
  return `<span class="badge ${cls}">${escapeHtml(label || "—")}</span>`;
}

function normalizedLocation(j) {
  if (!j.location_normalized_json) return j.location || "—";
  try {
    const parsed = JSON.parse(j.location_normalized_json);
    return parsed.display || j.location || "—";
  } catch {
    return j.location || "—";
  }
}

function parseJSONSafe(value) {
  if (!value || typeof value !== "string") return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function toast(msg) {
  let el = $("#toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => el.classList.remove("show"), 2400);
}

// ---------------------------------------------------------------------------
// /api/overview — metric tiles rendered on top of the Jobs page

async function renderMetrics() {
  try {
    const data = await fetchJSON("/api/overview");
    const t = data.totals;
    // Curated subset per user request: total jobs, new, funding, linkedin, companies, runs.
    const tiles = [
      ["Total jobs", t.jobs],
      ["New", t.new],
      ["Funding events", t.funding],
      ["LinkedIn posts", t.linkedin],
      ["Companies", t.companies],
      ["Search runs", t.runs],
    ];
    $("#metrics").innerHTML = tiles
      .map(
        ([label, n]) =>
          `<div class="metric"><div class="metric-label">${escapeHtml(label)}</div><div class="metric-value">${n}</div></div>`
      )
      .join("");
  } catch (e) {
    $("#metrics").innerHTML = `<div class="empty">Failed to load metrics: ${escapeHtml(e.message)}</div>`;
  }
}

// ---------------------------------------------------------------------------
// /api/jobs

let jobsState = { page: 1, limit: 50, q: "", dup_status: "", ats_type: "", remote_type: "", needs_review: false };

async function renderJobs() {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(jobsState)) {
    if (v !== "" && v !== false) qs.set(k, v);
  }
  const data = await fetchJSON("/api/jobs?" + qs.toString());
  // Filter chips
  function fillSelect(id, options, current) {
    const sel = $(id);
    sel.innerHTML =
      '<option value="">Any</option>' +
      options
        .map(
          (o) =>
            `<option value="${escapeHtml(o)}" ${current === o ? "selected" : ""}>${escapeHtml(o)}</option>`
        )
        .join("");
  }
  fillSelect("#filter-dup", data.filters.dup_status || [], jobsState.dup_status);
  fillSelect("#filter-ats", data.filters.ats_type || [], jobsState.ats_type);
  fillSelect("#filter-remote", data.filters.remote_type || [], jobsState.remote_type);

  // Rows
  if (!data.rows.length) {
    $("#jobs-body").innerHTML = `<tr><td colspan="11" class="empty">No jobs match the current filters.</td></tr>`;
  } else {
    $("#jobs-body").innerHTML = data.rows
      .map(
        (j) => `
        <tr>
          <td><a href="/jobs/${j.id}">${j.id}</a></td>
          <td>${escapeHtml(j.company_name)}</td>
          <td>${escapeHtml(j.title)}</td>
          <td>${escapeHtml(normalizedLocation(j))}</td>
          <td>${escapeHtml(j.remote_type || "—")}</td>
          <td>${escapeHtml(j.role_family || "—")}</td>
          <td>${escapeHtml(j.level || "—")}</td>
          <td>${escapeHtml(j.ats_type || "—")}</td>
          <td>${badge(j.duplicate_status)}</td>
          <td class="mono">${fmtDate(j.first_seen_at)}</td>
          <td>${applyLink(j)}</td>
        </tr>
      `
      )
      .join("");
  }
  $("#jobs-count").textContent = `${data.total} jobs · page ${data.page}`;
  $("#prev-page").disabled = data.page <= 1;
  $("#next-page").disabled = data.page * data.limit >= data.total;
}

function bindJobsFilters() {
  $("#search").addEventListener("input", debounce(() => {
    jobsState.q = $("#search").value.trim();
    jobsState.page = 1;
    renderJobs();
  }, 250));
  $("#filter-dup").addEventListener("change", () => { jobsState.dup_status = $("#filter-dup").value; jobsState.page = 1; renderJobs(); });
  $("#filter-ats").addEventListener("change", () => { jobsState.ats_type = $("#filter-ats").value; jobsState.page = 1; renderJobs(); });
  $("#filter-remote").addEventListener("change", () => { jobsState.remote_type = $("#filter-remote").value; jobsState.page = 1; renderJobs(); });
  $("#needs-review").addEventListener("change", () => { jobsState.needs_review = $("#needs-review").checked; jobsState.page = 1; renderJobs(); });
  $("#prev-page").addEventListener("click", () => { if (jobsState.page > 1) { jobsState.page -= 1; renderJobs(); }});
  $("#next-page").addEventListener("click", () => { jobsState.page += 1; renderJobs(); });
}

function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

// ---------------------------------------------------------------------------
// /api/jobs/:id

async function renderJobDetail(jobId) {
  const data = await fetchJSON(`/api/jobs/${jobId}`);
  const j = data.job;
  $("#job-title").textContent = `${j.title} — ${j.company_name}`;

  const metaCells = [
    ["Location", j.location || "—"],
    ["Normalized", normalizedLocation(j)],
    ["Remote", j.remote_type || "—"],
    ["Role family", j.role_family || "—"],
    ["Role match", j.role_match_status || "—"],
    ["Level", j.level || "—"],
    ["ATS", j.ats_type || "—"],
    ["Seniority", j.seniority || "—"],
    ["Employment", j.employment_type || "—"],
    ["Salary", j.salary_text || "—"],
    ["Posted", j.posted_date || "—"],
    ["Confidence", j.extraction_confidence != null ? Number(j.extraction_confidence).toFixed(2) : "—"],
    ["Dedup status", j.duplicate_status],
    ["First seen", fmtDate(j.first_seen_at)],
  ];
  $("#meta-grid").innerHTML = metaCells
    .map(
      ([label, val]) =>
        `<div class="detail-meta-cell"><div class="detail-meta-label">${escapeHtml(label)}</div><div class="detail-meta-value">${escapeHtml(val)}</div></div>`
    )
    .join("");

  let links = "";
  if (j.apply_url) links += `<a href="${escapeHtml(j.apply_url)}" target="_blank" rel="noopener">Apply →</a> · `;
  if (j.canonical_url) links += `<span class="mono">${escapeHtml(j.canonical_url)}</span>`;
  $("#apply-links").innerHTML = links || "";

  let skillsHtml = "";
  try {
    const skills = j.skills_json ? JSON.parse(j.skills_json) : [];
    if (skills && skills.length) {
      skillsHtml = "<strong>Skills:</strong> " + skills.map(escapeHtml).join(", ");
    }
  } catch {}
  $("#skills").innerHTML = skillsHtml;

  if (data.dedup_breakdown) {
    const b = data.dedup_breakdown;
    $("#dedup-breakdown").innerHTML = `
      <div class="card">
        <div class="card-title">Dedup breakdown vs job #${j.duplicate_of_job_id}</div>
        <div class="scores">
          <div class="score"><div class="score-label">Company</div><div class="score-value">${fmtScore(b.company_score)}</div></div>
          <div class="score"><div class="score-label">Title</div><div class="score-value">${fmtScore(b.title_score)}</div></div>
          <div class="score"><div class="score-label">Description</div><div class="score-value">${fmtScore(b.description_score)}</div></div>
          <div class="score"><div class="score-label">Location</div><div class="score-value">${fmtScore(b.location_score)}</div></div>
          <div class="score"><div class="score-label">Skills</div><div class="score-value">${fmtScore(b.skills_score)}</div></div>
        </div>
      </div>
    `;
  }

  $("#description").textContent = j.description || "(no description)";

  if (data.sources && data.sources.length) {
    $("#sources-body").innerHTML = data.sources
      .map(
        (s) => `
        <tr>
          <td>${escapeHtml(s.source_type || "—")}</td>
          <td>${escapeHtml(s.batch_id || "—")}</td>
          <td>${escapeHtml(s.source_query || "—")}</td>
          <td><a href="${escapeHtml(s.source_url)}" target="_blank" rel="noopener" class="mono">${escapeHtml(s.source_url || "—")}</a></td>
          <td class="mono">${fmtDate(s.found_at)}</td>
        </tr>
      `
      )
      .join("");
  } else {
    $("#sources-body").innerHTML = `<tr><td colspan="5" class="empty">No source records.</td></tr>`;
  }
}

function fmtScore(v) {
  if (v == null) return "—";
  return Number(v).toFixed(2);
}

// ---------------------------------------------------------------------------
// /api/dedup-pairs

async function renderDedup() {
  const data = await fetchJSON("/api/dedup-pairs");
  if (!data.pairs.length) {
    $("#pairs").innerHTML = `<div class="empty">Nothing to review — no possible duplicates pending.</div>`;
    return;
  }
  $("#pairs").innerHTML = data.pairs.map(renderPair).join("");
  $("#pending-count").textContent = `${data.pairs.length} pairs pending`;
  bindDedupActions();
}

function renderPair(p) {
  const score = p.new.duplicate_score != null ? Number(p.new.duplicate_score).toFixed(3) : "—";
  const matchHtml = p.match
    ? renderPairSide(p.match, "existing match")
    : `<div class="pair-side"><div class="empty">Match job #${p.new.duplicate_of_job_id} not found.</div></div>`;
  const scoresHtml = p.breakdown
    ? `<div class="scores">
        <div class="score"><div class="score-label">Company</div><div class="score-value">${fmtScore(p.breakdown.company_score)}</div></div>
        <div class="score"><div class="score-label">Title</div><div class="score-value">${fmtScore(p.breakdown.title_score)}</div></div>
        <div class="score"><div class="score-label">Description</div><div class="score-value">${fmtScore(p.breakdown.description_score)}</div></div>
        <div class="score"><div class="score-label">Location</div><div class="score-value">${fmtScore(p.breakdown.location_score)}</div></div>
        <div class="score"><div class="score-label">Skills</div><div class="score-value">${fmtScore(p.breakdown.skills_score)}</div></div>
      </div>`
    : "";
  return `
    <div class="pair-card" data-job-id="${p.new.id}">
      <div class="pair-header">
        <div>Pair: job <strong>#${p.new.id}</strong> ⇄ job <strong>#${p.new.duplicate_of_job_id}</strong></div>
        <div>Score: <strong>${score}</strong></div>
      </div>
      <div class="pair-grid">
        ${renderPairSide(p.new, "new")}
        ${matchHtml}
      </div>
      ${scoresHtml}
      <div class="pair-actions">
        <button class="primary" data-action="confirm">Confirm duplicate</button>
        <button class="danger" data-action="reject">Reject (mark new)</button>
      </div>
    </div>
  `;
}

function renderPairSide(j, label) {
  return `
    <div class="pair-side">
      <div class="pair-side-label">${label} — #${j.id}</div>
      <div class="pair-title">${escapeHtml(j.title)}</div>
      <div class="pair-company">${escapeHtml(j.company_name)}</div>
      <div class="pair-meta">${escapeHtml(j.location || "—")} · ${escapeHtml(j.ats_type || "—")} · first seen ${fmtDate(j.first_seen_at)}</div>
      ${j.apply_url ? `<a href="${escapeHtml(j.apply_url)}" target="_blank" rel="noopener">Apply →</a>` : ""}
      <div class="pair-desc">${escapeHtml((j.description || "").slice(0, 1200))}${(j.description || "").length > 1200 ? "…" : ""}</div>
    </div>
  `;
}

function bindDedupActions() {
  $$(".pair-card").forEach((card) => {
    const jobId = parseInt(card.dataset.jobId, 10);
    card.querySelector('[data-action="confirm"]').addEventListener("click", () => decide(jobId, "duplicate", card));
    card.querySelector('[data-action="reject"]').addEventListener("click", () => decide(jobId, "new", card));
  });
}

async function decide(jobId, decision, card) {
  try {
    await fetchJSON("/api/dedup-decision", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId, decision }),
    });
    card.remove();
    toast(`Job #${jobId} → ${decision}`);
    const remaining = $$(".pair-card").length;
    $("#pending-count").textContent = `${remaining} pairs pending`;
    if (remaining === 0) {
      $("#pairs").innerHTML = `<div class="empty">Nothing to review — no possible duplicates pending.</div>`;
    }
  } catch (e) {
    toast("Failed: " + e.message);
  }
}

// ---------------------------------------------------------------------------
// /api/discovery

async function renderDiscovery() {
  const data = await fetchJSON("/api/discovery");
  $("#funding-count").textContent = data.funding.length;
  $("#linkedin-count").textContent = data.linkedin.length;
  $("#watchlist-count").textContent = data.watchlist.length;

  $("#funding-body").innerHTML = data.funding.length
    ? data.funding
        .map(
          (f) => `
        <tr>
          <td>${f.id}</td>
          <td>${escapeHtml(f.company_name)}</td>
          <td>${escapeHtml(f.round || "—")}</td>
          <td>${escapeHtml(f.amount || "—")}</td>
          <td>${escapeHtml(f.announced_date || "—")}</td>
          <td><a href="${escapeHtml(f.source_url)}" target="_blank" rel="noopener" class="mono">${escapeHtml((f.source_url || "").slice(0, 70))}</a></td>
          <td class="mono">${fmtDate(f.found_at)}</td>
        </tr>`
        )
        .join("")
    : `<tr><td colspan="7" class="empty">No funding events yet.</td></tr>`;

  $("#linkedin-body").innerHTML = data.linkedin.length
    ? data.linkedin
        .map(
          (l) => `
        <tr>
          <td>${l.id}</td>
          <td>${escapeHtml(l.author_name || "—")}</td>
          <td>${escapeHtml(l.company_name || "—")}</td>
          <td>${escapeHtml(l.detected_role || "—")}</td>
          <td>${l.confidence != null ? Number(l.confidence).toFixed(2) : "—"}</td>
          <td>${escapeHtml(l.processed_status || "—")}</td>
          <td><a href="${escapeHtml(l.post_url)}" target="_blank" rel="noopener" class="mono">post</a></td>
          <td class="mono">${fmtDate(l.found_at)}</td>
        </tr>`
        )
        .join("")
    : `<tr><td colspan="8" class="empty">No LinkedIn posts yet.</td></tr>`;

  $("#watchlist-body").innerHTML = data.watchlist.length
    ? data.watchlist
        .map(
          (c) => `
        <tr>
          <td>${c.id}</td>
          <td>${escapeHtml(c.name)}</td>
          <td>${escapeHtml(c.ats_type || "—")}</td>
          <td><a href="${escapeHtml(c.ats_url)}" target="_blank" rel="noopener" class="mono">${escapeHtml((c.ats_url || "").slice(0, 60))}</a></td>
          <td class="mono">${fmtDate(c.last_polled_at)}</td>
          <td class="mono">${fmtDate(c.last_checked_at)}</td>
        </tr>`
        )
        .join("")
    : `<tr><td colspan="6" class="empty">No companies on the watchlist yet.</td></tr>`;

  bindTabs();
}

function bindTabs() {
  $$(".tab").forEach((t) => {
    t.addEventListener("click", () => {
      $$(".tab").forEach((x) => x.classList.remove("active"));
      $$(".tab-pane").forEach((x) => x.classList.remove("active"));
      t.classList.add("active");
      $("#pane-" + t.dataset.tab).classList.add("active");
    });
  });
}

// ---------------------------------------------------------------------------
// /api/runs

async function renderRuns() {
  const data = await fetchJSON("/api/runs");
  const toolCallsAll = Array.isArray(data.tool_calls) ? data.tool_calls : [];
  let traceRowsAll = [];
  let tracePage = 1;
  let toolCallsPage = 1;

  $("#cycles-body").innerHTML = data.cycles && data.cycles.length
    ? data.cycles
        .map(
          (c) => `
        <tr>
          <td>${c.id}</td>
          <td>${c.search_run_id || "—"}</td>
          <td>${escapeHtml(c.status)}</td>
          <td class="mono">${fmtDate(c.started_at)}</td>
          <td class="mono">${fmtDate(c.finished_at)}</td>
          <td class="mono">${fmtDate(c.sleep_until)}</td>
          <td>${escapeHtml(c.error_message || "—")}</td>
        </tr>`
        )
        .join("")
    : `<tr><td colspan="7" class="empty">No worker cycles.</td></tr>`;

  $("#react-metrics-body").innerHTML = data.cycles && data.cycles.length
    ? data.cycles
        .map((c) => {
          const summary = parseJSONSafe(c.summary_json) || {};
          const metrics = summary.react_metrics || {};
          const planner = summary.react_planner || {};
          return `
        <tr>
          <td>${c.id}</td>
          <td>${escapeHtml(c.status || "—")}</td>
          <td>${planner.enabled ? "on" : "off"}</td>
          <td>${planner.sampled_in ? "yes" : "no"}</td>
          <td>${metrics.planner_calls || 0}</td>
          <td>${metrics.planner_valid || 0}</td>
          <td>${metrics.planner_fallbacks || 0}</td>
          <td>${metrics.invalid_tool_choices || 0}</td>
          <td>${metrics.finish_declined_count || 0}</td>
          <td>${summary.react_steps || 0}</td>
        </tr>`;
        })
        .join("")
    : `<tr><td colspan="10" class="empty">No ReAct cycle summaries yet.</td></tr>`;

  $("#intelligence-metrics-body").innerHTML = data.cycles && data.cycles.length
    ? data.cycles
        .map((c) => {
          const summary = parseJSONSafe(c.summary_json) || {};
          const metrics = summary.intelligence_metrics || {};
          const reflection = summary.cycle_reflection || {};
          const focus = Array.isArray(reflection.next_cycle_focus)
            ? reflection.next_cycle_focus.slice(0, 2).join(" · ")
            : "—";
          const reflectionSummary = typeof reflection.summary === "string" ? reflection.summary : "—";
          return `
        <tr>
          <td>${c.id}</td>
          <td>${escapeHtml(c.status || "—")}</td>
          <td>${metrics.classifier_calls || 0}</td>
          <td>${metrics.classifier_valid || 0}</td>
          <td>${metrics.classifier_fallbacks || 0}</td>
          <td>${reflection.confidence != null ? Number(reflection.confidence).toFixed(2) : "—"}</td>
          <td>${escapeHtml(focus)}</td>
          <td>${escapeHtml(reflectionSummary.slice(0, 120))}</td>
        </tr>`;
        })
        .join("")
    : `<tr><td colspan="8" class="empty">No intelligence metrics yet.</td></tr>`;

  $("#batches-body").innerHTML = data.batches && data.batches.length
    ? data.batches
        .map(
          (b) => `
        <tr>
          <td>${b.id}</td>
          <td>${b.agent_cycle_id || "—"}</td>
          <td>${escapeHtml(b.status)}</td>
          <td>${b.job_count || 0}</td>
          <td class="mono">${fmtDate(b.created_at)}</td>
          <td class="mono">${fmtDate(b.flushed_at)}</td>
        </tr>`
        )
        .join("")
    : `<tr><td colspan="6" class="empty">No batches.</td></tr>`;

  $("#source-stats-body").innerHTML = data.source_stats && data.source_stats.length
    ? data.source_stats
        .map(
          (s) => {
            const meta = parseJSONSafe(s.metadata_json) || {};
            const signal = meta.signal || {};
            const signalBits = [];
            if (signal.google_blocked_at) signalBits.push("google_blocked");
            if (signal.posts_blocked) signalBits.push(`posts_blocked=${signal.posts_blocked}`);
            if (signal.queries_blocked) signalBits.push(`queries_blocked=${signal.queries_blocked}`);
            if (signal.stop_reason) signalBits.push(String(signal.stop_reason));
            const signalText = signalBits.length ? signalBits.join(" · ") : "—";
            return `
        <tr>
          <td>${escapeHtml(s.source_name)}</td>
          <td>${escapeHtml(s.last_status || "—")}</td>
          <td class="mono">${fmtDate(s.backoff_until)}</td>
          <td>${s.runs_total || 0}</td>
          <td>${s.successes_total || 0}</td>
          <td>${s.failures_total || 0}</td>
          <td>${s.candidates_total || 0}</td>
          <td>${s.jobs_saved_total || 0}</td>
        </tr>
        <tr>
          <td colspan="8"><small><code>signal:</code> ${escapeHtml(signalText)}</small></td>
        </tr>`;
          }
        )
        .join("")
    : `<tr><td colspan="8" class="empty">No source stats.</td></tr>`;

  const renderToolCalls = () => {
    const search = ($("#tool-calls-search")?.value || "").toLowerCase().trim();
    const statusFilter = $("#tool-calls-status-filter")?.value || "";
    const pageSize = parseInt($("#tool-calls-page-size")?.value || "25", 10);
    const filtered = toolCallsAll.filter((t) => {
      if (statusFilter && (t.status || "") !== statusFilter) return false;
      if (!search) return true;
      const hay = `${t.tool_name || ""} ${t.source_name || ""} ${t.error_message || ""}`.toLowerCase();
      return hay.includes(search);
    });
    const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
    toolCallsPage = Math.min(Math.max(1, toolCallsPage), totalPages);
    const start = (toolCallsPage - 1) * pageSize;
    const pageRows = filtered.slice(start, start + pageSize);
    $("#tool-calls-page-info").textContent = `page ${toolCallsPage} / ${totalPages} (${filtered.length})`;
    $("#tool-calls-body").innerHTML = pageRows.length
      ? pageRows
          .map(
            (t) => `
        <tr>
          <td>${t.id}</td>
          <td>${t.agent_cycle_id || "—"}</td>
          <td><code>${escapeHtml(t.tool_name || "—")}</code></td>
          <td>${escapeHtml(t.source_name || "—")}</td>
          <td>${escapeHtml(t.status || "—")}</td>
          <td>${t.latency_ms ?? "—"}</td>
          <td>${escapeHtml(t.error_message || "—")}</td>
          <td class="mono">${fmtDate(t.created_at)}</td>
        </tr>`
          )
          .join("")
      : `<tr><td colspan="8" class="empty">No tool calls match filters.</td></tr>`;
  };

  $("#runs-body").innerHTML = data.runs.length
    ? data.runs
        .map(
          (r) => `
        <tr>
          <td>${r.id}</td>
          <td>${escapeHtml(r.source_type)}</td>
          <td>${escapeHtml(r.status)}</td>
          <td class="mono">${fmtDate(r.started_at)}</td>
          <td class="mono">${fmtDate(r.finished_at)}</td>
          <td>${escapeHtml(r.query || "—")}</td>
          <td>${escapeHtml(r.error_message || "—")}</td>
        </tr>`
        )
        .join("")
    : `<tr><td colspan="7" class="empty">No runs.</td></tr>`;

  $("#events-body").innerHTML = data.events.length
    ? data.events
        .map(
          (e) => `
        <tr>
          <td>${e.id}</td>
          <td>${e.search_run_id || "—"}</td>
          <td><code>${escapeHtml(e.event_type)}</code></td>
          <td>${escapeHtml(e.event_message || "")}</td>
          <td class="mono">${fmtDate(e.created_at)}</td>
        </tr>`
        )
        .join("")
    : `<tr><td colspan="5" class="empty">No agent events.</td></tr>`;

  $("#fetches-body").innerHTML = data.fetches.length
    ? data.fetches
        .map(
          (f) => `
        <tr>
          <td>${f.id}</td>
          <td>${f.search_run_id || "—"}</td>
          <td>${escapeHtml(f.status)}</td>
          <td>${f.http_status || "—"}</td>
          <td>${escapeHtml(f.domain || "—")}</td>
          <td>${escapeHtml(f.detected_page_type || "—")}</td>
          <td>${escapeHtml(f.blocked_reason || "—")}</td>
          <td><a href="${escapeHtml(f.url)}" target="_blank" rel="noopener" class="mono">${escapeHtml((f.url || "").slice(0, 60))}</a></td>
          <td class="mono">${fmtDate(f.fetched_at)}</td>
        </tr>`
        )
        .join("")
    : `<tr><td colspan="9" class="empty">No page fetches.</td></tr>`;

  const cycleSelect = $("#react-trace-cycle-select");
  const traceSearchInput = $("#react-trace-search");
  const traceStatusFilter = $("#react-trace-status-filter");
  const tracePageSizeSel = $("#react-trace-page-size");
  const tracePrevBtn = $("#react-trace-prev");
  const traceNextBtn = $("#react-trace-next");
  const tracePageInfo = $("#react-trace-page-info");

  const renderTraceTable = () => {
    const search = (traceSearchInput?.value || "").toLowerCase().trim();
    const statusFilter = traceStatusFilter?.value || "";
    const pageSize = parseInt(tracePageSizeSel?.value || "20", 10);
    const filtered = traceRowsAll.filter((step) => {
      const decision = step.decision || {};
      const obs = step.observation || {};
      if (statusFilter && (obs.status || "") !== statusFilter) return false;
      if (!search) return true;
      const hay = `${decision.tool_name || ""} ${decision.reason || ""} ${decision.thought_summary || ""} ${obs.message || ""}`.toLowerCase();
      return hay.includes(search);
    });
    const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
    tracePage = Math.min(Math.max(1, tracePage), totalPages);
    const start = (tracePage - 1) * pageSize;
    const pageRows = filtered.slice(start, start + pageSize);
    if (tracePageInfo) tracePageInfo.textContent = `page ${tracePage} / ${totalPages} (${filtered.length})`;
    $("#react-trace-body").innerHTML = pageRows.length
      ? pageRows
          .map((step) => {
            const decision = step.decision || {};
            const obs = step.observation || {};
            return `
            <tr>
              <td>${step.step_index || "—"}</td>
              <td>${escapeHtml(step.phase || "—")}</td>
              <td>${escapeHtml(decision.action || "—")}</td>
              <td><code>${escapeHtml(decision.tool_name || "—")}</code></td>
              <td>${escapeHtml(decision.reason || "—")}</td>
              <td>${escapeHtml(decision.thought_summary || "—")}</td>
              <td>${escapeHtml(obs.status || "—")}</td>
              <td>${escapeHtml(obs.message || "—")}</td>
              <td>${obs.candidates ?? "—"}</td>
              <td>${obs.jobs_saved ?? "—"}</td>
            </tr>`;
          })
          .join("")
      : `<tr><td colspan="10" class="empty">No trace rows match filters.</td></tr>`;
  };

  if (cycleSelect) {
    cycleSelect.innerHTML = (data.cycles || [])
      .map((c) => `<option value="${c.id}">Cycle ${c.id} · ${escapeHtml(c.status || "—")}</option>`)
      .join("");
    const renderTrace = async () => {
      const cycleId = cycleSelect.value;
      if (!cycleId) {
        $("#react-trace-body").innerHTML = `<tr><td colspan="10" class="empty">No cycle selected.</td></tr>`;
        traceRowsAll = [];
        renderTraceTable();
        return;
      }
      const detail = await fetchJSON(`/api/runs/${cycleId}/react-trace`);
      traceRowsAll = Array.isArray(detail.react_trace) ? detail.react_trace : [];
      tracePage = 1;
      renderTraceTable();
    };
    cycleSelect.onchange = () => {
      void renderTrace();
    };
    if (traceSearchInput) traceSearchInput.oninput = () => { tracePage = 1; renderTraceTable(); };
    if (traceStatusFilter) traceStatusFilter.onchange = () => { tracePage = 1; renderTraceTable(); };
    if (tracePageSizeSel) tracePageSizeSel.onchange = () => { tracePage = 1; renderTraceTable(); };
    if (tracePrevBtn) tracePrevBtn.onclick = () => { tracePage -= 1; renderTraceTable(); };
    if (traceNextBtn) traceNextBtn.onclick = () => { tracePage += 1; renderTraceTable(); };
    await renderTrace();
  }

  const toolCallsSearch = $("#tool-calls-search");
  const toolCallsStatus = $("#tool-calls-status-filter");
  const toolCallsPageSize = $("#tool-calls-page-size");
  const toolCallsPrev = $("#tool-calls-prev");
  const toolCallsNext = $("#tool-calls-next");
  if (toolCallsSearch) toolCallsSearch.oninput = () => { toolCallsPage = 1; renderToolCalls(); };
  if (toolCallsStatus) toolCallsStatus.onchange = () => { toolCallsPage = 1; renderToolCalls(); };
  if (toolCallsPageSize) toolCallsPageSize.onchange = () => { toolCallsPage = 1; renderToolCalls(); };
  if (toolCallsPrev) toolCallsPrev.onclick = () => { toolCallsPage -= 1; renderToolCalls(); };
  if (toolCallsNext) toolCallsNext.onclick = () => { toolCallsPage += 1; renderToolCalls(); };
  renderToolCalls();
}

// ---------------------------------------------------------------------------
// bootstrap

document.addEventListener("DOMContentLoaded", () => {
  const page = document.body.dataset.page;
  try {
    if (page === "jobs") {
      bindJobsFilters();
      renderMetrics();
      renderJobs();
    } else if (page === "job_detail") renderJobDetail(parseInt(document.body.dataset.jobId, 10));
    else if (page === "dedup") renderDedup();
    else if (page === "discovery") renderDiscovery();
    else if (page === "runs") renderRuns();
  } catch (e) {
    console.error(e);
    toast("Page error: " + e.message);
  }
});
