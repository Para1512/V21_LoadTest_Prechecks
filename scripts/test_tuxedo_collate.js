"use strict";

const assert = require("assert");
const { collectAndCollate, compareAbnormalServices } = require("../lib/tuxedoStatsCollate");

function report(avg) {
  return `oper@VLRENBPTCOLAP01...
SERVICE SUMMARY REPORT
SVCNAME 9a-10a TOTALS
Num/Avg Num/Avg
--------------- -------
goodSvc 100/0.02 100/0.02
slowSvc 100/${avg} 100/${avg}
--------------- -------
TOTALS 200/0.00 200/0.00
######################################
oper@VLRENBPTTUXAP01...
SERVICE SUMMARY REPORT
SVCNAME 9a-10a TOTALS
Num/Avg Num/Avg
--------------- -------
apiSvc 10/0.05 10/0.05
--------------- -------
TOTALS 10/0.05 10/0.05
######################################`;
}

const realFetch = global.fetch;
global.fetch = async (url) => {
  const target = new URL(String(url));
  if (target.searchParams.get("action") === "download") {
    const file = target.searchParams.get("file");
    return new Response(file.includes("_100600") ? report("0.12") : report("20321"));
  }
  const until = target.searchParams.get("until").replace(":", "");
  return new Response(`<span class="filename">report_20261014_${until}00.out</span>`);
};

(async () => {
  try {
    const { state, payload } = await collectAndCollate({
      date: "2026-10-14",
      from: "09:16",
      until: "10:16"
    });
    assert.equal(payload.abnormal.length, 1);
    assert.equal(payload.abnormal[0].service, "slowSvc");
    const updated = await compareAbnormalServices(state, 50);
    assert.equal(updated.abnormal.length, 0);
    assert.equal(updated.online.rows.find((row) => row.service === "slowSvc").avgResponseTime, "0.12");
    assert.equal(updated.online.rows.find((row) => row.service === "slowSvc").totalCalls, 100);
    assert.equal(updated.comparisons[0].updates.length, 1);
    console.log("Tuxedo collate comparison test passed.");
  } finally {
    global.fetch = realFetch;
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
