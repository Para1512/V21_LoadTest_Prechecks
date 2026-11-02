"use strict";

const assert = require("assert");
const { collectAndCollate, compareAbnormalServices } = require("../lib/tuxedoStatsCollate");

function section(host, rows) {
  return `oper@${host}:/home/oper> tuxstats
START AFTER: 2026-10-14 09:16
END BEFORE: 2026-10-14 10:16
SERVICE SUMMARY REPORT
SVCNAME          9a-10a     TOTALS
                 Num/Avg    Num/Avg
---------------  -------    -------
${rows.map(([name, calls, avg]) => `${name}  ${calls}/${avg}  ${calls}/${avg}`).join("\n")}
---------------  -------    -------
TOTALS           1/0.00     1/0.00
######################################`;
}

function report(slowAvg, goodAvg = "0.02") {
  return [
    section("VLRENBPTCOLAP01", [["goodSvc", 100, goodAvg], ["sharedSvc", 300, "0.40"]]),
    section("VLRENBPTCOLAP02", [["goodSvc", 100, "0.04"], ["slowSvc", 100, slowAvg], ["sharedSvc", 100, "0.80"]]),
    section("VLRENBPTTUXAP01", [["apiSvc", 10, "0.05"]]),
    section("VLRENBPTTUXAP02", [["apiSvc", 30, "0.09"]])
  ].join("\n");
}

const realFetch = global.fetch;
global.fetch = async (url) => {
  const target = new URL(String(url));
  if (target.searchParams.get("action") === "download") {
    const file = target.searchParams.get("file");
    return new Response(file.endsWith("_101600.out") ? report("20321") : report("0.12", "0.50"));
  }
  const until = target.searchParams.get("until").replace(":", "");
  return new Response(`<span class="filename">report_20261014_${until}00.out</span>`);
};

const row = (report, service) => report.rows.find((item) => item.service === service);

(async () => {
  try {
    const { state, payload } = await collectAndCollate({
      date: "2026-10-14",
      from: "09:16",
      until: "10:16"
    });
    assert.deepEqual(payload.reports.online.missing, []);
    assert.equal(payload.reports.api.servers.length, 2);
    assert.equal(payload.abnormal.length, 1);
    assert.equal(payload.abnormal[0].login, "petole2a@VLRENBPTCOLAP02");
    assert.equal(payload.abnormal[0].service, "slowSvc");
    assert.equal(row(payload.reports.online, "sharedSvc").avgResponseTime, "0.50");
    assert.equal(row(payload.reports.online, "sharedSvc").totalCalls, 400);
    assert.equal(row(payload.reports.api, "apiSvc").avgResponseTime, "0.08");
    assert.deepEqual(payload.presets.map((p) => p.available), [true, true, true]);

    const updated = await compareAbnormalServices(state, { minutes: 50 });
    assert.equal(updated.abnormal.length, 0);
    assert.equal(row(updated.reports.online, "slowSvc").avgResponseTime, "0.12");
    assert.equal(row(updated.reports.online, "slowSvc").totalCalls, 100);
    assert.equal(row(updated.reports.online, "goodSvc").avgResponseTime, "0.03");
    assert.equal(updated.comparisons[0].corrected, 1);
    assert.ok(updated.reports.online.text.includes("slowSvc\t0.12\t100"));
    assert.equal(updated.reports.online.fileName, "oln_report.txt");
    assert.equal(updated.reports.api.fileName, "api_report.txt");

    await assert.rejects(() => compareAbnormalServices(state, { from: "09:00", until: "09:40" }));
    console.log("Tuxedo collate comparison test passed.");
  } finally {
    global.fetch = realFetch;
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
