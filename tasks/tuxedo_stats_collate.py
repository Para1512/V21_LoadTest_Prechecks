"""Collect Tuxedo IO stats, correct abnormal response times, and build Online/API reports."""

from __future__ import annotations

import copy
import os
import re
from datetime import datetime

import requests

REPORT_URL = (
    os.environ.get("TUX_IO_REPORT_URL") or "http://npe-services:8888/cgi-bin/TUX_IO_REPORT.py"
).strip()
REQUEST_TIMEOUT_S = int(os.environ.get("TUX_IO_REPORT_TIMEOUT_MS") or "180000") / 1000
ABNORMAL_RESPONSE_SECONDS = float(os.environ.get("TUX_IO_ABNORMAL_RESPONSE_SECONDS") or "10")

ONLINE_SERVERS = (
    {"host": "VLRENBPTCOLAP01", "login": "petole1a@VLRENBPTCOLAP01"},
    {"host": "VLRENBPTCOLAP02", "login": "petole2a@VLRENBPTCOLAP02"},
)
API_SERVERS = tuple(
    {"host": f"VLRENBPTTUXAP{index:02d}", "login": f"petapia@VLRENBPTTUXAP{index:02d}"}
    for index in range(1, 11)
)
ONLINE_HOSTS = tuple(server["host"] for server in ONLINE_SERVERS)
API_HOSTS = tuple(server["host"] for server in API_SERVERS)
LOGIN_BY_HOST = {server["host"]: server["login"] for server in ONLINE_SERVERS + API_SERVERS}
COMPARISON_PRESETS = (50, 40, 30)

_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_KNOWN_HOST_RE = re.compile(r"\b(VLRENBPT(?:COLAP|TUXAP)\d{2})\b", re.IGNORECASE)
_GENERIC_HOST_RE = re.compile(r"\b[A-Za-z0-9_.-]+@([A-Za-z0-9]+)")
_METRIC_RE = re.compile(r"^(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)$")
_SERVICE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_HEADER_WORDS = {"SVCNAME", "TOTALS", "NUM"}
_FILE_RE = re.compile(r"report_\d{8}_\d{6}\.out")
_SAFE_FILE_RE = re.compile(r"^report_[A-Za-z0-9._-]+\.out$")


class CollateError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _normalize_time(value) -> str | None:
    match = _TIME_RE.match(str(value or "").strip())
    if not match:
        return None
    hours = int(match.group(1))
    minutes = int(match.group(2))
    if hours > 23 or minutes > 59:
        return None
    return f"{hours:02d}:{minutes:02d}"


def _normalize_date(value) -> str | None:
    match = _DATE_RE.match(str(value or "").strip())
    if not match:
        return None
    try:
        datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    return match.group(0)


def _minutes_of_day(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def _time_of_day(total_minutes: int) -> str:
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def normalize_collate_input(body: dict | None) -> dict:
    payload = body or {}
    date = _normalize_date(payload.get("date"))
    start = _normalize_time(payload.get("from") or payload.get("start"))
    end = _normalize_time(payload.get("until") or payload.get("end"))
    if not date:
        raise CollateError("Enter the test date as YYYY-MM-DD.")
    if not start or not end:
        raise CollateError("Enter the test start and end times.")
    if _minutes_of_day(end) <= _minutes_of_day(start):
        raise CollateError("End time must be later than start time on the same date.")
    return {"date": date, "from": start, "until": end}


def _format_avg(value: float) -> str:
    return f"{round(float(value) + 1e-12, 2):.2f}"


def _format_calls(value: float):
    return int(value) if float(value).is_integer() else value


def _parse_metric(token: str) -> tuple[float, float] | None:
    match = _METRIC_RE.match(token)
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


def _parse_service_line(line: str) -> dict | None:
    tokens = line.split()
    if len(tokens) < 2:
        return None
    service, metrics = tokens[0], tokens[1:]
    if not _SERVICE_RE.match(service) or service.upper() in _HEADER_WORDS:
        return None
    totals = _parse_metric(metrics[-1])
    if not totals:
        return None
    if not all(_parse_metric(token) or re.fullmatch(r"-+", token) for token in metrics[:-1]):
        return None
    return {"service": service, "calls": totals[0], "avg": totals[1]}


def _host_from_line(line: str) -> str | None:
    known = _KNOWN_HOST_RE.search(line)
    if known:
        return known.group(1).upper()
    generic = _GENERIC_HOST_RE.search(line)
    return generic.group(1).upper() if generic else None


def parse_tuxedo_report(text: str) -> dict:
    by_host: dict[str, dict] = {}
    current = None
    for raw_line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        row = _parse_service_line(line)
        if not row:
            host = _host_from_line(line)
            if host:
                current = by_host.setdefault(
                    host,
                    {"host": host, "startAfter": "", "endBefore": "", "sections": 0, "services": {}},
                )
                current["sections"] += 1
                continue
        if current is None:
            continue
        if line.upper().startswith("START AFTER:"):
            current["startAfter"] = current["startAfter"] or line.split(":", 1)[1].strip()
            continue
        if line.upper().startswith("END BEFORE:"):
            current["endBefore"] = current["endBefore"] or line.split(":", 1)[1].strip()
            continue
        if not row:
            continue
        existing = current["services"].get(row["service"])
        if existing:
            existing["calls"] += row["calls"]
            existing["weighted"] += row["calls"] * row["avg"]
            existing["rows"] += 1
        else:
            current["services"][row["service"]] = {
                "calls": row["calls"],
                "weighted": row["calls"] * row["avg"],
                "avg": row["avg"],
                "rows": 1,
            }

    notes = []
    blocks = []
    for block in by_host.values():
        if block["sections"] > 1:
            notes.append(
                f"{block['host']} appeared {block['sections']} times in the NPE report; its rows were combined."
            )
        services = []
        for service, value in block["services"].items():
            avg = value["avg"] if value["rows"] == 1 or not value["calls"] else value["weighted"] / value["calls"]
            services.append(
                {"service": service, "calls": value["calls"], "avg": avg, "reportedAvg": avg, "corrected": False}
            )
        blocks.append(
            {
                "host": block["host"],
                "startAfter": block["startAfter"],
                "endBefore": block["endBefore"],
                "services": services,
            }
        )
    return {"blocks": blocks, "notes": notes}


def _group_for_host(host: str) -> str | None:
    if host in ONLINE_HOSTS:
        return "Online"
    if host in API_HOSTS:
        return "API"
    return None


def find_abnormal_services(blocks: list[dict]) -> list[dict]:
    order = {host: index for index, host in enumerate(ONLINE_HOSTS + API_HOSTS)}
    abnormal = []
    for block in blocks:
        group = _group_for_host(block["host"])
        if not group:
            continue
        for row in block["services"]:
            if row["avg"] <= ABNORMAL_RESPONSE_SECONDS:
                continue
            abnormal.append(
                {
                    "host": block["host"],
                    "login": LOGIN_BY_HOST[block["host"]],
                    "group": group,
                    "service": row["service"],
                    "avgResponseTime": _format_avg(row["avg"]),
                    "totalCalls": _format_calls(row["calls"]),
                }
            )
    abnormal.sort(
        key=lambda item: (order[item["host"]], -float(item["avgResponseTime"]), item["service"])
    )
    return abnormal


def _group_report(blocks: list[dict], servers: tuple[dict, ...], title: str, file_name: str) -> dict:
    by_host = {block["host"]: block for block in blocks}
    found = [server for server in servers if server["host"] in by_host]
    missing = [server for server in servers if server["host"] not in by_host]
    totals: dict[str, dict] = {}
    for server in found:
        for row in by_host[server["host"]]["services"]:
            current = totals.setdefault(row["service"], {"calls": 0.0, "weighted": 0.0})
            current["calls"] += row["calls"]
            current["weighted"] += row["calls"] * row["avg"]
    rows = [
        {
            "service": service,
            "avgResponseTime": _format_avg(current["weighted"] / current["calls"] if current["calls"] else 0),
            "totalCalls": _format_calls(current["calls"]),
        }
        for service, current in totals.items()
    ]
    rows.sort(key=lambda row: (-row["totalCalls"], row["service"]))
    return {
        "title": title,
        "fileName": file_name,
        "servers": [server["login"] for server in found],
        "missing": [server["login"] for server in missing],
        "rows": rows,
    }


def _report_text(report: dict, window: dict) -> str:
    lines = [
        f"PET Enhanced {report['title']}",
        f"Test window: {window['date']} {window['from']} - {window['until']}",
        f"Servers: {', '.join(report['servers']) if report['servers'] else 'none'}",
    ]
    if report["missing"]:
        lines.append(f"Missing servers: {', '.join(report['missing'])}")
    lines.extend(["", "Tuxedo service\tAvg response time\tTotal no of calls"])
    for row in report["rows"]:
        lines.append(f"{row['service']}\t{row['avgResponseTime']}\t{row['totalCalls']}")
    return "\r\n".join(lines) + "\r\n"


def _looks_like_report(text: str) -> bool:
    return bool(
        re.search(r"SERVICE SUMMARY REPORT", text, re.IGNORECASE) and _KNOWN_HOST_RE.search(text)
    )


def extract_report_filename(html: str) -> str | None:
    match = re.search(r'class="filename">\s*([^<]+?)\s*<', html or "")
    if match and _SAFE_FILE_RE.match(match.group(1).strip()):
        return match.group(1).strip()
    match = _FILE_RE.search(html or "")
    if match and _SAFE_FILE_RE.match(match.group(0)):
        return match.group(0)
    return None


def _extract_report_text(body: str) -> str:
    text = str(body or "")
    if _looks_like_report(text) and not re.search(r"<html", text, re.IGNORECASE):
        return text
    pre = re.search(r"<pre[^>]*>(.*?)</pre>", text, flags=re.IGNORECASE | re.DOTALL)
    if pre:
        inner = re.sub(r"<[^>]+>", "", pre.group(1))
        if _looks_like_report(inner):
            return inner
    stripped = re.sub(r"<[^>]+>", "\n", text)
    if _looks_like_report(stripped):
        return stripped
    return text


def _fetch_text(url: str, params: dict | None = None) -> str:
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_S)
    except requests.Timeout as exc:
        raise CollateError(
            f"Tuxedo stats request timed out after {int(REQUEST_TIMEOUT_S)} seconds.",
            504,
        ) from exc
    except requests.RequestException as exc:
        raise CollateError(str(exc) or "Could not reach the Tuxedo stats service.", 502) from exc
    if response.status_code >= 400:
        raise CollateError(
            f"NPE service returned HTTP {response.status_code}: {response.text[:300]}",
            502,
        )
    return response.text


def _collect_report(window: dict, log: list[str]) -> dict:
    log.append(
        f"Requesting Tuxedo stats from NPE services for {window['date']} {window['from']}–{window['until']}."
    )
    collect_body = _fetch_text(
        REPORT_URL,
        {
            "reporttype": "CollectTuxedoStats",
            "serverscount": "ALL",
            "date": window["date"],
            "from": window["from"],
            "until": window["until"],
        },
    )
    file_name = None
    if not _looks_like_report(collect_body) or re.search(r"<html", collect_body, re.IGNORECASE):
        file_name = extract_report_filename(collect_body)
        if not file_name:
            raise CollateError(
                "The stats page did not include a report file name. Check the date and times, then try again.",
                502,
            )
        log.append(f"NPE services created {file_name}. Downloading it.")
        report_text = _extract_report_text(
            _fetch_text(REPORT_URL, {"action": "download", "file": file_name})
        )
    else:
        report_text = _extract_report_text(collect_body)
    if not _looks_like_report(report_text):
        raise CollateError("The downloaded file is not a Tuxedo service summary report.", 502)
    parsed = parse_tuxedo_report(report_text)
    log.append(f"Read {len(parsed['blocks'])} server section(s) from the report.")
    return {"file": file_name, "reportText": report_text, "parsed": parsed}


def _coverage_message(label: str, servers: tuple[dict, ...], blocks: list[dict]) -> str:
    hosts = {block["host"] for block in blocks}
    missing = [server["login"] for server in servers if server["host"] not in hosts]
    found = len(servers) - len(missing)
    if missing:
        return f"{label} servers found: {found} of {len(servers)}. Missing: {', '.join(missing)}."
    return f"{label} servers found: {found} of {len(servers)}."


def response_for_state(state: dict, log: list[str] | None = None) -> dict:
    online = _group_report(state["blocks"], ONLINE_SERVERS, "Online report", "oln_report.txt")
    api = _group_report(state["blocks"], API_SERVERS, "API report", "api_report.txt")
    online["text"] = _report_text(online, state["window"])
    api["text"] = _report_text(api, state["window"])
    window_hint = next(
        (block for block in state["blocks"] if block["startAfter"] or block["endBefore"]), {}
    )
    start = _minutes_of_day(state["window"]["from"])
    end = _minutes_of_day(state["window"]["until"])
    known = set(ONLINE_HOSTS) | set(API_HOSTS)
    return {
        "success": True,
        "file": state["file"],
        "window": {
            **state["window"],
            "startAfter": window_hint.get("startAfter", ""),
            "endBefore": window_hint.get("endBefore", ""),
        },
        "notes": state["notes"],
        "ignoredHosts": [block["host"] for block in state["blocks"] if block["host"] not in known],
        "abnormalThreshold": ABNORMAL_RESPONSE_SECONDS,
        "originalAbnormalCount": state["originalAbnormalCount"],
        "abnormal": find_abnormal_services(state["blocks"]),
        "comparisons": state["comparisons"],
        "presets": [
            {"minutes": minutes, "available": start + minutes <= end} for minutes in COMPARISON_PRESETS
        ],
        "reports": {"online": online, "api": api},
        "log": log or [],
    }


def collect_and_collate(body: dict | None) -> tuple[dict, dict]:
    window = normalize_collate_input(body)
    log = [f"Test window: {window['date']} {window['from']}–{window['until']}."]
    report = _collect_report(window, log)
    state = {
        "file": report["file"],
        "rawReport": report["reportText"],
        "blocks": copy.deepcopy(report["parsed"]["blocks"]),
        "notes": report["parsed"]["notes"],
        "window": window,
        "comparisons": [],
        "originalAbnormalCount": 0,
    }
    log.append(_coverage_message("Online", ONLINE_SERVERS, state["blocks"]))
    log.append(_coverage_message("API", API_SERVERS, state["blocks"]))
    abnormal = find_abnormal_services(state["blocks"])
    state["originalAbnormalCount"] = len(abnormal)
    if abnormal:
        servers = len({item["host"] for item in abnormal})
        log.append(
            f"Found {len(abnormal)} abnormal response time(s) above {ABNORMAL_RESPONSE_SECONDS:g} sec "
            f"on {servers} server(s). Choose a comparison window to correct them."
        )
    else:
        log.append(
            f"No response times are above {ABNORMAL_RESPONSE_SECONDS:g} sec. PET Enhanced reports are ready."
        )
    return state, response_for_state(state, log)


def _comparison_window(test_window: dict, body: dict) -> dict:
    start = _minutes_of_day(test_window["from"])
    end = _minutes_of_day(test_window["until"])
    raw_minutes = body.get("minutes")
    if raw_minutes not in (None, ""):
        try:
            minutes = int(raw_minutes)
        except (TypeError, ValueError):
            minutes = 0
        if minutes < 1 or str(raw_minutes).strip() != str(minutes):
            raise CollateError("Choose a whole number of minutes for the comparison.")
        if start + minutes > end:
            raise CollateError(
                f"The test window is {end - start} minutes, so the first {minutes} minutes cannot be compared."
            )
        return {
            "date": test_window["date"],
            "from": test_window["from"],
            "until": _time_of_day(start + minutes),
            "label": f"first {minutes} min",
        }
    range_from = _normalize_time(body.get("from"))
    range_until = _normalize_time(body.get("until"))
    if not range_from or not range_until:
        raise CollateError("Choose a start and end time for the custom comparison range.")
    if _minutes_of_day(range_until) <= _minutes_of_day(range_from):
        raise CollateError("The custom range end time must be later than its start time.")
    if _minutes_of_day(range_from) < start or _minutes_of_day(range_until) > end:
        raise CollateError(
            f"The custom range must be inside the test window {test_window['from']}–{test_window['until']}."
        )
    return {
        "date": test_window["date"],
        "from": range_from,
        "until": range_until,
        "label": f"custom {range_from}–{range_until}",
    }


def compare_abnormal_services(state: dict, body: dict | None) -> dict:
    window = _comparison_window(state["window"], body or {})
    log = [f"Comparison window: {window['label']} ({window['date']} {window['from']}–{window['until']})."]
    abnormal_before = find_abnormal_services(state["blocks"])
    if not abnormal_before:
        log.append(
            f"No response times are above {ABNORMAL_RESPONSE_SECONDS:g} sec, so nothing needed to be compared."
        )
        return response_for_state(state, log)
    log.append(
        f"Checking {len(abnormal_before)} abnormal response time(s) against the same server and service."
    )

    comparison = _collect_report(window, log)
    lookup = {
        f"{block['host']}|{row['service']}": row
        for block in comparison["parsed"]["blocks"]
        for row in block["services"]
    }

    corrected = still_high = not_found = 0
    for block in state["blocks"]:
        if not _group_for_host(block["host"]):
            continue
        for row in block["services"]:
            if row["avg"] <= ABNORMAL_RESPONSE_SECONDS:
                continue
            replacement = lookup.get(f"{block['host']}|{row['service']}")
            if not replacement:
                not_found += 1
                continue
            if replacement["avg"] > ABNORMAL_RESPONSE_SECONDS:
                still_high += 1
                continue
            row["avg"] = replacement["avg"]
            row["corrected"] = True
            row["correctedBy"] = window["label"]
            corrected += 1

    log.append(
        f"Updated {corrected} response time(s) using the {window['label']} report. "
        "Total calls were kept from the full test report."
    )
    if still_high:
        log.append(
            f"{still_high} response time(s) were still above {ABNORMAL_RESPONSE_SECONDS:g} sec in the "
            f"{window['label']} report and were kept unchanged."
        )
    if not_found:
        log.append(
            f"{not_found} service(s) had no calls on the same server in the {window['label']} report "
            "and were kept unchanged."
        )
    log.append("Recalculated the Online report (oln_report.txt) and API report (api_report.txt).")

    state["comparisons"].append(
        {
            "label": window["label"],
            "from": window["from"],
            "until": window["until"],
            "file": comparison["file"],
            "checked": len(abnormal_before),
            "corrected": corrected,
            "stillHigh": still_high,
            "notFound": not_found,
        }
    )
    return response_for_state(state, log)
