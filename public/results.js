const form = document.getElementById("collate-form");
const feedback = document.getElementById("collate-feedback");
const output = document.getElementById("collate-output");
const submitButton = document.getElementById("collate-btn");
const processLog = document.getElementById("process-log");
const processLogList = document.getElementById("process-log-list");
const processElapsed = document.getElementById("process-elapsed");
let currentPayload = null;
let correctionDone = false;
let busy = false;
let elapsedTimer = null;

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

function timestamp() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function addLog(message, kind = "info") {
  processLog.hidden = false;
  processLogList.querySelectorAll(".process-step-running").forEach((item) => {
    item.classList.remove("process-step-running");
    item.classList.add("process-step-done");
  });
  const item = document.createElement("li");
  item.className = `process-step process-step-${kind}`;
  const time = document.createElement("span");
  time.className = "process-time";
  time.textContent = timestamp();
  const text = document.createElement("span");
  text.textContent = message;
  item.append(time, text);
  processLogList.appendChild(item);
  processLogList.scrollTop = processLogList.scrollHeight;
}

function addServerLog(lines) {
  (lines || []).forEach((line) => addLog(line, "done"));
}

function startBusy(message) {
  busy = true;
  submitButton.disabled = true;
  output.querySelectorAll("button, input").forEach((element) => {
    element.disabled = true;
  });
  addLog(message, "running");
  const started = Date.now();
  clearInterval(elapsedTimer);
  processElapsed.textContent = "0s";
  elapsedTimer = setInterval(() => {
    processElapsed.textContent = `${Math.round((Date.now() - started) / 1000)}s`;
  }, 1000);
}

function stopBusy() {
  busy = false;
  clearInterval(elapsedTimer);
  submitButton.disabled = false;
  processLogList.querySelectorAll(".process-step-running").forEach((item) => {
    item.classList.remove("process-step-running");
    item.classList.add("process-step-done");
  });
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload.success) {
    throw new Error(payload.message || `Request failed (HTTP ${response.status}).`);
  }
  return payload;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function actionButton(label, onClick, className) {
  const button = element("button", className, label);
  button.type = "button";
  button.addEventListener("click", onClick);
  return button;
}

async function copyText(text, label) {
  try {
    await navigator.clipboard.writeText(text);
    setFeedback(`${label} copied.`, "ok");
  } catch (_error) {
    setFeedback(`Could not access the clipboard. Use Download for the ${label} instead.`, "error");
  }
}

function downloadText(text, fileName) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = fileName;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

async function copyNpeReport(payload) {
  try {
    const response = await fetch(
      `${API_BASE_URL}/api/results/${encodeURIComponent(payload.reportId)}/raw?download=0`
    );
    if (!response.ok) throw new Error("The NPE report is no longer available. Run Collate again.");
    await copyText(await response.text(), "NPE report");
  } catch (error) {
    setFeedback(formatFetchFailureMessage(error), "error");
  }
}

function renderNpeActions(payload) {
  const group = element("div", "results-action-group");
  group.appendChild(element("span", "results-action-label", "NPE report"));
  if (payload.file) group.appendChild(element("span", "results-file", payload.file));
  const rawUrl = `${API_BASE_URL}/api/results/${encodeURIComponent(payload.reportId)}/raw`;
  const view = element("a", "results-action", "View");
  view.href = `${rawUrl}?download=0`;
  view.target = "_blank";
  view.rel = "noopener";
  const download = element("a", "results-action", "Download");
  download.href = rawUrl;
  group.append(view, download, actionButton("Copy", () => void copyNpeReport(payload)));
  return group;
}

function renderTable(headers, rows, highlightColumn, threshold) {
  const table = element("table", "disk-table results-table");
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  headers.forEach((label) => headRow.appendChild(element("th", "", label)));
  thead.appendChild(headRow);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  rows.forEach((values) => {
    const tr = document.createElement("tr");
    values.forEach((value, index) => {
      const cell = element("td", "", String(value));
      if (index === highlightColumn && Number(value) > threshold) cell.classList.add("results-high");
      tr.appendChild(cell);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  return table;
}

async function runComparison(body, label) {
  if (busy || !currentPayload?.reportId) return;
  setFeedback(`Correcting abnormal response times using the ${label} report…`, "running");
  startBusy(`Collecting the ${label} report from NPE services and comparing the same server and service…`);
  try {
    const payload = await postJson(
      `${API_BASE_URL}/api/results/${encodeURIComponent(currentPayload.reportId)}/compare`,
      body
    );
    addServerLog(payload.log);
    currentPayload = payload;
    correctionDone = true;
    const last = payload.comparisons[payload.comparisons.length - 1];
    const remaining = payload.abnormal.length;
    addLog(
      remaining
        ? `PET Enhanced reports are ready. ${remaining} response time(s) are still above ${payload.abnormalThreshold} sec.`
        : "PET Enhanced reports are ready.",
      remaining ? "warn" : "ok"
    );
    setFeedback(
      `Updated ${last ? last.corrected : 0} response time(s). PET Enhanced oln_report.txt and api_report.txt are ready.`,
      "ok"
    );
  } catch (error) {
    const message = formatFetchFailureMessage(error);
    addLog(message, "error");
    setFeedback(message, "error");
  } finally {
    stopBusy();
    renderResult(currentPayload);
  }
}

function renderCorrectionOptions(payload) {
  const wrap = element("div", "results-correction");
  wrap.appendChild(element("p", "results-option-title", "Choose a time frame to correct these response times"));

  const presets = element("div", "results-actions");
  (payload.presets || []).forEach((preset) => {
    const button = actionButton(`First ${preset.minutes} min`, () =>
      void runComparison({ minutes: preset.minutes }, `first ${preset.minutes} min`)
    );
    button.disabled = busy || !preset.available;
    if (!preset.available) button.title = "The test window is shorter than this.";
    presets.appendChild(button);
  });
  wrap.appendChild(presets);

  const custom = element("div", "results-actions results-custom-range");
  custom.appendChild(element("span", "results-action-label", "Custom range"));
  const from = document.createElement("input");
  from.type = "time";
  from.step = "60";
  from.value = payload.window.from;
  from.min = payload.window.from;
  from.max = payload.window.until;
  from.setAttribute("aria-label", "Custom range start time");
  const until = document.createElement("input");
  until.type = "time";
  until.step = "60";
  until.value = payload.window.until;
  until.min = payload.window.from;
  until.max = payload.window.until;
  until.setAttribute("aria-label", "Custom range end time");
  const compare = actionButton("Compare custom range", () => {
    if (!from.value || !until.value) {
      setFeedback("Choose a start and end time for the custom range.", "error");
      return;
    }
    void runComparison({ from: from.value, until: until.value }, `custom ${from.value}–${until.value}`);
  });
  custom.append(from, element("span", "results-range-sep", "to"), until, compare);
  wrap.appendChild(custom);
  wrap.querySelectorAll("input, button").forEach((node) => {
    if (busy) node.disabled = true;
  });
  return wrap;
}

function renderAbnormal(payload, initial) {
  const section = element("section", "results-abnormal");
  section.appendChild(
    element("h3", "", initial ? "Abnormal response times" : "Response times still above the limit")
  );
  section.appendChild(
    element(
      "p",
      "results-note",
      initial
        ? `${payload.abnormal.length} response time(s) are above ${payload.abnormalThreshold} sec. Pick a time frame: the portal collects that report and replaces a value only when the same server and service is at or below ${payload.abnormalThreshold} sec. Total calls stay from the full test report.`
        : `${payload.abnormal.length} response time(s) are still above ${payload.abnormalThreshold} sec. You can try another time frame.`
    )
  );

  let currentGroup = null;
  let rows = [];
  const flush = () => {
    if (!currentGroup) return;
    section.appendChild(element("h4", "results-server", currentGroup));
    section.appendChild(
      renderTable(["Tuxedo service", "Avg response time (sec)", "Total no of calls"], rows, 1, payload.abnormalThreshold)
    );
  };
  payload.abnormal.forEach((item) => {
    const label = `${item.group} · ${item.login}`;
    if (label !== currentGroup) {
      flush();
      currentGroup = label;
      rows = [];
    }
    rows.push([item.service, item.avgResponseTime, item.totalCalls]);
  });
  flush();

  section.appendChild(renderCorrectionOptions(payload));
  if (initial) {
    const skip = actionButton(
      "Skip correction and generate report",
      () => {
        correctionDone = true;
        addLog("Skipped correction. PET Enhanced reports use the full test report averages.", "ok");
        setFeedback("PET Enhanced oln_report.txt and api_report.txt are ready.", "ok");
        renderResult(currentPayload);
      },
      "results-skip"
    );
    skip.disabled = busy;
    section.appendChild(skip);
  }
  return section;
}

function renderReportCard(report) {
  const card = element("section", "results-report-card");
  const head = element("div", "results-report-head");
  const titleWrap = element("div");
  titleWrap.appendChild(element("h3", "", `PET Enhanced ${report.title}`));
  titleWrap.appendChild(element("span", "results-file", report.fileName));
  head.appendChild(titleWrap);
  const actions = element("div", "results-actions");
  const download = actionButton("Download", () => downloadText(report.text, report.fileName), "primary");
  download.disabled = busy;
  const copy = actionButton("Copy", () => void copyText(report.text, report.fileName));
  copy.disabled = busy;
  actions.append(download, copy);
  head.appendChild(actions);
  card.appendChild(head);

  const servers = report.servers.length ? report.servers.join(", ") : "none";
  card.appendChild(element("p", "results-hosts", `${report.rows.length} services · Servers: ${servers}`));
  if (report.missing.length) {
    card.appendChild(element("p", "results-missing", `Missing servers: ${report.missing.join(", ")}`));
  }

  if (report.rows.length) {
    const details = document.createElement("details");
    details.appendChild(element("summary", "", "Preview"));
    details.appendChild(
      renderTable(
        ["Tuxedo service", "Avg response time", "Total no of calls"],
        report.rows.map((row) => [row.service, row.avgResponseTime, row.totalCalls]),
        -1,
        Infinity
      )
    );
    card.appendChild(details);
  } else {
    card.appendChild(element("p", "results-empty", "No services were found for these servers."));
  }
  return card;
}

function renderResult(payload) {
  if (!payload) return;
  output.replaceChildren();
  const windowBit = payload.window.startAfter
    ? ` Report window ${payload.window.startAfter} to ${payload.window.endBefore}.`
    : "";
  output.appendChild(
    element(
      "p",
      "results-summary",
      `Test window ${payload.window.date} ${payload.window.from}–${payload.window.until}.${windowBit}`
    )
  );
  output.appendChild(renderNpeActions(payload));

  const hasAbnormal = payload.abnormal && payload.abnormal.length > 0;
  if (hasAbnormal && !correctionDone) {
    output.appendChild(renderAbnormal(payload, true));
  } else {
    const corrected = (payload.comparisons || []).reduce((sum, item) => sum + item.corrected, 0);
    let summary = `No response times were above ${payload.abnormalThreshold} sec.`;
    if (payload.originalAbnormalCount) {
      summary = corrected
        ? `Corrected ${corrected} of ${payload.originalAbnormalCount} abnormal response time(s). Total calls were kept from the full test report.`
        : `${payload.originalAbnormalCount} abnormal response time(s) were kept from the full test report.`;
    }
    output.appendChild(element("p", "results-note", summary));
    if (hasAbnormal) output.appendChild(renderAbnormal(payload, false));
    const cards = element("div", "results-report-grid");
    cards.append(renderReportCard(payload.reports.online), renderReportCard(payload.reports.api));
    output.appendChild(cards);
  }

  if (payload.ignoredHosts && payload.ignoredHosts.length) {
    output.appendChild(
      element("p", "results-note", `Ignored other servers in the file: ${payload.ignoredHosts.join(", ")}.`)
    );
  }
  if (payload.notes && payload.notes.length) {
    output.appendChild(element("p", "results-note", payload.notes.join(" ")));
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  const date = document.getElementById("test-date").value;
  const from = document.getElementById("test-from").value;
  const until = document.getElementById("test-until").value;
  currentPayload = null;
  correctionDone = false;
  output.replaceChildren();
  processLogList.replaceChildren();
  setFeedback("Collecting Tuxedo stats from NPE services…", "running");
  startBusy(`Collecting stats for ${date} ${from}–${until}. This can take a couple of minutes…`);

  try {
    const payload = await postJson(`${API_BASE_URL}/api/results/collate`, { date, from, until });
    addServerLog(payload.log);
    currentPayload = payload;
    if (payload.abnormal.length) {
      addLog("Waiting for you to choose a correction time frame.", "warn");
      setFeedback(
        `Found ${payload.abnormal.length} abnormal response time(s). Choose a time frame to correct them.`,
        "ok"
      );
    } else {
      addLog("PET Enhanced reports are ready.", "ok");
      setFeedback("PET Enhanced oln_report.txt and api_report.txt are ready.", "ok");
    }
  } catch (error) {
    const message = formatFetchFailureMessage(error);
    addLog(message, "error");
    setFeedback(message, "error");
  } finally {
    stopBusy();
    renderResult(currentPayload);
  }
});
