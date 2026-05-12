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
// /api/overview

async function renderOverview() {
  const data = await fetchJSON("/api/overview");
  const t = data.totals;
  const tiles = [
    ["Total jobs", t.jobs],
    ["New", t.new],
    ["Possible duplicates", t.possible],
    ["Confirmed duplicates", t.dup],
    ["Needs review", t.needs_review],
    ["Funding events", t.funding],
    ["LinkedIn posts", t.linkedin],
    ["Watchlist", t.watchlist],
    ["Companies", t.companies],
    ["Search runs", t.runs],
  ];
  $("#metrics").innerHTML = tiles
    .map(
      ([label, n]) =>
        `<div class="metric"><div class="metric-label">${escapeHtml(label)}</div><div class="metric-value">${n}</div></div>`
    )
    .join("");
  if (data.latest && data.latest.length > 0) {
    $("#latest-body").innerHTML = data.latest
      .map(
        (j) => `
        <tr>
          <td><a href="/jobs/${j.id}">${j.id}</a></td>
          <td>${escapeHtml(j.company_name)}</td>
          <td>${escapeHtml(j.title)}</td>
          <td>${escapeHtml(j.location || "—")}</td>
          <td>${escapeHtml(j.ats_type || "—")}</td>
          <td>${badge(j.duplicate_status)}</td>
          <td class="mono">${fmtDate(j.first_seen_at)}</td>
          <td>${applyLink(j)}</td>
        </tr>
      `
      )
      .join("");
  } else {
    $("#latest-body").innerHTML = `<tr><td colspan="8" class="empty">No jobs yet — run <code>make run</code>.</td></tr>`;
  }
  $("#db-path").textContent = data.db_path;
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
    $("#jobs-body").innerHTML = `<tr><td colspan="9" class="empty">No jobs match the current filters.</td></tr>`;
  } else {
    $("#jobs-body").innerHTML = data.rows
      .map(
        (j) => `
        <tr>
          <td><a href="/jobs/${j.id}">${j.id}</a></td>
          <td>${escapeHtml(j.company_name)}</td>
          <td>${escapeHtml(j.title)}</td>
          <td>${escapeHtml(j.location || "—")}</td>
          <td>${escapeHtml(j.remote_type || "—")}</td>
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
    ["Remote", j.remote_type || "—"],
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
          <td>${escapeHtml(s.source_query || "—")}</td>
          <td><a href="${escapeHtml(s.source_url)}" target="_blank" rel="noopener" class="mono">${escapeHtml(s.source_url || "—")}</a></td>
          <td class="mono">${fmtDate(s.found_at)}</td>
        </tr>
      `
      )
      .join("");
  } else {
    $("#sources-body").innerHTML = `<tr><td colspan="4" class="empty">No source records.</td></tr>`;
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
}

// ---------------------------------------------------------------------------
// bootstrap

document.addEventListener("DOMContentLoaded", () => {
  const page = document.body.dataset.page;
  try {
    if (page === "overview") renderOverview();
    else if (page === "jobs") {
      bindJobsFilters();
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
