const tasksRoot = document.getElementById("tasks-root");
const emailFeedback = document.getElementById("email-feedback");
const hupEmailButton = document.getElementById("btn-hup-email");
const crmEmailButton = document.getElementById("btn-crm-email");

function getApiBaseUrl() {
  const { protocol } = window.location;
  // Page served by this portal (Node or Flask) — same host:port as the API. Using "" fixes
  // "Failed to fetch" when the VDI is opened as http://<hostname> or http://<ip> (not only localhost).
  if (protocol === "http:" || protocol === "https:") {
    return "";
  }
  // file:// or other — call the default local dev server
  return "http://127.0.0.1:3000";
}

const API_BASE_URL = getApiBaseUrl();
const SERVER_BOUNCE_PORTAL_URL = "http://nprapw000379.rci.rogers.ca/batch/create";
const SERVER_BOUNCE_PORTAL_LABEL = "NPR — Create batch / bounce request";

function apiUrl(path) {
  return `${API_BASE_URL}${path}`;
}

function formatFetchFailureMessage(error) {
  const msg = error && error.message ? String(error.message) : String(error);
  if (
    msg === "Failed to fetch" ||
    /networkerror|load failed|fetch.*aborted/i.test(msg)
  ) {
    return [
      msg,
      "Open the portal using the http:// hostname:port URL from the server console, not a file:// page.",
      "Keep the portal server running on that machine (npm start or python app.py)."
    ].join(" ");
  }
  return msg;
}

const taskDefinitions = [
  {
    id: 1,
    name: "Data Loading",
    purpose:
      "VTS pre-check + prep; on VTS1 failure auto Oracle extract/import. Edit queries via Oracle Queries."
  },
  {
    id: 2,
    name: "LG Disk Space Check",
    purpose: "Check disk space usage in LG systems.",
    runEndpoint: "/api/tasks/lg-disk-check/run"
  },
  {
    id: 3,
    name: "Servers Log Space Check",
    purpose:
      "Fetch log path usage for all TUX, PRAPI, and APILINK hosts via NPE services; warn above 60% and auto-clean above 70%.",
    runEndpoint: "/api/tasks/server-log-check/run"
  },
  {
    id: 4,
    name: "Server Bounce",
    purpose: "Restart required servers.",
    runEndpoint: "/api/tasks/server-bounce/run"
  },
  {
    id: 5,
    name: "Server Health Check",
    purpose:
      "Fetches PET1-V21 from NPE services in the portal backend and shows TUX OLN / TUX API status here when processing finishes.",
    runEndpoint: "/api/tasks/server-health-check/run"
  },
  {
    id: 6,
    name: "Trigger Sanity",
    purpose: "Run post-actions sanity validation (placeholder / simulated until real checks are added).",
    runEndpoint: "/api/tasks/trigger-sanity/run"
  }
];

let tasks = taskDefinitions.map((task) => ({
  id: task.id,
  name: task.name,
  purpose: task.purpose,
  runEndpoint: task.runEndpoint,
  status: "Pending",
  skipped: false,
  output: "",
  outputType: "text",
  outputData: null,
  expanded: false
}));

function getStatusClass(status) {
  if (status === "Running") return "status-running";
  if (status === "In Progress") return "status-running";
  if (status === "Completed") return "status-completed";
  return "status-pending";
}

function renderTasks() {
  tasksRoot.innerHTML = "";

  tasks.forEach((task) => {
    const card = document.createElement("article");
    card.className = "task-card";

    const header = document.createElement("div");
    header.className = "task-header";

    const titleWrap = document.createElement("div");
    titleWrap.className = "task-title-wrap";
    const titleRow = document.createElement("div");
    titleRow.className = "task-title-row";
    const indexBadge = document.createElement("span");
    indexBadge.className = "task-index";
    indexBadge.textContent = String(task.id).padStart(2, "0");
    const title = document.createElement("div");
    title.className = "task-title";
    title.textContent = task.name;
    titleRow.appendChild(indexBadge);
    titleRow.appendChild(title);
    const purpose = document.createElement("div");
    purpose.className = "task-purpose";
    purpose.textContent = task.purpose;
    titleWrap.appendChild(titleRow);
    titleWrap.appendChild(purpose);

    const meta = document.createElement("div");
    meta.className = "task-meta";

    const statusPill = document.createElement("span");
    statusPill.className = `status-pill ${getStatusClass(task.status)}`;
    statusPill.textContent = task.status;
    meta.appendChild(statusPill);

    header.appendChild(titleWrap);
    header.appendChild(meta);
    card.appendChild(header);

    const actions = document.createElement("div");
    actions.className = "task-actions";

    const runButton = document.createElement("button");
    runButton.className = "run";
    runButton.textContent = "RUN";
    const bounceFormActive =
      task.id === 4 && task.outputType === "server-bounce-form" && task.status === "In Progress";
    runButton.disabled = task.status === "Running" || bounceFormActive;
    runButton.addEventListener("click", () => runTask(task.id));

    const skipButton = document.createElement("button");
    skipButton.className = "skip";
    skipButton.textContent = "SKIP";
    skipButton.disabled = task.status === "Running";
    skipButton.addEventListener("click", () => skipTask(task.id));

    actions.appendChild(runButton);
    actions.appendChild(skipButton);
    if (task.id === 1) {
      const dbBtn = document.createElement("button");
      dbBtn.type = "button";
      dbBtn.textContent = "Test Oracle DB";
      dbBtn.disabled = task.status === "Running";
      dbBtn.addEventListener("click", () => void runTask1OracleTest());
      actions.appendChild(dbBtn);
      const qBtn = document.createElement("button");
      qBtn.type = "button";
      qBtn.textContent = "Oracle Queries";
      qBtn.disabled = task.status === "Running";
      qBtn.addEventListener("click", () => void openOracleQueriesEditor());
      actions.appendChild(qBtn);
    }
    card.appendChild(actions);

    if (
      task.output ||
      task.outputType === "server-bounce-form" ||
      task.outputType === "server-log-space" ||
      task.outputType === "server-health-pet" ||
      task.outputType === "data-loading-vts" ||
      task.outputType === "oracle-connection-test"
    ) {
      const dropdownToggle = document.createElement("div");
      dropdownToggle.className = "dropdown-toggle";
      dropdownToggle.textContent = task.expanded ? "Hide Output ▲" : "Show Output ▼";
      dropdownToggle.addEventListener("click", () => {
        updateTask(task.id, { expanded: !task.expanded });
      });
      card.appendChild(dropdownToggle);

      const outputWrap = document.createElement("div");
      outputWrap.className = `${task.expanded ? "" : "hidden"}`.trim();

      if (task.outputType === "lg-disk-table" && task.outputData?.results) {
        outputWrap.appendChild(renderLgDiskTable(task.outputData));
      } else if (task.outputType === "server-log-space" && task.outputData) {
        outputWrap.appendChild(renderServerLogSpace(task.outputData, task));
      } else if (task.outputType === "server-health-pet" && task.outputData) {
        outputWrap.appendChild(renderServerHealthPet(task.outputData));
      } else if (task.outputType === "data-loading-vts" && task.outputData) {
        outputWrap.appendChild(renderDataLoadingVts(task.outputData));
      } else if (task.outputType === "oracle-connection-test" && task.outputData) {
        outputWrap.appendChild(renderOracleConnectionTest(task.outputData));
      } else if (task.outputType === "server-bounce-form" && task.outputData?.portalUrl) {
        outputWrap.appendChild(renderServerBounceForm(task));
      } else {
        const outputBox = document.createElement("pre");
        outputBox.className = "task-output";
        outputBox.textContent = task.output;
        outputWrap.appendChild(outputBox);
      }

      card.appendChild(outputWrap);
    }

    tasksRoot.appendChild(card);
  });
}

function getFreeSpaceClass(value) {
  if (typeof value !== "number") return "disk-free-error";
  if (value >= 50) return "disk-free-green";
  if (value >= 15) return "disk-free-yellow";
  return "disk-free-red";
}

async function openShare(machineName, drive) {
  const path = `\\\\${machineName}\\${drive}$`;
  try {
    window.open(path);
  } catch (_error) {
    // Browser may block UNC paths; backend fallback still runs.
  }

  try {
    await fetch(apiUrl("/api/open-share"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ machineName, drive })
    });
  } catch (_error) {
    // Ignore fallback errors in UI to keep flow simple.
  }
}

function renderLgDiskTable(outputData) {
  const root = document.createElement("div");
  root.className = "task-output";

  const title = document.createElement("div");
  title.className = "disk-header";
  title.textContent = outputData.header || "LG Disk Space Check Results";
  root.appendChild(title);

  if (outputData.hasCriticalWarning) {
    const banner = document.createElement("div");
    banner.className = "disk-warning-banner";
    banner.textContent = "Warning: One or more servers have less than 20 GB free space.";
    root.appendChild(banner);
  }

  if (outputData.results.some((r) => r.queryError)) {
    const hint = document.createElement("div");
    hint.className = "disk-warning-banner";
    hint.textContent =
      "Remote disk query failed for one or more rows. Read the error under each machine name. Python uses DCOM WMI (wmi/pywin32, like a classic Win32 app). Node uses Get-WmiObject (DCOM), not WinRM. You still need RPC/WMI allowed from this PC to each server, and an account with remote WMI rights (set LG_WMI_USER / LG_WMI_PASSWORD / LG_WMI_DOMAIN in .env if needed).";
    root.appendChild(hint);
  }

  const table = document.createElement("table");
  table.className = "disk-table";
  table.innerHTML = `
    <thead>
      <tr>
        <th>Machine Name</th>
        <th>C: Total (GB)</th>
        <th>C: Free (GB)</th>
        <th>D: Total (GB)</th>
        <th>D: Free (GB)</th>
      </tr>
    </thead>
    <tbody></tbody>
  `;

  const tbody = table.querySelector("tbody");
  outputData.results.forEach((row) => {
    const tr = document.createElement("tr");

    const machineCell = document.createElement("td");
    machineCell.appendChild(document.createTextNode(row.machine || row.ip || "Unknown"));
    if (row.queryError) {
      const errEl = document.createElement("div");
      errEl.className = "disk-row-error";
      errEl.textContent = row.queryError;
      machineCell.appendChild(errEl);
    }
    tr.appendChild(machineCell);

    const cTotal = document.createElement("td");
    cTotal.textContent = typeof row.c_total === "number" ? row.c_total.toFixed(2) : String(row.c_total);
    tr.appendChild(cTotal);

    const cFree = document.createElement("td");
    cFree.className = `disk-free-cell ${getFreeSpaceClass(row.c_free)}`;
    cFree.textContent = typeof row.c_free === "number" ? row.c_free.toFixed(2) : String(row.c_free);
    if (typeof row.c_free === "number") {
      cFree.title = "Click to open \\\\<machine>\\c$";
      cFree.addEventListener("click", () => openShare(row.machine, "c"));
    } else {
      cFree.title = "Unavailable until disk query succeeds";
      cFree.classList.add("disk-free-no-click");
    }
    tr.appendChild(cFree);

    const dTotal = document.createElement("td");
    dTotal.textContent = typeof row.d_total === "number" ? row.d_total.toFixed(2) : String(row.d_total);
    tr.appendChild(dTotal);

    const dFree = document.createElement("td");
    dFree.className = `disk-free-cell ${getFreeSpaceClass(row.d_free)}`;
    dFree.textContent = typeof row.d_free === "number" ? row.d_free.toFixed(2) : String(row.d_free);
    if (typeof row.d_free === "number") {
      dFree.title = "Click to open \\\\<machine>\\d$";
      dFree.addEventListener("click", () => openShare(row.machine, "d"));
    } else {
      dFree.title = "Unavailable until disk query succeeds";
      dFree.classList.add("disk-free-no-click");
    }
    tr.appendChild(dFree);

    tbody.appendChild(tr);
  });

  root.appendChild(table);
  return root;
}

function renderOracleConnectionTest(od) {
  const root = document.createElement("div");
  root.className = "task-output data-loading-vts";
  const intro = document.createElement("p");
  intro.className = "data-loading-intro";
  intro.textContent = od.message || (od.connected ? "Oracle connection OK" : "Oracle connection failed");
  root.appendChild(intro);
  if (od.config) {
    const cfg = document.createElement("p");
    cfg.className = "data-loading-vts-meta";
    cfg.textContent = `Target: ${od.config.user}@${od.config.host}:${od.config.port}/${od.config.service_name}`;
    root.appendChild(cfg);
  }
  if (od.checkpoints?.length) {
    const table = document.createElement("table");
    table.className = "data-loading-vts-table";
    const thead = document.createElement("thead");
    const hr = document.createElement("tr");
    ["Checkpoint", "Result", "Detail"].forEach((label) => {
      const th = document.createElement("th");
      th.textContent = label;
      hr.appendChild(th);
    });
    thead.appendChild(hr);
    table.appendChild(thead);
    const tbody = document.createElement("tbody");
    od.checkpoints.forEach((row) => {
      const tr = document.createElement("tr");
      tr.className = row.passed ? "data-loading-vts-row-pass" : "data-loading-vts-row-fail";
      [row.name, row.passed ? "PASSED" : "FAILED", row.detail ?? "—"].forEach((cell, idx) => {
        const td = document.createElement("td");
        td.textContent = String(cell);
        if (idx === 1) {
          td.className = row.passed ? "data-loading-vts-safe" : "data-loading-vts-unsafe";
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    root.appendChild(table);
  }
  if (od.messages?.length) {
    const logTitle = document.createElement("div");
    logTitle.className = "log-space-pro-subtitle";
    logTitle.textContent = "Log";
    root.appendChild(logTitle);
    const pre = document.createElement("pre");
    pre.className = "task-output";
    pre.textContent = od.messages.join("\n");
    root.appendChild(pre);
  }
  return root;
}

function renderVtsCountTable(title, block) {
  const section = document.createElement("div");
  section.className = "data-loading-vts-block";
  const h = document.createElement("div");
  h.className = "disk-header";
  h.textContent = title;
  section.appendChild(h);
  const status = document.createElement("p");
  status.className = block?.safe ? "data-loading-vts-safe" : "data-loading-vts-unsafe";
  status.textContent = block?.safe ? "Passed" : "Failed";
  section.appendChild(status);
  if (block?.checks?.length) {
    const table = document.createElement("table");
    table.className = "data-loading-vts-table";
    const thead = document.createElement("thead");
    const hr = document.createElement("tr");
    ["Column", "Count", "Status"].forEach((label) => {
      const th = document.createElement("th");
      th.textContent = label;
      hr.appendChild(th);
    });
    thead.appendChild(hr);
    table.appendChild(thead);
    const tbody = document.createElement("tbody");
    block.checks.forEach((row) => {
      const tr = document.createElement("tr");
      tr.className = row.ok ? "data-loading-vts-row-pass" : "data-loading-vts-row-fail";
      const statusText = row.ok ? "Passed" : "Failed";
      [row.column, row.count ?? "—", statusText].forEach((cell, idx) => {
        const td = document.createElement("td");
        td.textContent = String(cell);
        if (idx === 2) {
          td.className = row.ok ? "data-loading-vts-safe" : "data-loading-vts-unsafe";
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    section.appendChild(table);
  }
  return section;
}

function renderVtsPrecheckSummary(precheck) {
  const wrap = document.createElement("div");
  wrap.className = "data-loading-vts-block";
  const h = document.createElement("div");
  h.className = "disk-header";
  h.textContent = "Pre-check summary";
  wrap.appendChild(h);
  const passed = document.createElement("p");
  passed.className = "data-loading-vts-safe";
  passed.textContent = `Passed (${precheck.passedColumns?.length || 0}): ${
    precheck.passedColumns?.length ? precheck.passedColumns.join(", ") : "none"
  }`;
  wrap.appendChild(passed);
  const failed = document.createElement("p");
  failed.className = "data-loading-vts-unsafe";
  failed.textContent = `Failed (${precheck.failedColumns?.length || 0}): ${
    precheck.failedColumns?.length ? precheck.failedColumns.join(", ") : "none"
  }`;
  wrap.appendChild(failed);
  if (precheck.messages?.length) {
    const logTitle = document.createElement("div");
    logTitle.className = "log-space-pro-subtitle";
    logTitle.textContent = "Pre-check log";
    wrap.appendChild(logTitle);
    const pre = document.createElement("pre");
    pre.className = "task-output";
    pre.textContent = precheck.messages.join("\n");
    wrap.appendChild(pre);
  }
  return wrap;
}

function renderVtsCountsTable(title, countsObj) {
  const section = document.createElement("div");
  section.className = "data-loading-vts-block";
  const h = document.createElement("div");
  h.className = "disk-header";
  h.textContent = title;
  section.appendChild(h);
  if (!countsObj || typeof countsObj !== "object") {
    return section;
  }
  const table = document.createElement("table");
  table.className = "data-loading-vts-table";
  const thead = document.createElement("thead");
  const hr = document.createElement("tr");
  ["Column", "Count"].forEach((label) => {
    const th = document.createElement("th");
    th.textContent = label;
    hr.appendChild(th);
  });
  thead.appendChild(hr);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  Object.entries(countsObj).forEach(([col, count]) => {
    const tr = document.createElement("tr");
    const tdCol = document.createElement("td");
    tdCol.textContent = col;
    const tdCount = document.createElement("td");
    tdCount.textContent = count == null ? "—" : String(count);
    tr.appendChild(tdCol);
    tr.appendChild(tdCount);
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  section.appendChild(table);
  return section;
}

function renderVtsValidationSection(validationDetails) {
  const wrap = document.createElement("div");
  wrap.className = "data-loading-vts-block";
  const h = document.createElement("div");
  h.className = "disk-header";
  h.textContent = "Validation details";
  wrap.appendChild(h);
  if (!validationDetails?.length) {
    const empty = document.createElement("p");
    empty.className = "data-loading-vts-meta";
    empty.textContent = "No validation results yet.";
    wrap.appendChild(empty);
    return wrap;
  }
  validationDetails.forEach((v) => {
    const block = document.createElement("div");
    block.className = "data-loading-vts-validation";
    const status = document.createElement("p");
    status.className = v.passed ? "data-loading-vts-safe" : "data-loading-vts-unsafe";
    status.textContent = `${v.name}: ${v.passed ? "Passed" : "Failed"}`;
    block.appendChild(status);
    if (v.rows?.length) {
      const table = document.createElement("table");
      table.className = "data-loading-vts-table";
      const thead = document.createElement("thead");
      const hr = document.createElement("tr");
      ["Column", "Count", "Status"].forEach((label) => {
        const th = document.createElement("th");
        th.textContent = label;
        hr.appendChild(th);
      });
      thead.appendChild(hr);
      table.appendChild(thead);
      const tbody = document.createElement("tbody");
      v.rows.forEach((row) => {
        const rowPassed =
          row.passed !== undefined && row.passed !== null
            ? row.passed
            : v.passed;
        const tr = document.createElement("tr");
        tr.className = rowPassed ? "data-loading-vts-row-pass" : "data-loading-vts-row-fail";
        const tdCol = document.createElement("td");
        tdCol.textContent = row.column;
        const tdCount = document.createElement("td");
        tdCount.textContent = row.count == null ? "—" : String(row.count);
        const tdStatus = document.createElement("td");
        tdStatus.textContent = rowPassed ? "Passed" : "Failed";
        tdStatus.className = rowPassed ? "data-loading-vts-safe" : "data-loading-vts-unsafe";
        tr.appendChild(tdCol);
        tr.appendChild(tdCount);
        tr.appendChild(tdStatus);
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      block.appendChild(table);
    }
    wrap.appendChild(block);
  });
  return wrap;
}

function renderVtsRemediationSection(remediationRuns) {
  const wrap = document.createElement("div");
  wrap.className = "data-loading-vts-block";
  const h = document.createElement("div");
  h.className = "disk-header";
  h.textContent = "VTS remediation (Oracle → TXT → import)";
  wrap.appendChild(h);
  const runs = Array.isArray(remediationRuns) ? remediationRuns : [remediationRuns];
  runs.forEach((run) => {
    if (!run?.results?.length) return;
    run.results.forEach((r) => {
      const block = document.createElement("div");
      block.className = "data-loading-vts-validation";
      const status = document.createElement("p");
      const ok = !!r.success;
      status.className = ok ? "data-loading-vts-safe" : "data-loading-vts-unsafe";
      status.textContent = `${r.label || r.issue}: ${ok ? "Passed" : "Failed"}`;
      block.appendChild(status);
      if (r.rowCount != null) {
        const rows = document.createElement("p");
        rows.className = "data-loading-vts-meta";
        rows.textContent = `Rows: ${r.rowCount}${r.txtFile ? ` → ${r.txtFile}` : ""}`;
        block.appendChild(rows);
      }
      if (r.message) {
        const msg = document.createElement("p");
        msg.className = "data-loading-vts-meta";
        msg.textContent = r.message;
        block.appendChild(msg);
      }
      if (r.imports?.length) {
        r.imports.forEach((slot) => {
          const line = document.createElement("p");
          line.className = "data-loading-vts-meta";
          const impSlot = slot.import || {};
          const up = impSlot.upload || {};
          line.textContent = `${slot.vts}: ${slot.rowCount ?? "?"} rows → ${slot.txtFile || ""}${
            up.strategy ? ` (${up.strategy})` : ""
          }`;
          block.appendChild(line);
        });
      }
      const imp = r.import;
      const upload = imp?.upload || imp?.combinedResult?.upload || {};
      const importUrl = imp?.upload?.importUrl || imp?.combinedResult?.importUrl || upload.importUrl;
      if (importUrl) {
        const job = document.createElement("p");
        job.className = "data-loading-vts-meta";
        job.textContent = upload.jobId
          ? `Import: ${importUrl} → job ${upload.jobId}`
          : `Import: ${importUrl}`;
        block.appendChild(job);
      }
      if (upload.strategy) {
        const strat = document.createElement("p");
        strat.className = "data-loading-vts-meta";
        strat.textContent = `Upload: ${upload.strategy}`;
        block.appendChild(strat);
      }
      if (upload.responseBody && !r.success) {
        const resp = document.createElement("p");
        resp.className = "data-loading-vts-unsafe";
        resp.textContent = `VTS response: ${upload.responseBody}`;
        block.appendChild(resp);
      }
      if (imp?.columnResults?.length) {
        imp.columnResults.forEach((cr) => {
          const attempts = cr.attempts || [cr];
          const last = attempts[attempts.length - 1];
          if (last?.jobId) {
            const job = document.createElement("p");
            job.className = "data-loading-vts-meta";
            job.textContent = `${cr.column || "column"}: import job ${last.jobId}`;
            block.appendChild(job);
          }
        });
      }
      if (imp?.verification?.rows?.length) {
        imp.verification.rows.forEach((row) => {
          const v = document.createElement("p");
          v.className = row.passed ? "data-loading-vts-safe" : "data-loading-vts-unsafe";
          v.textContent = `${row.column}: expected ${row.expected}, actual ${row.actual ?? "missing"}`;
          block.appendChild(v);
        });
      }
      wrap.appendChild(block);
    });
  });
  return wrap;
}

function renderVtsClearSection(clearDetails) {
  const wrap = document.createElement("div");
  wrap.className = "data-loading-vts-block";
  const h = document.createElement("div");
  h.className = "disk-header";
  h.textContent = "Removing old test data";
  wrap.appendChild(h);
  if (!clearDetails?.length) {
    const empty = document.createElement("p");
    empty.className = "data-loading-vts-meta";
    empty.textContent = "No columns cleared (validation may have failed before clear step).";
    wrap.appendChild(empty);
    return wrap;
  }
  const table = document.createElement("table");
  table.className = "data-loading-vts-table";
  const thead = document.createElement("thead");
  const hr = document.createElement("tr");
  ["VTS", "Column", "Status"].forEach((label) => {
    const th = document.createElement("th");
    th.textContent = label;
    hr.appendChild(th);
  });
  thead.appendChild(hr);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  clearDetails.forEach((row) => {
    const tr = document.createElement("tr");
    const passed = row.success !== false;
    tr.className = passed ? "data-loading-vts-row-pass" : "data-loading-vts-row-fail";
    const statusText = passed ? "Passed" : "Failed";
    [row.vts, row.column, statusText].forEach((cell, idx) => {
      const td = document.createElement("td");
      td.textContent = String(cell);
      if (idx === 2) {
        td.className = passed ? "data-loading-vts-safe" : "data-loading-vts-unsafe";
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  return wrap;
}

function renderCleanValidationSummary(summary) {
  const wrap = document.createElement("div");
  wrap.className = "data-loading-vts-block";
  const h = document.createElement("div");
  h.className = "disk-header";
  h.textContent = "Validations";
  wrap.appendChild(h);

  if (!summary?.length) {
    const empty = document.createElement("p");
    empty.className = "data-loading-vts-meta";
    empty.textContent = "No validation results.";
    wrap.appendChild(empty);
    return wrap;
  }

  const table = document.createElement("table");
  table.className = "data-loading-vts-table";
  const thead = document.createElement("thead");
  const hr = document.createElement("tr");
  ["Group", "Check", "Count / Rule", "Status"].forEach((label) => {
    const th = document.createElement("th");
    th.textContent = label;
    hr.appendChild(th);
  });
  thead.appendChild(hr);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  summary.forEach((row) => {
    const tr = document.createElement("tr");
    tr.className = row.passed ? "data-loading-vts-row-pass" : "data-loading-vts-row-fail";
    const ruleText =
      row.required != null && row.count != null && typeof row.required === "number"
        ? `${row.count} (need >= ${row.required})`
        : row.required != null
          ? String(row.required)
          : row.count != null
            ? String(row.count)
            : "—";
    [
      row.group || "—",
      row.name || "—",
      ruleText,
      row.passed ? "PASSED" : "FAILED"
    ].forEach((cell, idx) => {
      const td = document.createElement("td");
      td.textContent = String(cell);
      if (idx === 3) {
        td.className = row.passed ? "data-loading-vts-safe" : "data-loading-vts-unsafe";
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  return wrap;
}

function renderGuidanceSection(guidance) {
  const wrap = document.createElement("div");
  wrap.className = "data-loading-guidance";
  const h = document.createElement("div");
  h.className = "disk-header";
  h.textContent = "What to do next";
  wrap.appendChild(h);
  (guidance || []).forEach((g) => {
    const card = document.createElement("div");
    card.className = "data-loading-guidance-card";
    const title = document.createElement("div");
    title.className = "data-loading-guidance-title";
    title.textContent = g.label || g.id || "Trigger";
    card.appendChild(title);
    const msg = document.createElement("p");
    msg.className = "data-loading-guidance-msg";
    msg.textContent = g.message || "";
    card.appendChild(msg);
    wrap.appendChild(card);
  });
  return wrap;
}

function openTask1LogsModal(od) {
  const existing = document.getElementById("task1-logs-overlay");
  if (existing) existing.remove();

  const overlay = document.createElement("div");
  overlay.id = "task1-logs-overlay";
  overlay.className = "oracle-queries-overlay";
  const panel = document.createElement("div");
  panel.className = "oracle-queries-panel task1-logs-panel";

  const title = document.createElement("h3");
  title.textContent = "Task 1 — detailed logs";
  panel.appendChild(title);

  const hint = document.createElement("p");
  hint.className = "data-loading-vts-meta";
  hint.textContent =
    "Column counts, remediation details, and full run log. Close when done.";
  panel.appendChild(hint);

  const prep = od.prep;
  if (od.precheck) {
    const preHdr = document.createElement("div");
    preHdr.className = "disk-header";
    preHdr.textContent = "Pre-check counts";
    panel.appendChild(preHdr);
    if (od.precheck.vts1) {
      panel.appendChild(renderVtsCountTable("VTS 1 (port 2244)", od.precheck.vts1));
    }
    if (od.precheck.vts2) {
      panel.appendChild(renderVtsCountTable("VTS 2 (port 2245)", od.precheck.vts2));
    }
  }

  if (prep?.dataDetails) {
    const dataHdr = document.createElement("div");
    dataHdr.className = "disk-header";
    dataHdr.textContent = "Data details (counts per column)";
    panel.appendChild(dataHdr);
    if (prep.dataDetails.vts1) {
      panel.appendChild(
        renderVtsCountsTable(prep.dataDetails.vts1.label || "VTS 1", prep.dataDetails.vts1.counts)
      );
    }
    if (prep.dataDetails.vts2) {
      panel.appendChild(
        renderVtsCountsTable(prep.dataDetails.vts2.label || "VTS 2", prep.dataDetails.vts2.counts)
      );
    }
  }

  if (prep?.validationDetails?.length) {
    panel.appendChild(renderVtsValidationSection(prep.validationDetails));
  }

  if (od.remediation?.length) {
    panel.appendChild(renderVtsRemediationSection(od.remediation));
  }

  if (prep && (prep.clearDetails?.length || prep.prepCompleted)) {
    panel.appendChild(renderVtsClearSection(prep.clearDetails || []));
  }

  const logLines = od.messages?.length
    ? od.messages
    : prep?.messages?.length
      ? prep.messages
      : [];
  if (logLines.length) {
    const logTitle = document.createElement("div");
    logTitle.className = "log-space-pro-subtitle";
    logTitle.textContent = "Full log";
    panel.appendChild(logTitle);
    const pre = document.createElement("pre");
    pre.className = "task-output task1-logs-pre";
    pre.textContent = logLines.join("\n");
    panel.appendChild(pre);
  }

  const actions = document.createElement("div");
  actions.className = "oracle-queries-actions";
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.textContent = "Close";
  closeBtn.addEventListener("click", () => overlay.remove());
  actions.appendChild(closeBtn);
  panel.appendChild(actions);

  overlay.appendChild(panel);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.remove();
  });
  document.body.appendChild(overlay);
}

function renderDataLoadingVts(od) {
  const root = document.createElement("div");
  root.className = "task-output data-loading-vts";
  const prep = od.prep;
  const guidance = od.guidance || [];
  const failed =
    od.phase === "insufficient" ||
    od.phase === "error" ||
    guidance.length > 0 ||
    (od.validationSummary || []).some((r) => !r.passed);

  const intro = document.createElement("p");
  intro.className = "data-loading-intro";
  if (od.phase === "checking") {
    intro.textContent = "Checking VTS column counts…";
  } else if (od.phase === "prep-done") {
    intro.textContent = "Task 1 completed — all validations passed.";
  } else if (failed) {
    intro.textContent =
      "Some validations still failed after the data reload. Follow the guidance below.";
  } else {
    intro.textContent = od.message || prep?.message || "VTS step finished.";
  }
  root.appendChild(intro);

  if (guidance.length) {
    root.appendChild(renderGuidanceSection(guidance));
  }

  if (od.validationSummary?.length) {
    root.appendChild(renderCleanValidationSummary(od.validationSummary));
  } else if (prep?.validationDetails?.length) {
    // Fallback if older payload without summary
    root.appendChild(renderVtsValidationSection(prep.validationDetails));
  }

  if (prep?.prepCompleted) {
    const done = document.createElement("p");
    done.className = "data-loading-vts-safe";
    done.textContent = "Old test data has been removed — VTS prep completed.";
    root.appendChild(done);
  }

  const hasLogData =
    failed ||
    od.messages?.length ||
    od.remediation?.length ||
    od.precheck ||
    prep?.dataDetails;
  if (hasLogData) {
    const bar = document.createElement("div");
    bar.className = "data-loading-logs-bar";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "data-loading-logs-btn";
    btn.textContent = failed ? "View logs (counts & details)" : "View logs";
    btn.addEventListener("click", () => openTask1LogsModal(od));
    bar.appendChild(btn);
    root.appendChild(bar);
  }

  return root;
}

function healthPetStatusClass(normalized) {
  if (normalized === "up") return "log-usage-tier-green";
  if (normalized === "down") return "log-usage-tier-red";
  return "log-usage-tier-muted";
}

function healthPetAppendTableSection(root, title, rows) {
  const section = document.createElement("section");
  section.className = "log-space-table-section";

  const h = document.createElement("h3");
  h.className = "log-space-table-section-title";
  h.textContent = title;
  section.appendChild(h);

  const wrap = document.createElement("div");
  wrap.className = "log-space-table-scroll";
  const table = document.createElement("table");
  table.className = "log-space-data-table";

  const thead = document.createElement("thead");
  const hr = document.createElement("tr");
  ["Server", "Status"].forEach((label) => {
    const th = document.createElement("th");
    th.textContent = label;
    hr.appendChild(th);
  });
  thead.appendChild(hr);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 2;
    td.className = "log-space-table-empty";
    td.textContent = "No hosts in this group.";
    tr.appendChild(td);
    tbody.appendChild(tr);
  } else {
    rows.forEach((s) => {
      const tr = document.createElement("tr");

      const tdHost = document.createElement("td");
      tdHost.className = "log-space-td-host";
      tdHost.textContent = s.server || "—";
      tr.appendChild(tdHost);

      const tdStatus = document.createElement("td");
      tdStatus.className = "log-space-td-usage";
      const pill = document.createElement("span");
      pill.className = `log-space-usage-pill ${healthPetStatusClass(s.normalized)}`;
      pill.textContent = s.status || "—";
      tdStatus.appendChild(pill);
      tr.appendChild(tdStatus);

      tbody.appendChild(tr);
    });
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  section.appendChild(wrap);
  root.appendChild(section);
}

function renderServerHealthPet(outputData) {
  const root = document.createElement("div");
  root.className = "task-output log-space-root log-space-pro";

  const head = document.createElement("div");
  head.className = "log-space-pro-head";
  const title = document.createElement("div");
  title.className = "log-space-pro-title";
  title.textContent = outputData.header || "Server Health Check (PET1-V21)";
  const sub = document.createElement("div");
  sub.className = "log-space-pro-subtitle";
  sub.textContent =
    "Parsed from the PET1-V21 CGI response. TUX OLN (COLAP hosts) and TUX API hosts are grouped like Task 3 (TUX vs PRAPI).";
  head.appendChild(title);
  head.appendChild(sub);
  root.appendChild(head);

  const metaStrip = document.createElement("div");
  metaStrip.className = "log-space-refresh-strip";
  const parts = [];
  if (outputData.petUrl) {
    const link = document.createElement("a");
    link.href = outputData.petUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = outputData.petUrl;
    metaStrip.appendChild(document.createTextNode("Source "));
    metaStrip.appendChild(link);
  }
  if (outputData.checkedAt) {
    try {
      parts.push(`Checked ${new Date(outputData.checkedAt).toLocaleString()}`);
    } catch {
      /* ignore */
    }
  }
  if (parts.length) {
    if (outputData.petUrl) metaStrip.appendChild(document.createTextNode(" · "));
    metaStrip.appendChild(document.createTextNode(parts.join("")));
  }
  if (outputData.petUrl || parts.length) root.appendChild(metaStrip);

  const warnings = outputData.warnings || [];
  if (warnings.length) {
    const fw = document.createElement("div");
    fw.className = "log-space-final-warning";
    fw.setAttribute("role", "alert");
    const fwTitle = document.createElement("div");
    fwTitle.className = "log-space-final-warning-label";
    fwTitle.textContent = "Server status warnings";
    const fwBody = document.createElement("div");
    fwBody.className = "log-space-final-warning-text";
    fwBody.textContent = warnings.join("\n");
    fw.appendChild(fwTitle);
    fw.appendChild(fwBody);
    root.appendChild(fw);
  } else if (outputData.allUp) {
    const ok = document.createElement("div");
    ok.className = "health-pet-all-clear";
    ok.textContent = "All monitored servers report UP — task completed.";
    root.appendChild(ok);
  }

  if (outputData.hasUnknown && !warnings.length && !outputData.allUp) {
    const soft = document.createElement("div");
    soft.className = "log-space-soft-warning";
    soft.textContent =
      "Some hosts have an unrecognized status string; confirm on the PET1-V21 page or table below.";
    root.appendChild(soft);
  }

  const dashboard = document.createElement("div");
  dashboard.className = "log-space-dashboard";

  const tablesShell = document.createElement("div");
  tablesShell.className = "log-space-tables-shell";

  const servers = outputData.servers || [];
  if (servers.length && !outputData.streaming) {
    healthPetAppendTableSection(
      tablesShell,
      "TUX",
      servers.filter((s) => s.group === "TUX")
    );
    healthPetAppendTableSection(
      tablesShell,
      "PRAPI",
      servers.filter((s) => s.group === "PRAPI")
    );
  } else {
    const pending = document.createElement("div");
    pending.className = "log-space-table-pending";
    pending.textContent = outputData.streaming
      ? "Fetching PET1-V21 status…"
      : "No server rows loaded.";
    tablesShell.appendChild(pending);
  }

  dashboard.appendChild(tablesShell);
  root.appendChild(dashboard);

  return root;
}

function logSpaceUsageTierClass(usage) {
  if (typeof usage !== "number") return "log-usage-tier-muted";
  if (usage >= 0 && usage <= 50) return "log-usage-tier-green";
  if (usage >= 51 && usage <= 69) return "log-usage-tier-yellow";
  return "log-usage-tier-red";
}

function logSpaceServersForGroup(servers, groupKey) {
  const g = groupKey.toUpperCase();
  return (servers || []).filter((s) => String(s.group || "").toUpperCase() === g);
}

function logSpaceAppendTableSection(root, title, rows) {
  const section = document.createElement("section");
  section.className = "log-space-table-section";

  const h = document.createElement("h3");
  h.className = "log-space-table-section-title";
  h.textContent = title;
  section.appendChild(h);

  const wrap = document.createElement("div");
  wrap.className = "log-space-table-scroll";
  const table = document.createElement("table");
  table.className = "log-space-data-table";

  const thead = document.createElement("thead");
  const hr = document.createElement("tr");
  ["Server", "Log path usage"].forEach((label) => {
    const th = document.createElement("th");
    th.textContent = label;
    hr.appendChild(th);
  });
  thead.appendChild(hr);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 2;
    td.className = "log-space-table-empty";
    td.textContent = "No data yet.";
    tr.appendChild(td);
    tbody.appendChild(tr);
  } else {
    rows.forEach((s) => {
      const tr = document.createElement("tr");

      const tdHost = document.createElement("td");
      tdHost.className = "log-space-td-host";
      tdHost.textContent = s.host || "—";
      tr.appendChild(tdHost);

      const tdUsage = document.createElement("td");
      tdUsage.className = "log-space-td-usage";
      if (s.error) {
        tdUsage.textContent = s.error;
        tdUsage.classList.add("log-usage-tier-muted");
      } else if (typeof s.usage === "number") {
        const pill = document.createElement("span");
        pill.className = `log-space-usage-pill ${logSpaceUsageTierClass(s.usage)}`;
        pill.textContent = `${s.usage}%`;
        tdUsage.appendChild(pill);
      } else {
        tdUsage.textContent = "—";
        tdUsage.classList.add("log-usage-tier-muted");
      }
      tr.appendChild(tdUsage);
      tbody.appendChild(tr);
    });
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  section.appendChild(wrap);
  root.appendChild(section);
}

function renderServerLogSpace(outputData, task) {
  const root = document.createElement("div");
  root.className = "task-output log-space-root log-space-pro";

  const head = document.createElement("div");
  head.className = "log-space-pro-head";
  const title = document.createElement("div");
  title.className = "log-space-pro-title";
  title.textContent = outputData.header || "Servers log path usage";
  const sub = document.createElement("div");
  sub.className = "log-space-pro-subtitle";
  sub.textContent =
    "Live usage from NPE services. Green 0–50%, yellow 51–69%, red 70–100%. Above 70% triggers automated clean.";
  head.appendChild(title);
  head.appendChild(sub);
  root.appendChild(head);

  if (outputData.lastUsageUpdatedAt) {
    const strip = document.createElement("div");
    strip.className = "log-space-refresh-strip";
    try {
      strip.textContent = `Last updated: ${new Date(outputData.lastUsageUpdatedAt).toLocaleString()}`;
    } catch {
      strip.textContent = "Last updated: —";
    }
    root.appendChild(strip);
  }

  const servers = outputData.servers;
  const hasOver60 = (servers || []).some((s) => typeof s.usage === "number" && s.usage > 60);
  const cleaning = outputData.cleaningOverlay;

  if (outputData.finalWarningMessage) {
    const fw = document.createElement("div");
    fw.className = "log-space-final-warning";
    fw.setAttribute("role", "alert");
    const fwTitle = document.createElement("div");
    fwTitle.className = "log-space-final-warning-label";
    fwTitle.textContent = "Final warning";
    const fwBody = document.createElement("div");
    fwBody.className = "log-space-final-warning-text";
    fwBody.textContent = outputData.finalWarningMessage;
    fw.appendChild(fwTitle);
    fw.appendChild(fwBody);
    root.appendChild(fw);
  } else if (outputData.hasUsageWarning || hasOver60) {
    const soft = document.createElement("div");
    soft.className = "log-space-soft-warning";
    soft.textContent = "At least one host is above 60% log path usage.";
    root.appendChild(soft);
  }

  const dashboard = document.createElement("div");
  dashboard.className = "log-space-dashboard";

  const tablesShell = document.createElement("div");
  tablesShell.className = `log-space-tables-shell${cleaning ? " log-space-tables-shell--blurred" : ""}`;

  if (servers && Array.isArray(servers)) {
    logSpaceAppendTableSection(tablesShell, "TUX", logSpaceServersForGroup(servers, "TUX"));
    logSpaceAppendTableSection(tablesShell, "PRAPI", logSpaceServersForGroup(servers, "PRAPI"));
    logSpaceAppendTableSection(tablesShell, "API LINK", logSpaceServersForGroup(servers, "APILINK"));
  } else {
    const pending = document.createElement("div");
    pending.className = "log-space-table-pending";
    pending.textContent =
      task.status === "Running" || outputData.streaming
        ? "Fetching usage from NPE services…"
        : "No server usage loaded.";
    tablesShell.appendChild(pending);
  }

  dashboard.appendChild(tablesShell);

  if (cleaning && cleaning.hosts && cleaning.hosts.length) {
    const overlay = document.createElement("div");
    overlay.className = "log-space-clean-overlay";
    const card = document.createElement("div");
    card.className = "log-space-clean-card";
    const line1 = document.createElement("div");
    line1.className = "log-space-clean-card-title";
    line1.textContent = "Clearing log files";
    const line2 = document.createElement("div");
    line2.className = "log-space-clean-card-body";
    const hostList = cleaning.hosts.join(", ");
    line2.textContent = `Removing files until ${cleaning.cutoffDatetime || "—"} for ${hostList}.`;
    card.appendChild(line1);
    card.appendChild(line2);
    overlay.appendChild(card);
    dashboard.appendChild(overlay);
  }

  root.appendChild(dashboard);

  const details = document.createElement("details");
  details.className = "log-space-activity-details";
  const summ = document.createElement("summary");
  summ.textContent = "Activity log";
  details.appendChild(summ);
  const logPre = document.createElement("pre");
  logPre.className = "log-space-summary";
  const lines = outputData.activityLog || [];
  logPre.textContent =
    lines.length > 0
      ? lines.join("\n")
      : task.status === "Running"
        ? "Starting automated log space check…"
        : "No activity recorded.";
  details.appendChild(logPre);
  root.appendChild(details);

  return root;
}

function renderServerBounceForm(task) {
  const root = document.createElement("div");
  root.className = "task-output bounce-form";

  const intro = document.createElement("p");
  intro.className = "bounce-intro";
  intro.textContent =
    "Open the batch portal below to create your bounce request. Enter the ReqCentral / JIRA issue key (e.g. RNER-197725), then click Check status. The portal loads the board preview from reqcentral.com — when the ticket is Done, this task completes; if it is In Progress, the task stays In Progress.";
  root.appendChild(intro);

  const link = document.createElement("a");
  link.href = task.outputData.portalUrl;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.className = "bounce-portal-link";
  link.textContent = `${task.outputData.portalLabel} — ${task.outputData.portalUrl}`;
  root.appendChild(link);

  const jiraRow = document.createElement("div");
  jiraRow.className = "bounce-jira-row";

  const label = document.createElement("label");
  label.className = "bounce-jira-label";
  label.setAttribute("for", "bounce-jira-input");
  label.textContent = "JIRA issue key";
  jiraRow.appendChild(label);

  const input = document.createElement("input");
  input.type = "text";
  input.id = "bounce-jira-input";
  input.className = "bounce-jira-input";
  input.placeholder = "e.g. RNER-197725";
  input.autocomplete = "off";
  jiraRow.appendChild(input);

  const checkBtn = document.createElement("button");
  checkBtn.type = "button";
  checkBtn.className = "primary bounce-check-btn";
  checkBtn.textContent = "Check status";
  jiraRow.appendChild(checkBtn);

  root.appendChild(jiraRow);

  const feedback = document.createElement("div");
  feedback.className = "bounce-check-feedback";
  if (task.bounceCheckMessage) {
    feedback.textContent = task.bounceCheckMessage;
  }
  root.appendChild(feedback);

  const resetRow = document.createElement("div");
  resetRow.className = "bounce-reset-row";
  const resetBtn = document.createElement("button");
  resetBtn.type = "button";
  resetBtn.className = "skip bounce-reset-btn";
  resetBtn.textContent = "Reset task";
  resetBtn.addEventListener("click", () => {
    updateTask(4, {
      status: "Pending",
      skipped: false,
      output: "",
      outputType: "text",
      outputData: null,
      bounceCheckMessage: "",
      expanded: false
    });
  });
  resetRow.appendChild(resetBtn);
  root.appendChild(resetRow);

  checkBtn.addEventListener("click", async () => {
    const key = input.value.trim().toUpperCase();
    if (!key) {
      feedback.textContent = "Enter an issue key (for example RNER-197725).";
      return;
    }
    feedback.textContent = "Checking ReqCentral…";
    checkBtn.disabled = true;
    try {
      const response = await fetch(apiUrl("/api/jira/issue-status"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ issueKey: key })
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(result.message || `Request failed with status ${response.status}`);
      }
      if (result.completed) {
        const statusLine = result.statusName ? `Board status: ${result.statusName}` : "Board status: Done";
        const browse = result.browseUrl || `https://reqcentral.com/browse/${key}`;
        updateTask(4, {
          status: "Completed",
          skipped: false,
          output: [
            "Server bounce — ReqCentral ticket shows Done.",
            `Issue: ${key}`,
            statusLine,
            `Preview / board: ${browse}`,
            `Batch portal: ${SERVER_BOUNCE_PORTAL_LABEL}`,
            `URL: ${SERVER_BOUNCE_PORTAL_URL}`
          ].join("\n"),
          outputType: "text",
          outputData: null,
          bounceCheckMessage: "",
          expanded: true
        });
        return;
      }
      if (result.inProgress) {
        const browse = result.browseUrl || `https://reqcentral.com/browse/${key}`;
        const msg = [
          `Preview: ${browse}`,
          `Board status: ${result.statusName || "In Progress"}.`,
          "Task remains In Progress until the ticket is Done on the board."
        ].join("\n");
        feedback.textContent = msg;
        updateTask(4, {
          status: "In Progress",
          bounceCheckMessage: msg,
          outputType: "server-bounce-form",
          outputData: {
            portalUrl: SERVER_BOUNCE_PORTAL_URL,
            portalLabel: SERVER_BOUNCE_PORTAL_LABEL
          },
          expanded: true
        });
        return;
      }
      const hint =
        result.message ||
        (result.statusName
          ? `Board shows: "${result.statusName}". Open ReqCentral to confirm, then check again.`
          : "Could not determine status from the page. Open the ReqCentral link and verify on the board.");
      feedback.textContent = hint;
      updateTask(4, { bounceCheckMessage: hint });
    } catch (err) {
      feedback.textContent = err.message || String(err);
      updateTask(4, { bounceCheckMessage: feedback.textContent });
    } finally {
      checkBtn.disabled = false;
    }
  });

  return root;
}

function updateTask(taskId, updates) {
  tasks = tasks.map((task) => (task.id === taskId ? { ...task, ...updates } : task));
  renderTasks();
}

async function runPetHealthTask() {
  const taskId = 5;
  updateTask(taskId, {
    status: "Running",
    skipped: false,
    expanded: true,
    output: "",
    outputType: "server-health-pet",
    outputData: {
      header: "Server Health Check — PET1-V21",
      servers: [],
      streaming: true,
      petUrl: "",
      warnings: [],
      allUp: false,
      hasUnknown: false,
      checkedAt: null
    }
  });

  try {
    const response = await fetch(apiUrl("/api/tasks/server-health-check/run"), { method: "POST" });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(result.message || `Request failed with status ${response.status}`);
    }
    updateTask(taskId, {
      status: "Completed",
      skipped: false,
      output: result.header || "Server Health Check (PET1-V21)",
      outputType: "server-health-pet",
      outputData: {
        ...result,
        streaming: false
      },
      expanded: true
    });
  } catch (error) {
    updateTask(taskId, {
      status: "Completed",
      skipped: false,
      output: `Task failed: ${formatFetchFailureMessage(error)}`,
      outputType: "text",
      outputData: null,
      expanded: true
    });
  }
}

async function runServerLogSpaceTask() {
  const taskId = 3;
  updateTask(taskId, {
    status: "Running",
    skipped: false,
    expanded: true,
    output: "",
    outputType: "server-log-space",
    outputData: {
      activityLog: [],
      servers: null,
      streaming: true,
      cleaningOverlay: null,
      lastUsageUpdatedAt: null,
      header: "Servers Log Space — automated check"
    }
  });

  const appendLog = (message) => {
    const t = tasks.find((x) => x.id === taskId);
    const od = t?.outputData || {};
    updateTask(taskId, {
      outputData: {
        ...od,
        activityLog: [...(od.activityLog || []), message],
        streaming: true
      }
    });
  };

  try {
    const response = await fetch(apiUrl("/api/tasks/server-log-check/run"), {
      method: "POST",
      headers: { Accept: "application/x-ndjson, application/json" }
    });

    const ct = response.headers.get("content-type") || "";

    if (ct.includes("application/json")) {
      const text = await response.text();
      let result = {};
      try {
        result = JSON.parse(text);
      } catch {
        throw new Error(text.slice(0, 200) || `Request failed with status ${response.status}`);
      }
      if (!response.ok) {
        throw new Error(result.message || `Request failed with status ${response.status}`);
      }
      updateTask(taskId, {
        status: "Completed",
        skipped: false,
        output: result.header || "Servers Log Space Check",
        outputType: "server-log-space",
        outputData: {
          ...result,
          streaming: false,
          cleaningOverlay: null,
          lastUsageUpdatedAt: result.lastUsageUpdatedAt || new Date().toISOString()
        },
        expanded: true
      });
      return;
    }

    if (!response.ok) {
      const text = await response.text();
      let msg = `Request failed with status ${response.status}`;
      try {
        const j = JSON.parse(text);
        if (j.message) msg = j.message;
      } catch {
        if (text) msg = text.slice(0, 300);
      }
      throw new Error(msg);
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw new Error("This browser does not support streaming the log space check.");
    }

    const decoder = new TextDecoder();
    let buffer = "";
    let finalPayload = null;
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        let ev;
        try {
          ev = JSON.parse(line);
        } catch {
          continue;
        }
        if (ev.event === "log" && ev.message) {
          appendLog(ev.message);
        }
        if (ev.event === "usage_snapshot" && Array.isArray(ev.servers)) {
          const t = tasks.find((x) => x.id === taskId);
          const od = t?.outputData || {};
          updateTask(taskId, {
            outputData: {
              ...od,
              servers: ev.servers,
              lastUsageUpdatedAt: ev.updatedAt || new Date().toISOString(),
              streaming: true
            }
          });
        }
        if (ev.event === "clean_start") {
          const t = tasks.find((x) => x.id === taskId);
          const od = t?.outputData || {};
          updateTask(taskId, {
            outputData: {
              ...od,
              cleaningOverlay: {
                cutoffDatetime: ev.cutoffDatetime,
                hosts: Array.isArray(ev.hosts) ? ev.hosts : [],
                group: ev.group || ""
              },
              streaming: true
            }
          });
        }
        if (ev.event === "clean_end") {
          const t = tasks.find((x) => x.id === taskId);
          const od = t?.outputData || {};
          updateTask(taskId, {
            outputData: {
              ...od,
              cleaningOverlay: null,
              streaming: true
            }
          });
        }
        if (ev.event === "done") finalPayload = ev.data;
        if (ev.event === "error") throw new Error(ev.message || "Stream error");
      }
      if (done) break;
    }
    const tail = buffer.trim();
    if (tail.startsWith("{")) {
      try {
        const ev = JSON.parse(tail);
        if (ev.event === "done") finalPayload = ev.data;
        if (ev.event === "error") throw new Error(ev.message || "Stream error");
      } catch (e) {
        if (!(e instanceof SyntaxError)) {
          throw e;
        }
      }
    }

    if (!finalPayload) {
      throw new Error("No result received from the server log space check.");
    }

    const tBefore = tasks.find((x) => x.id === taskId);
    const prevUpdated = tBefore?.outputData?.lastUsageUpdatedAt;
    updateTask(taskId, {
      status: "Completed",
      skipped: false,
      output: finalPayload.header || "Servers Log Space Check",
      outputType: "server-log-space",
      outputData: {
        ...finalPayload,
        activityLog: finalPayload.activityLog || [],
        streaming: false,
        cleaningOverlay: null,
        lastUsageUpdatedAt: finalPayload.lastUsageUpdatedAt || prevUpdated || new Date().toISOString()
      },
      expanded: true
    });
  } catch (error) {
    updateTask(taskId, {
      status: "Completed",
      skipped: false,
      output: `Task failed: ${formatFetchFailureMessage(error)}`,
      outputType: "text",
      outputData: null,
      expanded: true
    });
  }
}

function applyTask1FlowResult(flow) {
  const phase = flow.phase || (flow.success ? "prep-done" : "error");
  const guidance = flow.guidance || [];
  const output =
    phase === "prep-done"
      ? "Task 1 completed — all validations passed."
      : guidance.length
        ? guidance.map((g) => g.message).join(" ")
        : flow.prep?.message ||
          flow.message ||
          (phase === "insufficient"
            ? "Validations failed after data reload."
            : "Task 1 finished with errors.");
  updateTask(1, {
    status: "Completed",
    skipped: false,
    output,
    outputType: "data-loading-vts",
    outputData: {
      phase,
      precheck: flow.precheck,
      prep: flow.prep,
      remediation: flow.remediation || [],
      queryDefaults: flow.queryDefaults,
      messages: flow.messages || [],
      guidance,
      validationSummary: flow.validationSummary || []
    },
    expanded: true
  });
}

async function runTask1OracleTest() {
  updateTask(1, {
    status: "Running",
    skipped: false,
    expanded: true,
    output: "",
    outputType: "oracle-connection-test",
    outputData: { message: "Testing Oracle connection from Python backend…" }
  });
  try {
    const res = await fetch(apiUrl("/api/oracle/test-connection"), { method: "POST" });
    const result = await res.json().catch(() => ({}));
    updateTask(1, {
      status: "Completed",
      skipped: false,
      output: result.message || (result.connected ? "Oracle OK" : "Oracle failed"),
      outputType: "oracle-connection-test",
      outputData: result,
      expanded: true
    });
  } catch (err) {
    updateTask(1, {
      status: "Completed",
      skipped: false,
      output: `Oracle test failed: ${formatFetchFailureMessage(err)}`,
      outputType: "oracle-connection-test",
      outputData: {
        connected: false,
        message: formatFetchFailureMessage(err),
        checkpoints: [],
        messages: [formatFetchFailureMessage(err)]
      },
      expanded: true
    });
  }
}

async function runTask1VtsFlow() {
  updateTask(1, {
    status: "Running",
    skipped: false,
    expanded: true,
    output: "",
    outputType: "data-loading-vts",
    outputData: { phase: "checking", messages: [] }
  });
  try {
    const res = await fetch(apiUrl("/api/tasks/data-loading/run-flow"), { method: "POST" });
    const flow = await res.json().catch(() => ({}));
    if (!flow.phase && !flow.precheck) {
      throw new Error(flow.message || `Task 1 flow failed (HTTP ${res.status})`);
    }
    applyTask1FlowResult(flow);
  } catch (err) {
    updateTask(1, {
      status: "Completed",
      skipped: false,
      output: `Task 1 failed: ${formatFetchFailureMessage(err)}`,
      outputType: "data-loading-vts",
      outputData: {
        phase: "error",
        message: formatFetchFailureMessage(err)
      },
      expanded: true
    });
  }
}

let oracleQueriesModalEl = null;

function closeOracleQueriesEditor() {
  if (oracleQueriesModalEl) {
    oracleQueriesModalEl.remove();
    oracleQueriesModalEl = null;
  }
}

async function openOracleQueriesEditor() {
  closeOracleQueriesEditor();
  const overlay = document.createElement("div");
  overlay.className = "oracle-queries-overlay";
  const panel = document.createElement("div");
  panel.className = "oracle-queries-panel";
  const title = document.createElement("h3");
  title.textContent = "Oracle queries (VTS 1)";
  panel.appendChild(title);
  const hint = document.createElement("p");
  hint.className = "oracle-queries-hint";
  hint.textContent =
    "Edit SQL used when VTS validation fails. Trigger 1+6: P_BAN query, then P_BAN_H in 3×1000 batches. Keep {BAN_LIST}. Prefer EXISTS (not JOIN) on service_agreement — JOIN is auto-rewritten at runtime.";
  panel.appendChild(hint);
  const form = document.createElement("div");
  form.className = "oracle-queries-form";
  panel.appendChild(form);
  const actions = document.createElement("div");
  actions.className = "oracle-queries-actions";
  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.textContent = "Cancel";
  cancelBtn.addEventListener("click", () => closeOracleQueriesEditor());
  const updateBtn = document.createElement("button");
  updateBtn.type = "button";
  updateBtn.className = "primary";
  updateBtn.textContent = "Update";
  actions.appendChild(cancelBtn);
  actions.appendChild(updateBtn);
  panel.appendChild(actions);
  overlay.appendChild(panel);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeOracleQueriesEditor();
  });
  document.body.appendChild(overlay);
  oracleQueriesModalEl = overlay;

  form.textContent = "Loading queries…";
  let payload;
  try {
    const res = await fetch(apiUrl("/api/tasks/data-loading/oracle-queries"));
    payload = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(payload.message || `HTTP ${res.status}`);
  } catch (err) {
    form.textContent = `Failed to load queries: ${formatFetchFailureMessage(err)}`;
    updateBtn.disabled = true;
    return;
  }

  const meta = payload.meta || [];
  const queries = payload.queries || {};
  const editors = {};
  form.textContent = "";
  meta.forEach((m) => {
    const lb = document.createElement("label");
    lb.textContent = m.label || m.id;
    form.appendChild(lb);
    const sub = document.createElement("div");
    sub.className = "oracle-queries-sub";
    sub.textContent = `TXT: ${m.txtFile} — headers: ${(m.headers || []).join(", ")}`;
    form.appendChild(sub);
    const ta = document.createElement("textarea");
    ta.className = "oracle-queries-sql";
    ta.spellcheck = false;
    ta.value = queries[m.id] || "";
    ta.dataset.queryId = m.id;
    editors[m.id] = ta;
    form.appendChild(ta);
  });

  updateBtn.addEventListener("click", async () => {
    updateBtn.disabled = true;
    cancelBtn.disabled = true;
    const body = { queries: {} };
    Object.entries(editors).forEach(([id, ta]) => {
      body.queries[id] = ta.value;
    });
    try {
      const res = await fetch(apiUrl("/api/tasks/data-loading/oracle-queries"), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      const saved = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(saved.message || `Save failed (HTTP ${res.status})`);
      closeOracleQueriesEditor();
    } catch (err) {
      hint.textContent = `Save failed: ${formatFetchFailureMessage(err)}`;
      hint.className = "oracle-queries-hint oracle-queries-error";
    } finally {
      updateBtn.disabled = false;
      cancelBtn.disabled = false;
    }
  });
}

async function runTask(taskId) {
  const selectedTask = tasks.find((task) => task.id === taskId);
  if (!selectedTask) return;

  if (taskId === 1) {
    void runTask1VtsFlow();
    return;
  }

  if (taskId === 4) {
    runTask4ServerBounce();
    return;
  }

  if (taskId === 3) {
    void runServerLogSpaceTask();
    return;
  }

  if (taskId === 5) {
    void runPetHealthTask();
    return;
  }

  updateTask(taskId, {
    status: "Running",
    skipped: false,
    expanded: true
  });

  try {
    const response = await fetch(apiUrl(selectedTask.runEndpoint), { method: "POST" });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = result.message || `Request failed with status ${response.status}`;
      throw new Error(detail);
    }
    const taskIsLgDiskCheck = taskId === 2;
    updateTask(taskId, {
      status: "Completed",
      skipped: false,
      output: taskIsLgDiskCheck
        ? (result.header || "LG Disk Space Check Results")
        : (result.output || "Task finished with no output."),
      outputType: taskIsLgDiskCheck ? "lg-disk-table" : "text",
      outputData: taskIsLgDiskCheck ? result : null,
      expanded: true
    });
  } catch (error) {
    updateTask(taskId, {
      status: "Completed",
      skipped: false,
      output: `Task failed: ${formatFetchFailureMessage(error)}`,
      outputType: "text",
      outputData: null,
      expanded: true
    });
  }
}

function runTask4ServerBounce() {
  updateTask(4, {
    status: "In Progress",
    skipped: false,
    output: "Server bounce — use the portal link, then verify your JIRA ticket.",
    outputType: "server-bounce-form",
    outputData: {
      portalUrl: SERVER_BOUNCE_PORTAL_URL,
      portalLabel: SERVER_BOUNCE_PORTAL_LABEL
    },
    bounceCheckMessage: "",
    expanded: true
  });
}

function skipTask(taskId) {
  updateTask(taskId, {
    status: "Completed",
    skipped: true,
    output: "User bypassed this step",
    outputType: "text",
    outputData: null,
    expanded: true
  });
}

async function triggerEmail(buttonEl, endpoint, label) {
  const originalText = buttonEl.textContent;
  buttonEl.disabled = true;
  buttonEl.textContent = `${label} (Sending...)`;
  emailFeedback.textContent = "";

  try {
    const response = await fetch(apiUrl(endpoint), { method: "POST" });
    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }
    const result = await response.json();
    emailFeedback.textContent = result.message || `${label} triggered successfully.`;
  } catch (error) {
    emailFeedback.textContent = `${label} failed: ${formatFetchFailureMessage(error)}`;
  } finally {
    buttonEl.disabled = false;
    buttonEl.textContent = originalText;
  }
}

hupEmailButton.addEventListener("click", () =>
  triggerEmail(hupEmailButton, "/api/email/hup-offer-cleanup", "HUP Offer Cleanup")
);

crmEmailButton.addEventListener("click", () =>
  triggerEmail(crmEmailButton, "/api/email/crm-igw-bounce", "CRM IGW Bounce")
);

renderTasks();
