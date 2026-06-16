"use strict";

const NPE_BASE =
  (process.env.NPE_SERVICES_CGI_BASE || "http://npe-services:8888/cgi-bin").replace(/\/$/, "");

const FETCH_V21 = `${NPE_BASE}/fetchPetV21FsUsage`;
const FETCH_APILINK = `${NPE_BASE}/fetchPetAPILinkFsUsage`;
const CLEAN_V21 = `${NPE_BASE}/cleanPetV21Logs`;
const CLEAN_APILINK =
  process.env.LOG_CLEAN_APILINK_CGI || `${NPE_BASE}/cleanPetAPILinkLogs`;

const REQUEST_TIMEOUT_MS = parseInt(process.env.LOG_SPACE_REQUEST_TIMEOUT_MS || "120000", 10);

/** @type {{ host: string, group: string, kind: 'v21' | 'apilink' }[]} */
const SERVER_DEFINITIONS = [
  { host: "VLRENBPTCOLAP01", group: "TUX", kind: "v21" },
  { host: "VLRENBPTCOLAP02", group: "TUX", kind: "v21" },
  ...[
    "VLRENBPTTUXAP01",
    "VLRENBPTTUXAP02",
    "VLRENBPTTUXAP03",
    "VLRENBPTTUXAP04",
    "VLRENBPTTUXAP05",
    "VLRENBPTTUXAP06",
    "VLRENBPTTUXAP07",
    "VLRENBPTTUXAP08",
    "VLRENBPTTUXAP09",
    "VLRENBPTTUXAP10"
  ].map((host) => ({ host, group: "PRAPI", kind: "v21" })),
  ...["LDCPETAPICL01", "LDCPETAPICL02", "LDCPETAPICL03", "LDCPETAPICL04", "LDCPETAPICL05", "LDCPETAPICL06", "LDCPETAPICL07", "LDCPETAPICL08"].map(
    (host) => ({ host, group: "APILINK", kind: "apilink" })
  )
];

function usageUrl(host, kind) {
  const base = kind === "apilink" ? FETCH_APILINK : FETCH_V21;
  return `${base}?host=${encodeURIComponent(host)}`;
}

function cleanUrl(host, kind, datetime) {
  const base = kind === "apilink" ? CLEAN_APILINK : CLEAN_V21;
  const gzip = "off";
  return `${base}?host=${encodeURIComponent(host)}&gzip=${gzip}&datetime=${encodeURIComponent(datetime)}`;
}

function formatCutoffDatetime(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const h = String(d.getHours()).padStart(2, "0");
  const min = String(d.getMinutes()).padStart(2, "0");
  return `${y}-${m}-${day}T${h}:${min}`;
}

/** @param {number} daysBack */
function cutoffDateTimeDaysBack(daysBack) {
  const d = new Date(Date.now() - daysBack * 86400000);
  d.setHours(23, 59, 0, 0);
  return d;
}

/** @returns {number[]} */
function buildDaysBackSequence() {
  const seq = [15, 7];
  let last = 7;
  const min = 1 / 48; // half hour minimum step
  for (let i = 0; i < 24; i++) {
    last = last / 2;
    if (last < min) break;
    seq.push(last);
  }
  return seq;
}

function interpretCleanSuccess(text) {
  const raw = String(text || "").trim();
  if (!raw) return false;
  try {
    const j = JSON.parse(raw);
    const s = String(j.status ?? j.Status ?? j.state ?? "").toLowerCase();
    if (s === "success" || s === "ok") return true;
    if (j.success === true) return true;
  } catch {
    // fall through
  }
  return /"status"\s*:\s*"success"/i.test(raw) || /^success$/i.test(raw);
}

async function httpGet(url) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    const body = await res.text();
    return { ok: res.ok, status: res.status, body };
  } finally {
    clearTimeout(t);
  }
}

/**
 * @param {string} host
 * @param {'v21' | 'apilink'} kind
 * @returns {Promise<{ host: string, kind: string, usage: number | null, error?: string }>}
 */
async function fetchUsageForHost(host, kind) {
  const url = usageUrl(host, kind);
  try {
    const { ok, status, body } = await httpGet(url);
    if (!ok) {
      return { host, kind, usage: null, error: `HTTP ${status}` };
    }
    let parsed;
    try {
      parsed = JSON.parse(body);
    } catch {
      return { host, kind, usage: null, error: "Invalid JSON from usage endpoint" };
    }
    const raw = parsed.fsUsage ?? parsed.fsusage ?? parsed.usage;
    if (raw == null) {
      return { host, kind, usage: null, error: "Missing fsUsage in response" };
    }
    const n = parseInt(String(raw).replace(/%/g, "").trim(), 10);
    if (!Number.isFinite(n)) {
      return { host, kind, usage: null, error: `Unparseable fsUsage: ${raw}` };
    }
    return { host, kind, usage: n };
  } catch (e) {
    const msg = e && e.name === "AbortError" ? "Request timed out" : String(e.message || e);
    return { host, kind, usage: null, error: msg };
  }
}

async function fetchAllUsages(push, silent, emit) {
  if (!silent) {
    push("Checking usage for TUX, PRAPI, and APILINK servers (parallel fetch)…");
  }
  const tasks = SERVER_DEFINITIONS.map((def) =>
    fetchUsageForHost(def.host, def.kind).then((r) => ({ ...r, group: def.group }))
  );
  const rows = await Promise.all(tasks);
  if (emit) {
    emit({
      event: "usage_snapshot",
      servers: rows,
      updatedAt: new Date().toISOString()
    });
  }
  return rows;
}

function summarizeUsages(rows) {
  const withUsage = rows.filter((r) => typeof r.usage === "number");
  const warn = withUsage.filter((r) => r.usage > 60);
  const critical = withUsage.filter((r) => r.usage > 70);
  return { withUsage, warn, critical };
}

async function runCleanForHost(host, kind, group, daysBack, push, emit) {
  const dt = formatCutoffDatetime(cutoffDateTimeDaysBack(daysBack));
  const url = cleanUrl(host, kind, dt);
  push(`Removing files on ${host} (${group}) until ${dt} (≈ ${daysBack} day(s) back at 23:59)…`);
  if (emit) {
    emit({
      event: "clean_start",
      cutoffDatetime: dt,
      hosts: [host],
      group
    });
  }
  try {
    const { ok, status, body } = await httpGet(url);
    const bodyOk = interpretCleanSuccess(body);
    const success = Boolean(ok && bodyOk);
    if (!ok) {
      push(`Clean request failed for ${host}: HTTP ${status} — ${body.slice(0, 500)}`);
    } else if (!bodyOk) {
      push(`Clean response for ${host} did not indicate success. Body (truncated): ${body.slice(0, 500)}`);
    } else {
      push(`Clean completed for ${host} (service reported success).`);
    }
    return success;
  } catch (e) {
    const msg = e && e.name === "AbortError" ? "Request timed out" : String(e.message || e);
    push(`Clean request error for ${host}: ${msg}`);
    return false;
  }
}

/**
 * @param {(payload: { event: string } & Record<string, unknown>) => void} [emit]
 * @returns {Promise<object>}
 */
async function runServerLogSpaceCheck(emit) {
  let lastUsageUpdatedAt = null;
  const rawEmit = typeof emit === "function" ? emit : () => {};
  const safeEmit = (payload) => {
    if (payload && payload.event === "usage_snapshot" && payload.updatedAt) {
      lastUsageUpdatedAt = payload.updatedAt;
    }
    rawEmit(payload);
  };
  const activityLog = [];
  const push = (msg) => {
    activityLog.push(msg);
    safeEmit({ event: "log", message: msg });
  };

  let rows = await fetchAllUsages(push, false, safeEmit);
  let { warn, critical } = summarizeUsages(rows);

  if (warn.length) {
    push(`Warning: ${warn.length} host(s) above 60% log path usage: ${warn.map((w) => `${w.host} (${w.usage}%)`).join(", ")}`);
  } else {
    push("No hosts above 60% usage.");
  }

  const errors = rows.filter((r) => r.error);
  if (errors.length) {
    push(`Note: ${errors.length} host(s) could not be read: ${errors.map((e) => `${e.host}: ${e.error}`).join("; ")}`);
  }

  const cleanedHosts = [];
  const initialCriticalHosts = critical.map((r) => r.host);

  for (const hostName of initialCriticalHosts) {
    let hostRow = rows.find((r) => r.host === hostName);
    if (!hostRow || typeof hostRow.usage !== "number" || hostRow.usage <= 70) {
      continue;
    }
    const { host, kind, group } = hostRow;
    push(`--- ${host} (${group}) is above 70%; starting automated clean cycle ---`);
    const daysSeq = buildDaysBackSequence();
    for (let i = 0; i < daysSeq.length; i++) {
      const daysBack = daysSeq[i];
      const ok = await runCleanForHost(host, kind, group, daysBack, push, safeEmit);
      if (!ok) {
        push(`Stopping clean attempts for ${host} after failed request.`);
        cleanedHosts.push({ host, failed: true });
        safeEmit({ event: "clean_end", host, success: false });
        break;
      }
      push(`Re-checking usage for all servers after clean…`);
      rows = await fetchAllUsages(push, true, safeEmit);
      safeEmit({ event: "clean_end", host, success: true });
      hostRow = rows.find((r) => r.host === host);
      const u = hostRow && typeof hostRow.usage === "number" ? hostRow.usage : null;
      push(`${host} usage is now: ${u == null ? "unknown" : `${u}%`}`);
      if (u != null && u < 60) {
        push(`${host} is below 60%; stopping clean cycle for this host.`);
        cleanedHosts.push({ host, stoppedAtDaysBack: daysBack, finalUsage: u });
        break;
      }
      if (i === daysSeq.length - 1) {
        push(`${host} still at or above 60% after maximum clean steps.`);
        cleanedHosts.push({ host, stoppedAtDaysBack: daysBack, finalUsage: u, exhausted: true });
      }
    }
  }

  if (!initialCriticalHosts.length) {
    push("No hosts above 70%; no clean requests were sent.");
  }

  const finalSummary = summarizeUsages(rows);
  let finalWarningMessage = null;
  if (finalSummary.warn.length) {
    finalWarningMessage = `Still above 60% on: ${finalSummary.warn.map((w) => `${w.host} (${w.usage}%)`).join(", ")}`;
    push(`Final warning: ${finalWarningMessage}`);
  }

  return {
    success: true,
    type: "server-log-space",
    header: "Servers Log Space — automated check",
    hasUsageWarning: finalSummary.warn.length > 0,
    hasCriticalOver70: initialCriticalHosts.length > 0,
    lowSpaceThreshold: 60,
    criticalThreshold: 70,
    finalWarningMessage,
    lastUsageUpdatedAt: lastUsageUpdatedAt || new Date().toISOString(),
    servers: rows,
    activityLog,
    cleanedHosts
  };
}

module.exports = {
  runServerLogSpaceCheck,
  SERVER_DEFINITIONS
};
