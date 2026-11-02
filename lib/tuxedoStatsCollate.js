"use strict";

const cheerio = require("cheerio");

const REPORT_URL = (
  process.env.TUX_IO_REPORT_URL || "http://npe-services:8888/cgi-bin/TUX_IO_REPORT.py"
).trim();
const REQUEST_TIMEOUT_MS = parseInt(process.env.TUX_IO_REPORT_TIMEOUT_MS || "180000", 10);
const ABNORMAL_RESPONSE_TIME_SECONDS = Number(
  process.env.TUX_IO_ABNORMAL_RESPONSE_SECONDS || "10"
);

const ONLINE_SERVERS = Object.freeze([
  { host: "VLRENBPTCOLAP01", login: "petole1a@VLRENBPTCOLAP01" },
  { host: "VLRENBPTCOLAP02", login: "petole2a@VLRENBPTCOLAP02" }
]);
const API_SERVERS = Object.freeze(
  Array.from({ length: 10 }, (_, index) => {
    const host = `VLRENBPTTUXAP${String(index + 1).padStart(2, "0")}`;
    return { host, login: `petapia@${host}` };
  })
);
const ONLINE_HOSTS = Object.freeze(ONLINE_SERVERS.map((server) => server.host));
const API_HOSTS = Object.freeze(API_SERVERS.map((server) => server.host));
const LOGIN_BY_HOST = new Map(
  [...ONLINE_SERVERS, ...API_SERVERS].map((server) => [server.host, server.login])
);
const COMPARISON_PRESETS = Object.freeze([50, 40, 30]);

const KNOWN_HOST_RE = /\b(VLRENBPT(?:COLAP|TUXAP)\d{2})\b/i;
const GENERIC_HOST_RE = /\b[A-Za-z0-9_.-]+@([A-Za-z0-9]+)/;
const METRIC_RE = /^(\d+(?:\.\d+)?)\/(\d+(?:\.\d+)?)$/;
const SERVICE_RE = /^[A-Za-z][A-Za-z0-9_]*$/;
const HEADER_WORDS = new Set(["SVCNAME", "TOTALS", "NUM"]);

function normalizeTime(value) {
  const match = String(value || "")
    .trim()
    .match(/^(\d{1,2}):(\d{2})$/);
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return null;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

function normalizeDate(value) {
  const match = String(value || "")
    .trim()
    .match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  ) {
    return null;
  }
  return `${match[1]}-${match[2]}-${match[3]}`;
}

function minutesOfDay(hhmm) {
  const [hours, minutes] = hhmm.split(":").map(Number);
  return hours * 60 + minutes;
}

function timeOfDay(totalMinutes) {
  return `${String(Math.floor(totalMinutes / 60)).padStart(2, "0")}:${String(totalMinutes % 60).padStart(2, "0")}`;
}

function validationError(message) {
  const error = new Error(message);
  error.statusCode = 400;
  return error;
}

function normalizeCollateInput(body) {
  const date = normalizeDate(body && body.date);
  const from = normalizeTime(body && (body.from || body.start));
  const until = normalizeTime(body && (body.until || body.end));
  if (!date) {
    throw validationError("Enter the test date as YYYY-MM-DD.");
  }
  if (!from || !until) {
    throw validationError("Enter the test start and end times.");
  }
  if (minutesOfDay(until) <= minutesOfDay(from)) {
    throw validationError("End time must be later than start time on the same date.");
  }
  return { date, from, until };
}

function formatAvg(value) {
  return (Math.round((Number(value) + Number.EPSILON) * 100) / 100).toFixed(2);
}

function isSafeReportName(name) {
  return /^report_[A-Za-z0-9._-]+\.out$/.test(String(name || "").trim());
}

function parseMetric(token) {
  const match = String(token || "").match(METRIC_RE);
  if (!match) return null;
  return { calls: Number(match[1]), avg: Number(match[2]) };
}

function parseServiceLine(line) {
  const tokens = line.trim().split(/\s+/);
  if (tokens.length < 2) return null;
  const [service, ...metrics] = tokens;
  if (!SERVICE_RE.test(service) || HEADER_WORDS.has(service.toUpperCase())) return null;
  const totals = parseMetric(metrics[metrics.length - 1]);
  if (!totals) return null;
  if (!metrics.slice(0, -1).every((token) => parseMetric(token) || /^-+$/.test(token))) return null;
  return { service, calls: totals.calls, avg: totals.avg };
}

function hostFromLine(line) {
  const known = line.match(KNOWN_HOST_RE);
  if (known) return known[1].toUpperCase();
  const generic = line.match(GENERIC_HOST_RE);
  return generic ? generic[1].toUpperCase() : null;
}

function parseTuxedoReport(text) {
  const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
  const byHost = new Map();
  let current = null;

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;
    const row = parseServiceLine(line);
    if (!row) {
      const host = hostFromLine(line);
      if (host) {
        current = byHost.get(host);
        if (!current) {
          current = { host, startAfter: "", endBefore: "", sections: 0, services: new Map() };
          byHost.set(host, current);
        }
        current.sections += 1;
        continue;
      }
    }
    if (!current) continue;
    const startMatch = line.match(/^START AFTER:\s*(.+)$/i);
    if (startMatch) {
      current.startAfter = current.startAfter || startMatch[1].trim();
      continue;
    }
    const endMatch = line.match(/^END BEFORE:\s*(.+)$/i);
    if (endMatch) {
      current.endBefore = current.endBefore || endMatch[1].trim();
      continue;
    }
    if (!row) continue;
    const existing = current.services.get(row.service);
    if (existing) {
      existing.calls += row.calls;
      existing.weighted += row.calls * row.avg;
      existing.rows += 1;
    } else {
      current.services.set(row.service, {
        calls: row.calls,
        weighted: row.calls * row.avg,
        avg: row.avg,
        rows: 1
      });
    }
  }

  const notes = [];
  const blocks = [...byHost.values()].map((block) => {
    if (block.sections > 1) {
      notes.push(`${block.host} appeared ${block.sections} times in the NPE report; its rows were combined.`);
    }
    return {
      host: block.host,
      startAfter: block.startAfter,
      endBefore: block.endBefore,
      services: [...block.services.entries()].map(([service, value]) => {
        const avg = value.rows === 1 || value.calls === 0 ? value.avg : value.weighted / value.calls;
        return { service, calls: value.calls, avg, reportedAvg: avg, corrected: false };
      })
    };
  });
  return { blocks, notes };
}

function groupForHost(host) {
  if (ONLINE_HOSTS.includes(host)) return "Online";
  if (API_HOSTS.includes(host)) return "API";
  return null;
}

function findAbnormalServices(blocks) {
  const order = new Map([...ONLINE_HOSTS, ...API_HOSTS].map((host, index) => [host, index]));
  return blocks
    .filter((block) => groupForHost(block.host))
    .flatMap((block) =>
      block.services
        .filter((row) => row.avg > ABNORMAL_RESPONSE_TIME_SECONDS)
        .map((row) => ({
          host: block.host,
          login: LOGIN_BY_HOST.get(block.host),
          group: groupForHost(block.host),
          service: row.service,
          avgResponseTime: formatAvg(row.avg),
          totalCalls: row.calls
        }))
    )
    .sort(
      (left, right) =>
        order.get(left.host) - order.get(right.host) ||
        Number(right.avgResponseTime) - Number(left.avgResponseTime) ||
        left.service.localeCompare(right.service)
    );
}

function groupReport(blocks, servers, title, fileName) {
  const byHost = new Map(blocks.map((block) => [block.host, block]));
  const found = servers.filter((server) => byHost.has(server.host));
  const missing = servers.filter((server) => !byHost.has(server.host));
  const totals = new Map();
  for (const server of found) {
    for (const row of byHost.get(server.host).services) {
      const current = totals.get(row.service) || { calls: 0, weighted: 0 };
      current.calls += row.calls;
      current.weighted += row.calls * row.avg;
      totals.set(row.service, current);
    }
  }
  const rows = [...totals.entries()]
    .map(([service, current]) => ({
      service,
      avgResponseTime: formatAvg(current.calls > 0 ? current.weighted / current.calls : 0),
      totalCalls: current.calls
    }))
    .sort((left, right) => right.totalCalls - left.totalCalls || left.service.localeCompare(right.service));
  return {
    title,
    fileName,
    servers: found.map((server) => server.login),
    missing: missing.map((server) => server.login),
    rows
  };
}

function reportText(report, window) {
  const lines = [
    `PET Enhanced ${report.title}`,
    `Test window: ${window.date} ${window.from} - ${window.until}`,
    `Servers: ${report.servers.length ? report.servers.join(", ") : "none"}`
  ];
  if (report.missing.length) lines.push(`Missing servers: ${report.missing.join(", ")}`);
  lines.push("", "Tuxedo service\tAvg response time\tTotal no of calls");
  for (const row of report.rows) {
    lines.push(`${row.service}\t${row.avgResponseTime}\t${row.totalCalls}`);
  }
  return `${lines.join("\r\n")}\r\n`;
}

function cloneBlocks(blocks) {
  return blocks.map((block) => ({
    ...block,
    services: block.services.map((service) => ({ ...service }))
  }));
}

function extractReportFilename(html) {
  const $ = cheerio.load(String(html || ""));
  const fromClass = $(".filename").first().text().replace(/\s+/g, "").trim();
  if (isSafeReportName(fromClass)) return fromClass;
  const match = String(html || "").match(/report_\d{8}_\d{6}\.out/);
  if (match && isSafeReportName(match[0])) return match[0];
  return null;
}

function looksLikeReport(text) {
  return /SERVICE SUMMARY REPORT/i.test(text) && KNOWN_HOST_RE.test(text);
}

function extractReportText(body) {
  const text = String(body || "");
  if (looksLikeReport(text) && !/<html/i.test(text)) return text;
  const $ = cheerio.load(text);
  const pre = $("pre").first().text();
  if (looksLikeReport(pre)) return pre;
  const stripped = text.replace(/<[^>]+>/g, "\n");
  if (looksLikeReport(stripped)) return stripped;
  return text;
}

async function fetchText(url) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(url, { signal: controller.signal });
    const text = await response.text();
    if (!response.ok) {
      const error = new Error(`NPE service returned HTTP ${response.status}: ${text.slice(0, 300)}`);
      error.statusCode = 502;
      throw error;
    }
    return text;
  } catch (error) {
    if (error.name === "AbortError") {
      const timeoutError = new Error(
        `Tuxedo stats request timed out after ${Math.round(REQUEST_TIMEOUT_MS / 1000)} seconds.`
      );
      timeoutError.statusCode = 504;
      throw timeoutError;
    }
    if (error.statusCode) throw error;
    const wrapped = new Error(error.message || "Could not reach the Tuxedo stats service.");
    wrapped.statusCode = 502;
    throw wrapped;
  } finally {
    clearTimeout(timer);
  }
}

async function collectReport(input, log) {
  log.push(`Requesting Tuxedo stats from NPE services for ${input.date} ${input.from}–${input.until}.`);
  const collectUrl = new URL(REPORT_URL);
  collectUrl.searchParams.set("reporttype", "CollectTuxedoStats");
  collectUrl.searchParams.set("serverscount", "ALL");
  collectUrl.searchParams.set("date", input.date);
  collectUrl.searchParams.set("from", input.from);
  collectUrl.searchParams.set("until", input.until);

  const collectBody = await fetchText(collectUrl);
  let file = null;
  let reportText = collectBody;
  if (!looksLikeReport(collectBody) || /<html/i.test(collectBody)) {
    file = extractReportFilename(collectBody);
    if (!file) {
      const error = new Error(
        "The stats page did not include a report file name. Check the date and times, then try again."
      );
      error.statusCode = 502;
      throw error;
    }
    log.push(`NPE services created ${file}. Downloading it.`);
    const downloadUrl = new URL(REPORT_URL);
    downloadUrl.searchParams.set("action", "download");
    downloadUrl.searchParams.set("file", file);
    reportText = extractReportText(await fetchText(downloadUrl));
  } else {
    reportText = extractReportText(collectBody);
  }

  if (!looksLikeReport(reportText)) {
    const error = new Error("The downloaded file is not a Tuxedo service summary report.");
    error.statusCode = 502;
    throw error;
  }

  const parsed = parseTuxedoReport(reportText);
  log.push(`Read ${parsed.blocks.length} server section(s) from the report.`);
  return { file, reportText, parsed };
}

function coverageMessage(label, servers, blocks) {
  const hosts = new Set(blocks.map((block) => block.host));
  const missing = servers.filter((server) => !hosts.has(server.host)).map((server) => server.login);
  const found = servers.length - missing.length;
  return missing.length
    ? `${label} servers found: ${found} of ${servers.length}. Missing: ${missing.join(", ")}.`
    : `${label} servers found: ${found} of ${servers.length}.`;
}

function responseForState(state, log = []) {
  const online = groupReport(state.blocks, ONLINE_SERVERS, "Online report", "oln_report.txt");
  const api = groupReport(state.blocks, API_SERVERS, "API report", "api_report.txt");
  online.text = reportText(online, state.window);
  api.text = reportText(api, state.window);
  const windowHint = state.blocks.find((block) => block.startAfter || block.endBefore) || {};
  const start = minutesOfDay(state.window.from);
  const end = minutesOfDay(state.window.until);
  const known = new Set([...ONLINE_HOSTS, ...API_HOSTS]);
  return {
    success: true,
    file: state.file,
    window: {
      ...state.window,
      startAfter: windowHint.startAfter || "",
      endBefore: windowHint.endBefore || ""
    },
    notes: state.notes,
    ignoredHosts: state.blocks.map((block) => block.host).filter((host) => !known.has(host)),
    abnormalThreshold: ABNORMAL_RESPONSE_TIME_SECONDS,
    originalAbnormalCount: state.originalAbnormalCount,
    abnormal: findAbnormalServices(state.blocks),
    comparisons: state.comparisons,
    presets: COMPARISON_PRESETS.map((minutes) => ({ minutes, available: start + minutes <= end })),
    reports: { online, api },
    log
  };
}

async function collectAndCollate(body) {
  const input = normalizeCollateInput(body);
  const log = [`Test window: ${input.date} ${input.from}–${input.until}.`];
  const report = await collectReport(input, log);
  const state = {
    file: report.file,
    rawReport: report.reportText,
    blocks: cloneBlocks(report.parsed.blocks),
    notes: report.parsed.notes,
    window: input,
    comparisons: [],
    originalAbnormalCount: 0
  };
  log.push(coverageMessage("Online", ONLINE_SERVERS, state.blocks));
  log.push(coverageMessage("API", API_SERVERS, state.blocks));
  const abnormal = findAbnormalServices(state.blocks);
  state.originalAbnormalCount = abnormal.length;
  if (abnormal.length) {
    const servers = new Set(abnormal.map((item) => item.host)).size;
    log.push(
      `Found ${abnormal.length} abnormal response time(s) above ${ABNORMAL_RESPONSE_TIME_SECONDS} sec on ${servers} server(s). Choose a comparison window to correct them.`
    );
  } else {
    log.push(`No response times are above ${ABNORMAL_RESPONSE_TIME_SECONDS} sec. PET Enhanced reports are ready.`);
  }
  return { state, payload: responseForState(state, log) };
}

function comparisonWindow(testWindow, body) {
  const start = minutesOfDay(testWindow.from);
  const end = minutesOfDay(testWindow.until);
  const rawMinutes = body && body.minutes;
  if (rawMinutes !== undefined && rawMinutes !== null && rawMinutes !== "") {
    const minutes = Number(rawMinutes);
    if (!Number.isInteger(minutes) || minutes < 1) {
      throw validationError("Choose a whole number of minutes for the comparison.");
    }
    if (start + minutes > end) {
      throw validationError(
        `The test window is ${end - start} minutes, so the first ${minutes} minutes cannot be compared.`
      );
    }
    return {
      date: testWindow.date,
      from: testWindow.from,
      until: timeOfDay(start + minutes),
      label: `first ${minutes} min`
    };
  }
  const from = normalizeTime(body && body.from);
  const until = normalizeTime(body && body.until);
  if (!from || !until) {
    throw validationError("Choose a start and end time for the custom comparison range.");
  }
  if (minutesOfDay(until) <= minutesOfDay(from)) {
    throw validationError("The custom range end time must be later than its start time.");
  }
  if (minutesOfDay(from) < start || minutesOfDay(until) > end) {
    throw validationError(
      `The custom range must be inside the test window ${testWindow.from}–${testWindow.until}.`
    );
  }
  return { date: testWindow.date, from, until, label: `custom ${from}–${until}` };
}

async function compareAbnormalServices(state, body) {
  const window = comparisonWindow(state.window, body || {});
  const log = [`Comparison window: ${window.label} (${window.date} ${window.from}–${window.until}).`];
  const abnormalBefore = findAbnormalServices(state.blocks);
  if (!abnormalBefore.length) {
    log.push(`No response times are above ${ABNORMAL_RESPONSE_TIME_SECONDS} sec, so nothing needed to be compared.`);
    return responseForState(state, log);
  }
  log.push(`Checking ${abnormalBefore.length} abnormal response time(s) against the same server and service.`);

  const comparison = await collectReport(window, log);
  const lookup = new Map();
  for (const block of comparison.parsed.blocks) {
    for (const row of block.services) {
      lookup.set(`${block.host}|${row.service}`, row);
    }
  }

  let corrected = 0;
  let stillHigh = 0;
  let notFound = 0;
  for (const block of state.blocks) {
    if (!groupForHost(block.host)) continue;
    for (const row of block.services) {
      if (!(row.avg > ABNORMAL_RESPONSE_TIME_SECONDS)) continue;
      const replacement = lookup.get(`${block.host}|${row.service}`);
      if (!replacement) {
        notFound += 1;
        continue;
      }
      if (replacement.avg > ABNORMAL_RESPONSE_TIME_SECONDS) {
        stillHigh += 1;
        continue;
      }
      row.avg = replacement.avg;
      row.corrected = true;
      row.correctedBy = window.label;
      corrected += 1;
    }
  }

  log.push(
    `Updated ${corrected} response time(s) using the ${window.label} report. Total calls were kept from the full test report.`
  );
  if (stillHigh) {
    log.push(`${stillHigh} response time(s) were still above ${ABNORMAL_RESPONSE_TIME_SECONDS} sec in the ${window.label} report and were kept unchanged.`);
  }
  if (notFound) {
    log.push(`${notFound} service(s) had no calls on the same server in the ${window.label} report and were kept unchanged.`);
  }
  log.push("Recalculated the Online report (oln_report.txt) and API report (api_report.txt).");

  state.comparisons.push({
    label: window.label,
    from: window.from,
    until: window.until,
    file: comparison.file,
    checked: abnormalBefore.length,
    corrected,
    stillHigh,
    notFound
  });
  return responseForState(state, log);
}

module.exports = {
  ONLINE_HOSTS,
  API_HOSTS,
  ABNORMAL_RESPONSE_TIME_SECONDS,
  normalizeCollateInput,
  parseTuxedoReport,
  extractReportFilename,
  findAbnormalServices,
  collectAndCollate,
  compareAbnormalServices
};
