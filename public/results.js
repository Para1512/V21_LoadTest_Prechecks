const form = document.getElementById("collate-form");
const feedback = document.getElementById("collate-feedback");
const output = document.getElementById("collate-output");
const submitButton = document.getElementById("collate-btn");
let currentPayload = null;

function getApiBaseUrl() {
  const { protocol } = window.location;
  if (protocol === "http:" || protocol === "https:") return "";
  return "http://127.0.0.1:3000";
}

const API_BASE_URL = getApiBaseUrl();

function formatFetchFailureMessage(error) {
  const msg = error && error.message ? String(error.message) : String(error);
  if (msg === "Failed to fetch" || /networkerror|load failed|fetch.*aborted/i.test(msg)) {
    return "Could not reach the portal server. Keep it running and open this page from its http address.";
  }
  return msg;
}

function setFeedback(message, kind) {
  feedback.textContent = message || "";
  feedback.className = `feedback${kind ? ` feedback-${kind}` : ""}`;
}

function hostLine(group) {
  const found = group.hosts && group.hosts.length ? group.hosts.join(", ") : "none";
  const missing = group.missing && group.missing.length ? ` Missing: ${group.missing.join(", ")}.` : "";
  return `Servers: ${found}.${missing}`;
}

function renderReport(group) {
  const section = document.createElement("section");
  section.className = "results-report";

  const title = document.createElement("h3");
  title.textContent = group.title;
  section.appendChild(title);

  const hosts = document.createElement("p");
  hosts.className = "results-hosts";
  hosts.textContent = hostLine(group);
  section.appendChild(hosts);

  if (!group.rows || group.rows.length === 0) {
    const empty = document.createElement("p");
    empty.className = "results-empty";
    empty.textContent = "No services were found for this group.";
    section.appendChild(empty);
    return section;
  }

  const table = document.createElement("table");
  table.className = "disk-table results-table";
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  ["Tuxedo service", "Avg response time", "Total no of calls"].forEach((label) => {
    const cell = document.createElement("th");
    cell.textContent = label;
    headRow.appendChild(cell);
  });
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  group.rows.forEach((row) => {
    const tr = document.createElement("tr");
    [row.service, row.avgResponseTime, String(row.totalCalls)].forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (index === 0) cell.className = "results-service";
      tr.appendChild(cell);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  section.appendChild(table);
  return section;
}

function textReport(payload) {
  const lines = [
    `V21 Tuxedo Results — ${payload.window.date} ${payload.window.from} to ${payload.window.until}`,
    ""
  ];
  [payload.online, payload.api].forEach((group) => {
    lines.push(group.title.toUpperCase());
    lines.push("Tuxedo service\tAvg response time\tTotal no of calls");
    group.rows.forEach((row) => {
      lines.push(`${row.service}\t${row.avgResponseTime}\t${row.totalCalls}`);
    });
    lines.push("");
  });
  return lines.join("\n");
}

function downloadCombinedReport(payload) {
  const blob = new Blob([textReport(payload)], { type: "text/plain;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `tuxedo_combined_${payload.window.date}_${payload.window.from.replace(":", "")}-${payload.window.until.replace(":", "")}.txt`;
  link.click();
  URL.revokeObjectURL(link.href);
}

async function copyCombinedReport(payload) {
  try {
    await navigator.clipboard.writeText(textReport(payload));
    setFeedback("Combined results copied to the clipboard.", "ok");
  } catch (_error) {
    setFeedback("Could not access the clipboard. Use Download combined results instead.", "error");
  }
}

async function compareResponseTimes(minutes) {
  if (!currentPayload?.reportId) return;
  const comparisonButtons = output.querySelectorAll("[data-comparison-minutes]");
  comparisonButtons.forEach((button) => {
    button.disabled = true;
  });
  setFeedback(`Collecting the first ${minutes} minutes for abnormal response-time comparison…`, "running");
  try {
    const response = await fetch(
      `${API_BASE_URL}/api/results/${encodeURIComponent(currentPayload.reportId)}/compare`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ minutes })
      }
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.success) {
      throw new Error(payload.message || `Comparison failed (HTTP ${response.status}).`);
    }
    currentPayload = payload;
    renderResult(payload);
    const lastComparison = payload.comparisons[payload.comparisons.length - 1];
    setFeedback(
      lastComparison.updates.length
        ? `Updated ${lastComparison.updates.length} abnormal response time(s) using the first ${minutes} minutes. Total calls were kept from the original test window.`
        : `No normal response times were found in the first ${minutes} minutes. The original averages were kept.`,
      "ok"
    );
  } catch (error) {
    setFeedback(formatFetchFailureMessage(error), "error");
    comparisonButtons.forEach((button) => {
      button.disabled = false;
    });
  }
}

function renderActions(payload) {
  const actions = document.createElement("div");
  actions.className = "results-actions";

  if (payload.reportId) {
    const viewRaw = document.createElement("a");
    viewRaw.className = "results-action";
    viewRaw.href = `${API_BASE_URL}/api/results/${encodeURIComponent(payload.reportId)}/raw?download=0`;
    viewRaw.target = "_blank";
    viewRaw.rel = "noopener";
    viewRaw.textContent = "View raw NPE report";
    actions.appendChild(viewRaw);

    const raw = document.createElement("a");
    raw.className = "results-action";
    raw.href = `${API_BASE_URL}/api/results/${encodeURIComponent(payload.reportId)}/raw`;
    raw.textContent = payload.file ? `Download raw NPE report (${payload.file})` : "Download raw NPE report";
    actions.appendChild(raw);
  }

  const download = document.createElement("button");
  download.type = "button";
  download.textContent = "Download combined results";
  download.addEventListener("click", () => downloadCombinedReport(payload));
  actions.appendChild(download);

  const copy = document.createElement("button");
  copy.type = "button";
  copy.textContent = "Copy combined results";
  copy.addEventListener("click", () => void copyCombinedReport(payload));
  actions.appendChild(copy);
  return actions;
}

function renderAbnormalResponseTimes(payload) {
  if (!payload.abnormal || payload.abnormal.length === 0) return null;
  const section = document.createElement("section");
  section.className = "results-abnormal";
  const title = document.createElement("h3");
  title.textContent = "Response-time review needed";
  section.appendChild(title);
  const note = document.createElement("p");
  note.className = "results-note";
  note.textContent = `${payload.abnormal.length} server-service response time(s) are at least ${payload.abnormalThreshold} seconds. Compare them with an earlier period; only a normal value replaces the average. Original total calls do not change.`;
  section.appendChild(note);

  const list = document.createElement("ul");
  list.className = "results-abnormal-list";
  payload.abnormal.forEach((item) => {
    const entry = document.createElement("li");
    entry.textContent = `${item.group}: ${item.host} — ${item.service}: ${item.avgResponseTime}s (${item.totalCalls} calls)`;
    list.appendChild(entry);
  });
  section.appendChild(list);

  const buttons = document.createElement("div");
  buttons.className = "results-actions";
  [50, 40, 30].forEach((minutes) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.comparisonMinutes = String(minutes);
    button.textContent = `Compare first ${minutes} min`;
    button.addEventListener("click", () => void compareResponseTimes(minutes));
    buttons.appendChild(button);
  });
  section.appendChild(buttons);
  return section;
}

function renderResult(payload) {
  output.replaceChildren();
  const summary = document.createElement("p");
  summary.className = "results-summary";
  const fileBit = payload.file ? ` File ${payload.file}.` : "";
  const windowBit =
    payload.window && payload.window.startAfter
      ? ` Report window ${payload.window.startAfter} to ${payload.window.endBefore}.`
      : "";
  summary.textContent = `Combined from ${payload.window.date} ${payload.window.from}–${payload.window.until}.${fileBit}${windowBit}`;
  output.appendChild(summary);
  output.appendChild(renderActions(payload));

  if (payload.comparisons?.length) {
    const updateSummary = document.createElement("p");
    updateSummary.className = "results-note";
    const updates = payload.comparisons.flatMap((comparison) => comparison.updates || []);
    updateSummary.textContent = updates.length
      ? `Adjusted averages: ${updates.map((item) => `${item.host}/${item.service} ${item.from}s → ${item.to}s`).join("; ")}.`
      : "Comparison completed; no reported average was replaced.";
    output.appendChild(updateSummary);
  } else if (!payload.corrections || payload.corrections.length === 0) {
    const note = document.createElement("p");
    note.className = "results-note";
    note.textContent =
      "Averages currently use the reported totals column.";
    output.appendChild(note);
  } else {
    const note = document.createElement("p");
    note.className = "results-note";
    note.textContent = `Corrected ${payload.corrections.length} response time${
      payload.corrections.length === 1 ? "" : "s"
    } before combining.`;
    output.appendChild(note);
  }

  output.appendChild(renderReport(payload.online));
  output.appendChild(renderReport(payload.api));
  const abnormalReview = renderAbnormalResponseTimes(payload);
  if (abnormalReview) output.appendChild(abnormalReview);

  if (payload.ignoredHosts && payload.ignoredHosts.length) {
    const extra = document.createElement("p");
    extra.className = "results-note";
    extra.textContent = `Ignored other servers in the file: ${payload.ignoredHosts.join(", ")}.`;
    output.appendChild(extra);
  }
  if (payload.notes && payload.notes.length) {
    const notes = document.createElement("p");
    notes.className = "results-note";
    notes.textContent = payload.notes.join(" ");
    output.appendChild(notes);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const date = document.getElementById("test-date").value;
  const from = document.getElementById("test-from").value;
  const until = document.getElementById("test-until").value;
  submitButton.disabled = true;
  setFeedback("Collecting Tuxedo stats from npe-services…", "running");
  output.replaceChildren();

  try {
    const response = await fetch(`${API_BASE_URL}/api/results/collate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ date, from, until })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.success) {
      throw new Error(payload.message || `Collate failed (HTTP ${response.status}).`);
    }
    currentPayload = payload;
    renderResult(payload);
    setFeedback("Online and API reports are ready.", "ok");
  } catch (error) {
    setFeedback(formatFetchFailureMessage(error), "error");
  } finally {
    submitButton.disabled = false;
  }
});
