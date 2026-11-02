require("dotenv").config();
const express = require("express");
const cors = require("cors");
const path = require("path");
const nodemailer = require("nodemailer");
const { randomUUID } = require("crypto");
const { execFile, exec } = require("child_process");
const { runServerLogSpaceCheck } = require("./lib/taskServerLogSpace");
const { runPetHealthCheck, PET_HEALTH_URL } = require("./lib/taskPetHealth");
const { checkReqcentralIssue } = require("./lib/reqcentralStatus");
const { collectAndCollate, compareAbnormalServices } = require("./lib/tuxedoStatsCollate");

const app = express();
const resultReports = new Map();

function parsePort(value, fallback) {
  const n = parseInt(String(value ?? "").trim(), 10);
  if (Number.isFinite(n) && n >= 1 && n <= 65535) {
    return n;
  }
  return fallback;
}

const PORT = parsePort(process.env.PORT, 3000);
const HOST = (process.env.HOST || "0.0.0.0").trim() || "0.0.0.0";
const MOBAXTERM_EXE = (
  process.env.MOBAXTERM_PATH || "C:\\Moba\\MobaXterm_Personal_24.0.exe"
).trim();
const HUP_OFFER_COUNT_URL =
  process.env.HUP_OFFER_COUNT_URL ||
  "http://npe-services:8888/cgi-bin/fetchPetV21HupOfferCount";
const HUP_OFFER_CLEAN_URL =
  process.env.HUP_OFFER_CLEAN_URL ||
  "http://npe-services:8888/cgi-bin/cleanPetV21HupOfferTable";
const HUP_OFFER_STATUS_URL =
  process.env.HUP_OFFER_STATUS_URL ||
  "http://npe-services:8888/cgi-bin/checkPetV21HupOfferCleanUpJobStatus";

function openDefaultBrowser(url) {
  const cmd =
    process.platform === "win32"
      ? `start "" "${url}"`
      : process.platform === "darwin"
        ? `open "${url}"`
        : `xdg-open "${url}"`;
  exec(cmd, () => {});
}

app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, "public")));

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchNpeJson(url, params = null) {
  const target = new URL(url);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      target.searchParams.set(key, value);
    }
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 60000);
  try {
    const response = await fetch(target, { signal: controller.signal });
    const text = await response.text();
    if (!response.ok) {
      throw new Error(`NPE service returned HTTP ${response.status}: ${text.slice(0, 300)}`);
    }
    const payload = JSON.parse(text);
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("NPE service returned an unexpected non-object response.");
    }
    return payload;
  } finally {
    clearTimeout(timeout);
  }
}

function execPowerShell(command, options = {}) {
  const timeout = typeof options.timeout === "number" ? options.timeout : 20000;
  return new Promise((resolve, reject) => {
    execFile(
      "powershell.exe",
      ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
      { timeout, maxBuffer: 1024 * 1024 * 8 },
      (error, stdout, stderr) => {
        if (error) {
          reject(new Error(stderr || error.message));
          return;
        }
        resolve((stdout || "").trim());
      }
    );
  });
}

const lgDiskServers = {
  "10.61.139.26": "VWRENBPTLRCAP01",
  "10.61.139.27": "VWRENBPTLRGAP02",
  "10.61.139.28": "VWRENBPTLRGAP03",
  "10.61.139.29": "VWRENBPTLRGAP04",
  "10.61.139.30": "VWRENBPTLRGAP05",
  "10.61.139.31": "VWRENBPTLRGAP06",
  "10.61.129.119": "VM0196PTLRG009"
};

const tuxBounceServers = [
  { login: "petole1a@VLRENBPTCOLAP01", server: "VLRENBPTCOLAP01", label: "TUXEDO" },
  { login: "petole2a@VLRENBPTCOLAP02", server: "VLRENBPTCOLAP02", label: "TUXEDO" }
];

async function launchMobaXterm() {
  const psPath = MOBAXTERM_EXE.replace(/'/g, "''");
  const psCommand = `if (Test-Path '${psPath}') { Start-Process -FilePath '${psPath}' | Out-Null; "started" } else { "missing" }`;
  const result = await execPowerShell(psCommand);
  return result.includes("started");
}

async function runSshScript(login, shellScript) {
  const escaped = shellScript.replace(/"/g, "`\"");
  const psCommandLines = [
    "$ErrorActionPreference = \"Stop\"",
    `$login = "${login}"`,
    `$inputPayload = "${escaped}"`,
    "$raw = $inputPayload | ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 $login 2>&1",
    "($raw -join \"`n\")"
  ];
  const psCommand = psCommandLines.join("\n");
  return execPowerShell(psCommand);
}

async function runTuxBounce(login) {
  const bounceCommand = process.env.TUX_BOUNCE_COMMAND || "get tuxedo bounce status";
  const inputPayload = "`n`na`n" + bounceCommand + "`nexit`n";
  try {
    const output = await runSshScript(login, inputPayload);
    const normalized = (output || "").trim();
    if (!normalized) {
      return "FAILED TO CONNECT";
    }
    if (/permission denied|timed out|could not resolve|connection refused|host key verification failed/i.test(normalized)) {
      return "FAILED TO CONNECT";
    }
    if (/failed|error/i.test(normalized) && !/0 failed/i.test(normalized)) {
      return "BOUNCE COMMAND FAILED";
    }
    return "BOUNCE COMMAND EXECUTED";
  } catch (_error) {
    return "FAILED TO CONNECT";
  }
}

async function getRemoteDiskData(ip, machine) {
  // Get-WmiObject uses DCOM/RPC (legacy WMI), not WinRM — avoids HRESULT 0x803381bb when querying by IP.
  const psCommand = `
$ErrorActionPreference = "Stop"
$computer = "${ip}"
$disks = Get-WmiObject -Class Win32_LogicalDisk -ComputerName $computer -Filter "DriveType=3"
$c = $disks | Where-Object { $_.DeviceID -eq "C:" }
$d = $disks | Where-Object { $_.DeviceID -eq "D:" }
[pscustomobject]@{
  ip = "${ip}"
  machine = "${machine}"
  c_total = if ($c -and $c.Size) { [math]::Round([double]$c.Size / 1GB, 2) } else { $null }
  c_free = if ($c -and $c.FreeSpace) { [math]::Round([double]$c.FreeSpace / 1GB, 2) } else { $null }
  d_total = if ($d -and $d.Size) { [math]::Round([double]$d.Size / 1GB, 2) } else { $null }
  d_free = if ($d -and $d.FreeSpace) { [math]::Round([double]$d.FreeSpace / 1GB, 2) } else { $null }
} | ConvertTo-Json -Compress
`;

  try {
    const raw = await execPowerShell(psCommand, { timeout: 45000 });
    return JSON.parse(raw);
  } catch (error) {
    const err = (error && error.message) || String(error);
    return {
      ip,
      machine,
      c_total: "Error",
      c_free: "Error",
      d_total: "Error",
      d_free: "Error",
      queryError: err.slice(0, 800)
    };
  }
}

async function simulateTaskRun(taskName, purpose) {
  const startedAt = new Date().toISOString();
  await delay(900);
  const finishedAt = new Date().toISOString();
  return [
    `Task: ${taskName}`,
    `Purpose: ${purpose}`,
    `Started: ${startedAt}`,
    `Finished: ${finishedAt}`,
    "Result: Simulated run completed (no external systems were called).",
    "Note: Replace this placeholder with your real data load or sanity job when you wire it in."
  ].join("\n");
}

function getSmtpTransporter() {
  const host = process.env.SMTP_HOST;
  const port = Number(process.env.SMTP_PORT || 587);
  const user = process.env.SMTP_USER;
  const pass = process.env.SMTP_PASS;

  if (!host || !user || !pass) {
    return null;
  }

  return nodemailer.createTransport({
    host,
    port,
    secure: port === 465,
    auth: { user, pass }
  });
}

async function sendPortalEmail({ to, cc, subject, text }) {
  const transporter = getSmtpTransporter();
  const fromAddress = process.env.SMTP_FROM || process.env.SMTP_USER;

  if (!transporter || !fromAddress) {
    throw new Error(
      "SMTP is not configured. Set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, and SMTP_FROM."
    );
  }

  const info = await transporter.sendMail({
    from: fromAddress,
    to,
    cc,
    subject,
    text
  });

  return info;
}

app.post("/api/email/crm-igw-bounce", async (_req, res) => {
  try {
    const info = await sendPortalEmail({
      to: "\"#RCI_V21 NPE Support\" <RCIV21NPESupport@rci.rogers.com>",
      cc: "\"QE Performance Engineering\" <PerformanceTesting@rci.rogers.com>",
      subject: "Rogers V21 | PET | Test Preparatory Activities",
      text: [
        "Hi @RCI_V21 NPE Support,",
        "",
        "Can you please reboot CRM IGW Domain 1 Servers.",
        "",
        "Thanks & Regards",
        "Para Chaitanya"
      ].join("\n")
    });

    res.json({
      success: true,
      message: "CRM IGW Bounce email triggered successfully.",
      messageId: info.messageId
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      message: `Failed to send CRM IGW Bounce email: ${error.message}`
    });
  }
});

const dataLoadingPythonOnly = {
  success: false,
  message:
    "Task 1 (Data loading) is implemented on the Python backend only. Run: python app.py (or start-portal.bat choosing Python), then use the portal in the browser."
};

app.all("/api/oracle/test-connection", async (_req, res) => {
  res.status(501).json(dataLoadingPythonOnly);
});

app.get("/api/tasks/data-loading/query-defaults", async (_req, res) => {
  res.status(501).json(dataLoadingPythonOnly);
});

app.post("/api/tasks/data-loading/p-ban-reload", async (_req, res) => {
  res.status(501).json(dataLoadingPythonOnly);
});

app.post("/api/tasks/data-loading/vts-precheck", async (_req, res) => {
  res.status(501).json(dataLoadingPythonOnly);
});

app.post("/api/tasks/data-loading/vts-prep", async (_req, res) => {
  res.status(501).json(dataLoadingPythonOnly);
});

app.get("/api/tasks/hup-offer/count", async (_req, res) => {
  try {
    res.json(await fetchNpeJson(HUP_OFFER_COUNT_URL));
  } catch (error) {
    res.status(502).json({
      success: false,
      message: `Failed to fetch HUP_OFFER count: ${error.message || String(error)}`
    });
  }
});

app.post("/api/tasks/hup-offer/clean", async (_req, res) => {
  try {
    res.json(await fetchNpeJson(HUP_OFFER_CLEAN_URL));
  } catch (error) {
    res.status(502).json({
      success: false,
      message: `Failed to start HUP_OFFER cleanup: ${error.message || String(error)}`
    });
  }
});

app.get("/api/tasks/hup-offer/status", async (req, res) => {
  const pid = String(req.query.pid || "").trim();
  const jobId = String(req.query.jobId || "").trim();
  if (!pid || !jobId) {
    res.status(400).json({ success: false, message: "pid and jobId are required." });
    return;
  }
  try {
    res.json(await fetchNpeJson(HUP_OFFER_STATUS_URL, { pid, jobId }));
  } catch (error) {
    res.status(502).json({
      success: false,
      message: `Failed to check HUP_OFFER cleanup status: ${error.message || String(error)}`
    });
  }
});

app.post("/api/tasks/lg-disk-check/run", async (_req, res) => {
  const results = [];
  for (const [ip, machine] of Object.entries(lgDiskServers)) {
    const item = await getRemoteDiskData(ip, machine);
    results.push(item);
  }
  const hasCriticalWarning = results.some(
    (item) =>
      (typeof item.c_free === "number" && item.c_free < 20) ||
      (typeof item.d_free === "number" && item.d_free < 20)
  );
  res.json({
    success: true,
    type: "lg-disk-table",
    header: "LG Disk Space Check Results",
    hasCriticalWarning,
    results
  });
});

app.post("/api/open-share", async (req, res) => {
  const { machineName, drive } = req.body || {};
  const normalizedDrive = (drive || "").toLowerCase();
  if (!machineName || !["c", "d"].includes(normalizedDrive)) {
    res.status(400).json({ success: false, message: "Invalid machineName or drive." });
    return;
  }

  const sharePath = `\\\\${machineName}\\${normalizedDrive}$`;
  const psCommand = `Start-Process explorer.exe "${sharePath}"`;

  try {
    await execPowerShell(psCommand);
    res.json({ success: true, path: sharePath });
  } catch (error) {
    res.status(500).json({ success: false, message: `Failed to open share: ${error.message}` });
  }
});

app.post("/api/tasks/server-log-check/run", async (req, res) => {
  const accept = String(req.headers.accept || "").toLowerCase();
  const wantsStream =
    accept.includes("application/x-ndjson") ||
    accept.includes("application/ndjson") ||
    String(req.query.stream || "") === "1";

  if (wantsStream) {
    res.setHeader("Content-Type", "application/x-ndjson; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("X-Accel-Buffering", "no");
    if (typeof res.flushHeaders === "function") {
      res.flushHeaders();
    }
    const writeEvent = (obj) => {
      res.write(`${JSON.stringify(obj)}\n`);
    };
    try {
      const result = await runServerLogSpaceCheck((payload) => writeEvent(payload));
      writeEvent({ event: "done", data: result });
    } catch (error) {
      writeEvent({
        event: "error",
        message: error.message || String(error)
      });
    }
    res.end();
    return;
  }

  try {
    const result = await runServerLogSpaceCheck(() => {});
    res.json(result);
  } catch (error) {
    res.status(500).json({
      success: false,
      message: error.message || "Server log space check failed."
    });
  }
});

app.post("/api/tasks/server-bounce/run", async (_req, res) => {
  const mobaStarted = await launchMobaXterm();
  await delay(1200);

  const servers = [];
  for (const item of tuxBounceServers) {
    const status = await runTuxBounce(item.login);
    servers.push({
      group: "TUX Servers",
      server: item.server,
      label: item.label,
      status
    });
  }

  res.json({
    success: true,
    type: "server-bounce-list",
    mobaStarted,
    mobaPath: MOBAXTERM_EXE,
    servers
  });
});

app.post("/api/tasks/server-health-check/run", async (_req, res) => {
  try {
    const payload = await runPetHealthCheck();
    if (!payload.success) {
      res.status(502).json(payload);
      return;
    }
    res.json(payload);
  } catch (error) {
    res.status(500).json({
      success: false,
      message: error.message || "Server health check failed.",
      petUrl: PET_HEALTH_URL
    });
  }
});

app.post("/api/results/collate", async (req, res) => {
  try {
    const { state, payload } = await collectAndCollate(req.body || {});
    const reportId = randomUUID();
    resultReports.set(reportId, { ...state, createdAt: Date.now() });
    res.json({ ...payload, reportId });
  } catch (error) {
    res.status(error.statusCode || 500).json({
      success: false,
      message: error.message || "Collate failed."
    });
  }
});

app.get("/api/results/:reportId/raw", (req, res) => {
  const report = resultReports.get(req.params.reportId);
  if (!report) {
    res.status(404).json({ success: false, message: "The report session has expired. Run Collate again." });
    return;
  }
  const filename = report.file || `tuxedo_stats_${report.window.date}_${report.window.from.replace(":", "")}.out`;
  res.type("text/plain");
  if (req.query.download !== "0") {
    res.attachment(filename);
  }
  res.send(report.rawReport);
});

app.post("/api/results/:reportId/compare", async (req, res) => {
  const report = resultReports.get(req.params.reportId);
  if (!report) {
    res.status(404).json({ success: false, message: "The report session has expired. Run Collate again." });
    return;
  }
  try {
    const payload = await compareAbnormalServices(report, req.body || {});
    res.json({ ...payload, reportId: req.params.reportId });
  } catch (error) {
    res.status(error.statusCode || 500).json({
      success: false,
      message: error.message || "Response-time comparison failed."
    });
  }
});

app.post("/api/tasks/trigger-sanity/run", async (_req, res) => {
  const output = await simulateTaskRun(
    "Trigger Sanity",
    "Run post-actions sanity validation."
  );
  res.json({ success: true, output });
});

app.post("/api/jira/issue-status", async (req, res) => {
  const raw = String((req.body && (req.body.issueKey || req.body.jiraId)) || "")
    .trim()
    .toUpperCase();
  if (!/^[A-Z][A-Z0-9_]*-\d+$/.test(raw)) {
    res.status(400).json({ success: false, message: "Enter a valid issue key like RNER-197725." });
    return;
  }

  try {
    const payload = await checkReqcentralIssue(raw);
    if (!payload.success) {
      res.status(502).json(payload);
      return;
    }
    res.json(payload);
  } catch (error) {
    res.status(502).json({
      success: false,
      source: "reqcentral",
      completed: false,
      inProgress: false,
      statusName: null,
      message: error.message || String(error)
    });
  }
});

app.get("/", (_req, res) => {
  res.sendFile(path.join(__dirname, "public", "index.html"));
});

app.get("/results", (_req, res) => {
  res.sendFile(path.join(__dirname, "public", "results.html"));
});

const server = app.listen(PORT, HOST, () => {
  const loopbackUrl = `http://127.0.0.1:${PORT}`;
  const localUrl = `http://localhost:${PORT}`;
  console.log(`Automation Portal listening on ${HOST}:${PORT}`);
  console.log(`Open in your browser: ${loopbackUrl}  (or ${localUrl})`);

  const openBrowser =
    process.env.OPEN_BROWSER === "1" ||
    (process.env.OPEN_BROWSER !== "0" && process.platform === "win32");
  if (openBrowser) {
    openDefaultBrowser(loopbackUrl);
  }
});

server.on("error", (err) => {
  if (err.code === "EADDRINUSE") {
    console.error(
      `Port ${PORT} is already in use. Stop the other app or set PORT in .env to a free port (e.g. PORT=3001).`
    );
  } else {
    console.error("Server failed to start:", err.message);
  }
  process.exit(1);
});
