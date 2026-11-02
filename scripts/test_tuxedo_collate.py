"""Offline check for tasks/tuxedo_stats_collate.py (mocks the NPE service)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tasks import tuxedo_stats_collate as collate  # noqa: E402


def section(host, rows):
    body = "\n".join(f"{name}  {calls}/{avg}  {calls}/{avg}" for name, calls, avg in rows)
    return f"""oper@{host}:/home/oper> tuxstats
START AFTER: 2026-10-14 09:16
END BEFORE: 2026-10-14 10:16
SERVICE SUMMARY REPORT
SVCNAME          9a-10a     TOTALS
                 Num/Avg    Num/Avg
---------------  -------    -------
{body}
---------------  -------    -------
TOTALS           1/0.00     1/0.00
######################################"""


def report(slow_avg, good_avg="0.02"):
    return "\n".join(
        [
            section("VLRENBPTCOLAP01", [("goodSvc", 100, good_avg), ("sharedSvc", 300, "0.40")]),
            section(
                "VLRENBPTCOLAP02",
                [("goodSvc", 100, "0.04"), ("slowSvc", 100, slow_avg), ("sharedSvc", 100, "0.80")],
            ),
            section("VLRENBPTTUXAP01", [("apiSvc", 10, "0.05")]),
            section("VLRENBPTTUXAP02", [("apiSvc", 30, "0.09")]),
        ]
    )


class FakeResponse:
    def __init__(self, text):
        self.text = text
        self.status_code = 200


def fake_get(url, params=None, timeout=None):
    params = params or {}
    if params.get("action") == "download":
        if params["file"].endswith("_101600.out"):
            return FakeResponse(report("20321"))
        return FakeResponse(report("0.12", "0.50"))
    until = params["until"].replace(":", "")
    return FakeResponse(f'<span class="filename">report_20261014_{until}00.out</span>')


def row(rep, service):
    return next(item for item in rep["rows"] if item["service"] == service)


def main():
    collate.requests.get = fake_get
    state, payload = collate.collect_and_collate({"date": "2026-10-14", "from": "09:16", "until": "10:16"})
    assert payload["reports"]["online"]["missing"] == []
    assert len(payload["reports"]["api"]["servers"]) == 2
    assert len(payload["abnormal"]) == 1, payload["abnormal"]
    assert payload["abnormal"][0]["login"] == "petole2a@VLRENBPTCOLAP02"
    assert row(payload["reports"]["online"], "sharedSvc")["avgResponseTime"] == "0.50"
    assert row(payload["reports"]["online"], "sharedSvc")["totalCalls"] == 400
    assert row(payload["reports"]["api"], "apiSvc")["avgResponseTime"] == "0.08"

    updated = collate.compare_abnormal_services(state, {"minutes": 50})
    assert updated["abnormal"] == []
    assert row(updated["reports"]["online"], "slowSvc")["avgResponseTime"] == "0.12"
    assert row(updated["reports"]["online"], "slowSvc")["totalCalls"] == 100
    assert row(updated["reports"]["online"], "goodSvc")["avgResponseTime"] == "0.03"
    assert updated["comparisons"][0]["corrected"] == 1
    assert "slowSvc\t0.12\t100" in updated["reports"]["online"]["text"]

    try:
        collate.compare_abnormal_services(state, {"from": "09:00", "until": "09:40"})
        raise AssertionError("custom range outside the test window should fail")
    except collate.CollateError:
        pass
    print("Python Tuxedo collate comparison test passed.")


if __name__ == "__main__":
    main()
