"use strict";

const DEFAULT_BROWSE_BASE = "https://reqcentral.com/browse";

function inferFromHtml(html) {
  if (!html || typeof html !== "string") {
    return { statusName: "Unknown", completed: false, inProgress: false, uncertain: true };
  }
  const lower = html.toLowerCase();
  const titleMatch = html.match(/<title[^>]*>([^<]{1,240})<\/title>/i);
  const title = titleMatch ? titleMatch[1].trim() : "";

  if (/\bin progress\b/.test(lower)) {
    return { statusName: "In Progress", completed: false, inProgress: true };
  }
  if (/\b(done|closed|resolved)\b/.test(lower)) {
    return { statusName: "Done", completed: true, inProgress: false };
  }

  const loz = html.match(/jira-issue-status[^>]*>\s*([^<]{1,80})</i);
  if (loz) {
    const label = loz[1].trim();
    const l = label.toLowerCase();
    if (l.includes("progress")) {
      return { statusName: label, completed: false, inProgress: true };
    }
    if (/(done|closed|resolved|complete)/i.test(label)) {
      return { statusName: label, completed: true, inProgress: false };
    }
    return { statusName: label, completed: false, inProgress: false, uncertain: true };
  }

  return { statusName: title.slice(0, 120) || "Unknown", completed: false, inProgress: false, uncertain: true };
}

async function checkReqcentralIssue(issueKey) {
  const base = (process.env.REQCENTRAL_BROWSE_BASE || DEFAULT_BROWSE_BASE).replace(/\/+$/, "");
  const browseUrl = `${base}/${encodeURIComponent(issueKey)}`;
  const headers = {
    "User-Agent":
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    Accept: "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"
  };

  try {
    const r = await fetch(browseUrl, { headers, redirect: "follow" });
    if (!r.ok) {
      return {
        success: true,
        source: "reqcentral",
        browseUrl,
        completed: false,
        inProgress: false,
        statusName: null,
        message: `ReqCentral returned HTTP ${r.status}. Open the link manually to verify.`
      };
    }
    const html = await r.text();
    const inferred = inferFromHtml(html);
    return {
      success: true,
      source: "reqcentral",
      browseUrl,
      completed: inferred.completed,
      inProgress: inferred.inProgress,
      statusName: inferred.statusName,
      message:
        inferred.completed || inferred.inProgress
          ? null
          : inferred.uncertain
            ? "Could not confidently read status from the page. Open ReqCentral and confirm on the board."
            : null
    };
  } catch (error) {
    return {
      success: false,
      source: "reqcentral",
      browseUrl,
      completed: false,
      inProgress: false,
      statusName: null,
      message: error.message || String(error)
    };
  }
}

module.exports = { checkReqcentralIssue };
