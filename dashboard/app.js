const state = {
  snapshot: null,
  selectedSource: "",
};

function formatDate(value) {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function badgeClass(row) {
  if (row.active) return "ok";
  if (row.last_error) return "error";
  if (row.last_run_at) return "ok";
  return "idle";
}

function badgeLabel(row) {
  if (row.active) return "Running";
  if (row.last_error) return "Error";
  if (row.last_run_at) return "Idle";
  return "Pending";
}

function renderStats(snapshot) {
  const summary = snapshot.scheduler.summary;
  const apiSummary = snapshot.api_status.summary;
  const cards = [
    ["Scheduled Platforms", summary.scheduled_platforms],
    ["Active Runs", summary.active_runs],
    ["Healthy APIs", apiSummary.healthy],
    ["API Errors", apiSummary.errors],
  ];

  document.getElementById("stats-grid").innerHTML = cards
    .map(
      ([label, value]) => `
        <article class="stat-card">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
        </article>
      `
    )
    .join("");
}

function renderPlatformTable(snapshot) {
  const rows = snapshot.scheduler.platform_rows;
  const body = document.getElementById("platform-table-body");
  if (!rows.length) {
    body.innerHTML = `
      <tr>
        <td colspan="6">
          <div class="empty-state">No scheduled platforms yet.</div>
        </td>
      </tr>
    `;
    return;
  }

  body.innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td>${escapeHtml(row.chat_id)}</td>
          <td>${escapeHtml(row.platform)}</td>
          <td><span class="status-badge ${badgeClass(row)}">${escapeHtml(badgeLabel(row))}</span></td>
          <td>${escapeHtml(formatDate(row.last_run_at))}</td>
          <td>${escapeHtml(row.last_result_count)}</td>
          <td>${escapeHtml(row.seen_job_count)}</td>
        </tr>
      `
    )
    .join("");
}

function renderApiStatus(snapshot) {
  const list = document.getElementById("api-status-list");
  const sources = snapshot.api_status.sources;
  if (!sources.length) {
    list.innerHTML = `<div class="empty-state">No API log files found yet.</div>`;
    return;
  }

  list.innerHTML = sources
    .map(
      (source) => `
        <div class="status-card">
          <div class="status-card-header">
            <h3>${escapeHtml(source.source)}</h3>
            <span class="status-badge ${source.healthy ? "ok" : "error"}">
              ${source.healthy ? "Healthy" : "Attention"}
            </span>
          </div>
          <p class="meta">
            Latest action: ${escapeHtml(source.latest_action || "n/a")}<br />
            Latest status: ${escapeHtml(source.latest_status)}<br />
            Last seen: ${escapeHtml(formatDate(source.latest_timestamp))}<br />
            Recent errors: ${escapeHtml(source.recent_error_count)}
          </p>
        </div>
      `
    )
    .join("");
}

function renderSourceFilter(snapshot) {
  const filter = document.getElementById("source-filter");
  const current = state.selectedSource;
  const options = [
    `<option value="">All</option>`,
    ...snapshot.api_status.sources.map(
      (source) => `<option value="${escapeHtml(source.source)}">${escapeHtml(source.source)}</option>`
    ),
  ];
  filter.innerHTML = options.join("");
  filter.value = current;
}

function renderApiLogs(snapshot) {
  const source = state.selectedSource;
  const logs = source
    ? snapshot.recent_api_logs.filter((entry) => entry.source === source)
    : snapshot.recent_api_logs;
  const container = document.getElementById("api-log-list");

  if (!logs.length) {
    container.innerHTML = `<div class="empty-state">No API events for this filter.</div>`;
    return;
  }

  container.innerHTML = logs
    .slice(0, 50)
    .map(
      (entry) => `
        <article class="log-card">
          <div class="log-card-header">
            <h3>${escapeHtml(entry.source)} · ${escapeHtml(entry.action || "event")}</h3>
            <span class="status-badge ${
              String(entry.status).startsWith("4") || String(entry.status).startsWith("5") || entry.status === "exception"
                ? "error"
                : "ok"
            }">${escapeHtml(entry.status)}</span>
          </div>
          <p class="meta">${escapeHtml(formatDate(entry.timestamp))}</p>
          <pre class="json-preview">${escapeHtml(JSON.stringify(entry.metadata || {}, null, 2))}</pre>
        </article>
      `
    )
    .join("");
}

function renderMonitorLog(snapshot) {
  const panel = document.getElementById("monitor-log");
  if (!snapshot.monitor_log_tail.length) {
    panel.textContent = "No monitor log lines yet.";
    return;
  }
  panel.textContent = snapshot.monitor_log_tail.join("\n");
}

function renderSnapshot(snapshot) {
  state.snapshot = snapshot;
  document.getElementById("generated-at").textContent = formatDate(snapshot.generated_at);
  renderStats(snapshot);
  renderPlatformTable(snapshot);
  renderApiStatus(snapshot);
  renderSourceFilter(snapshot);
  renderApiLogs(snapshot);
  renderMonitorLog(snapshot);
}

async function loadSnapshot() {
  const response = await fetch("/api/overview");
  const snapshot = await response.json();
  renderSnapshot(snapshot);
}

document.getElementById("refresh-button").addEventListener("click", () => {
  loadSnapshot().catch((error) => console.error(error));
});

document.getElementById("source-filter").addEventListener("change", (event) => {
  state.selectedSource = event.target.value;
  if (state.snapshot) {
    renderApiLogs(state.snapshot);
  }
});

loadSnapshot().catch((error) => console.error(error));
setInterval(() => {
  loadSnapshot().catch((error) => console.error(error));
}, 15000);
