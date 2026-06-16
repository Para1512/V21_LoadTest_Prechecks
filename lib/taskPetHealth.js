"use strict";

const cheerio = require("cheerio");

const NPE_BASE =
  (process.env.NPE_SERVICES_CGI_BASE || "http://npe-services:8888/cgi-bin").replace(/\/$/, "");
const PET_PATH = (process.env.PET1_V21_HEALTH_CGI || "PET1-V21").replace(/^\//, "");
const PET_HEALTH_URL =
  String(process.env.PET1_V21_HEALTH_URL || "").trim() || `${NPE_BASE}/${PET_PATH}`;

const REQUEST_TIMEOUT_MS = parseInt(process.env.PET_HEALTH_REQUEST_TIMEOUT_MS || "60000", 10);

function normalizeStatus(raw) {
  const s = String(raw || "").trim().toLowerCase();
  if (!s) return "unknown";
  if (/^(up|online|running|ok|active|started|good|live)$/.test(s)) return "up";
  if (/^(down|offline|stopped|dead|inactive|fail|failed|error|bad)$/.test(s)) return "down";
  return "unknown";
}

function inferGroupFromHost(server) {
  const u = String(server || "").toUpperCase();
  if (u.includes("TUXAP")) return "PRAPI";
  if (u.includes("COLAP")) return "TUX";
  return "TUX";
}

function sliceAfter(html, startRe, endRe) {
  const idx = html.search(startRe);
  if (idx < 0) return "";
  const m = html.match(startRe);
  const from = idx + (m ? m[0].length : 0);
  const tail = html.slice(from);
  if (!endRe) return tail;
  const endIdx = tail.search(endRe);
  return endIdx >= 0 ? tail.slice(0, endIdx) : tail;
}

function parseTablesInFragment(htmlFragment, fixedGroup) {
  const $ = cheerio.load(htmlFragment);
  const seen = new Set();
  const rows = [];
  $("table").each((_, tbl) => {
    $(tbl).find("tr").each((__, tr) => {
      const cells = $(tr)
        .find("th, td")
        .map((___, c) => $(c).text().replace(/\s+/g, " ").trim())
        .get()
        .filter(Boolean);
      if (cells.length < 2) return;
      const server = cells[0];
      const statusRaw = cells[cells.length - 1];
      if (/^(server|host|machine|name)$/i.test(server) && /^status$/i.test(statusRaw)) return;
      if (/^status$/i.test(server)) return;
      if (!/^[A-Za-z0-9][A-Za-z0-9_.-]+$/.test(server) || server.length < 4) return;
      const group = fixedGroup;
      const key = `${group}:${server}`;
      if (seen.has(key)) return;
      seen.add(key);
      rows.push({
        group,
        server,
        label: group === "TUX" ? "TUX OLN" : "TUX API",
        status: statusRaw,
        normalized: normalizeStatus(statusRaw)
      });
    });
  });
  return rows;
}

function parseAllTablesInferGroup(html) {
  const $ = cheerio.load(html);
  const seen = new Set();
  const rows = [];
  $("table").each((_, tbl) => {
    $(tbl).find("tr").each((__, tr) => {
      const cells = $(tr)
        .find("th, td")
        .map((___, c) => $(c).text().replace(/\s+/g, " ").trim())
        .get()
        .filter(Boolean);
      if (cells.length < 2) return;
      const server = cells[0];
      const statusRaw = cells[cells.length - 1];
      if (/^(server|host|machine|name)$/i.test(server)) return;
      if (!/^[A-Za-z0-9][A-Za-z0-9_.-]+$/.test(server) || server.length < 4) return;
      const group = inferGroupFromHost(server);
      const key = `${group}:${server}`;
      if (seen.has(key)) return;
      seen.add(key);
      rows.push({
        group,
        server,
        label: group === "TUX" ? "TUX OLN" : "TUX API",
        status: statusRaw,
        normalized: normalizeStatus(statusRaw)
      });
    });
  });
  return rows;
}

function parseLoosePlainText(html) {
  const $ = cheerio.load(html);
  const plain = ($("body").length ? $("body") : $.root()).text();
  const compact = plain.replace(/\s+/g, " ");
  const re =
    /\b([A-Z]{2,}[A-Z0-9]*(?:PT)?(?:COLAP|TUXAP)[A-Z0-9]*)\b\s*(?:\|\s*|[-–:]\s*|\s+)\s*(UP|DOWN)\b/gi;
  const seen = new Set();
  const rows = [];
  let m;
  while ((m = re.exec(compact)) !== null) {
    const server = m[1].toUpperCase();
    const statusRaw = m[2];
    const group = inferGroupFromHost(server);
    const key = `${group}:${server}`;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({
      group,
      server,
      label: group === "TUX" ? "TUX OLN" : "TUX API",
      status: statusRaw,
      normalized: normalizeStatus(statusRaw)
    });
  }
  return rows;
}

function parsePetHealthHtml(html) {
  const olHtml = sliceAfter(html, /TUX\s+OLN(?:\s+Servers?)?/i, /TUX\s+API(?:\s+Servers?)?/i);
  const apiHtml = sliceAfter(html, /TUX\s+API(?:\s+Servers?)?/i, null);

  let servers = [];
  if (olHtml.trim()) servers.push(...parseTablesInFragment(olHtml, "TUX"));
  if (apiHtml.trim()) servers.push(...parseTablesInFragment(apiHtml, "PRAPI"));

  if (servers.length === 0) {
    servers = parseAllTablesInferGroup(html);
  }
  if (servers.length === 0) {
    servers = parseLoosePlainText(html);
  }

  const map = new Map();
  for (const r of servers) {
    map.set(`${r.group}:${r.server}`, r);
  }
  return [...map.values()];
}

function buildWarnings(servers) {
  const warnings = [];
  for (const s of servers) {
    if (s.normalized === "down") {
      warnings.push(`Warning: ${s.server} (${s.label}) is DOWN (reported: ${s.status}).`);
    }
  }
  return warnings;
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

async function runPetHealthCheck() {
  const { ok, status, body } = await httpGet(PET_HEALTH_URL);
  if (!ok) {
    return {
      success: false,
      message: `PET1-V21 request failed: HTTP ${status}`,
      petUrl: PET_HEALTH_URL
    };
  }

  const servers = parsePetHealthHtml(body);
  if (servers.length === 0) {
    return {
      success: false,
      message: "Could not parse TUX OLN / TUX API server rows from PET1-V21 response.",
      petUrl: PET_HEALTH_URL,
      previewExcerpt: body.replace(/\s+/g, " ").trim().slice(0, 500)
    };
  }

  const warnings = buildWarnings(servers);
  const hasUnknown = servers.some((s) => s.normalized === "unknown");
  const allUp = servers.every((s) => s.normalized === "up");

  return {
    success: true,
    type: "server-health-pet",
    header: "Server Health Check (PET1-V21)",
    petUrl: PET_HEALTH_URL,
    servers,
    allUp,
    warnings,
    hasUnknown,
    checkedAt: new Date().toISOString()
  };
}

module.exports = { runPetHealthCheck, PET_HEALTH_URL };
