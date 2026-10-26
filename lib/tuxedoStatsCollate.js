"use strict";

const cheerio = require("cheerio");

const REPORT_URL = (
  process.env.TUX_IO_REPORT_URL || "http://npe-services:8888/cgi-bin/TUX_IO_REPORT.py"
).trim();
const REQUEST_TIMEOUT_MS = parseInt(process.env.TUX_IO_REPORT_TIMEOUT_MS || "180000", 10);

const ONLINE_HOSTS = Object.freeze(["VLRENBPTCOLAP01", "VLRENBPTCOLAP02"]);
const API_HOSTS = Object.freeze(
  Array.from({ length: 10 }, (_, index) => `VLRENBPTTUXAP${String(index + 1).padStart(2, "0")}`)
);

/**
 * Replace a service's reported TOTALS average before servers are combined.
 * Leave empty until the false-response-time rules are known.
 * { service, avg, host? } — host limits the override to one machine.
 */
const RESPONSE_TIME_CORRECTIONS = Object.freeze([]);

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
    throw validationError("Enter start and end times as 24-hour HH:MM. 9:16 AM is 09:16.");
  }
  if (minutesOfDay(until) <= minutesOfDay(from)) {
    throw validationError("End time must be later than start time on the same date.");
  }
  return { date, from, until };
}

function round2(value) {
  return Math.round((Number(value) + Number.EPSILON) * 100) / 100;
}

function formatAvg(value) {
  return round2(value).toFixed(2);
}

function isSafeReportName(name) {
  return /^report_[A-Za-z0-9._-]+\.out$/.test(String(name || "").trim());
}

function parseMetric(token) {
  const match = String(token || "").match(/^(\d+(?:\.\d+)?)\/(\d+(?:\.\d+)?)$/);
  if (!match) return null;
  return { calls: Number(match[1]), avg: Number(match[2]) };
}

function correctionFor(host, service) {
  return RESPONSE_TIME_CORRECTIONS.find(
    (rule) => rule.service === service && (!rule.host || rule.host === host)
  );
}

function parseBlock(blockText) {
  const lines = blockText.split("\n");
  const hostLine = lines.find((line) => /^\s*oper@/i.test(line));
  if (!hostLine) return null;
  const hostMatch = hostLine.match(/oper@([A-Za-z0-9]+)/i);
  if (!hostMatch) return null;
  const host = hostMatch[1].toUpperCase();
  let startAfter = "";
  let endBefore = "";
  const services = [];

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line || /^-+$/.test(line.replace(/\s+/g, ""))) continue;
    const startMatch = line.match(/^START AFTER:\s*(.+)$/i);
    if (startMatch) {
      startAfter = startMatch[1].trim();
      continue;
    }
    const endMatch = line.match(/^END BEFORE:\s*(.+)$/i);
    if (endMatch) {
      endBefore = endMatch[1].trim();
      continue;
    }
    const tokens = line.split(/\s+/);
    if (tokens.length < 2) continue;
    const service = tokens[0];
    if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(service)) continue;
    if (/^(SVCNAME|TOTALS|Num)$/i.test(service)) continue;
    const metrics = tokens.slice(1).map(parseMetric);
    if (metrics.some((metric) => !metric)) continue;
    const totals = metrics[metrics.length - 1];
    const rule = correctionFor(host, service);
    services.push({
      service,
      calls: totals.calls,
      avg: rule ? Number(rule.avg) : totals.avg,
      reportedAvg: totals.avg,
      corrected: Boolean(rule)
    });
  }

  return { host, startAfter, endBefore, services };
}

function parseTuxedoReport(text) {
  const normalized = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const blocks = normalized
    .split(/\n(?=oper@)/i)
    .map(parseBlock)
    .filter(Boolean);

  const byHost = new Map();
  const notes = [];
  for (const block of blocks) {
    if (byHost.has(block.host)) {
      notes.push(`Duplicate block for ${block.host}; the later block was used.`);
    }
    byHost.set(block.host, block);
  }
  return { blocks: [...byHost.values()], notes };
}

function combineServices(blocks) {
  const totals = new Map();
  const corrections = [];
  for (const block of blocks) {
    for (const row of block.services) {
      if (row.corrected) {
        corrections.push({
          host: block.host,
          service: row.service,
          reportedAvg: formatAvg(row.reportedAvg),
          avg: formatAvg(row.avg)
        });
      }
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

  return { rows, corrections };
}

function groupReport(allBlocks, hosts, title) {
  const found = [];
  const selected = [];
  for (const host of hosts) {
    const block = allBlocks.find((item) => item.host === host);
    if (!block) continue;
    found.push(host);
    selected.push(block);
  }
  const missing = hosts.filter((host) => !found.includes(host));
  const combined = combineServices(selected);
  const windowHint = selected.find((block) => block.startAfter || block.endBefore) || {};
  return {
    title,
    hosts: found,
    missing,
    startAfter: windowHint.startAfter || "",
    endBefore: windowHint.endBefore || "",
    rows: combined.rows,
    corrections: combined.corrections
  };
}

function collateParsedReport(text) {
  const parsed = parseTuxedoReport(text);
  const known = new Set([...ONLINE_HOSTS, ...API_HOSTS]);
  const online = groupReport(parsed.blocks, ONLINE_HOSTS, "Online report");
  const api = groupReport(parsed.blocks, API_HOSTS, "API report");
  return {
    notes: parsed.notes,
    ignoredHosts: parsed.blocks.map((block) => block.host).filter((host) => !known.has(host)),
    online,
    api,
    corrections: [...online.corrections, ...api.corrections]
  };
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
  return /oper@/i.test(text) && /SERVICE SUMMARY REPORT/i.test(text);
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

async function collectAndCollate(body) {
  const input = normalizeCollateInput(body);
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

  const collated = collateParsedReport(reportText);
  return {
    success: true,
    file,
    window: {
      date: input.date,
      from: input.from,
      until: input.until,
      startAfter: collated.online.startAfter || collated.api.startAfter || "",
      endBefore: collated.online.endBefore || collated.api.endBefore || ""
    },
    notes: collated.notes,
    ignoredHosts: collated.ignoredHosts,
    corrections: collated.corrections,
    online: {
      title: collated.online.title,
      hosts: collated.online.hosts,
      missing: collated.online.missing,
      rows: collated.online.rows
    },
    api: {
      title: collated.api.title,
      hosts: collated.api.hosts,
      missing: collated.api.missing,
      rows: collated.api.rows
    }
  };
}

module.exports = {
  ONLINE_HOSTS,
  API_HOSTS,
  RESPONSE_TIME_CORRECTIONS,
  normalizeCollateInput,
  parseTuxedoReport,
  collateParsedReport,
  extractReportFilename,
  collectAndCollate
};
