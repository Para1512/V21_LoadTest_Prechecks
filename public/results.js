const form = document.getElementById("collate-form");
const feedback = document.getElementById("collate-feedback");
const output = document.getElementById("collate-output");
const submitButton = document.getElementById("collate-btn");

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

  if (!payload.corrections || payload.corrections.length === 0) {
    const note = document.createElement("p");
    note.className = "results-note";
    note.textContent =
      "No response-time corrections are configured. Averages use the reported totals column.";
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
    renderResult(payload);
    setFeedback("Online and API reports are ready.", "ok");
  } catch (error) {
    setFeedback(formatFetchFailureMessage(error), "error");
  } finally {
    submitButton.disabled = false;
  }
});
