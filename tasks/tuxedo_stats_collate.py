"""Collect Tuxedo IO stats and combine online vs API servers."""

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

ONLINE_HOSTS = ("VLRENBPTCOLAP01", "VLRENBPTCOLAP02")
API_HOSTS = tuple(f"VLRENBPTTUXAP{index:02d}" for index in range(1, 11))
ABNORMAL_RESPONSE_SECONDS = float(os.environ.get("TUX_IO_ABNORMAL_RESPONSE_SECONDS") or "10")

# Replace a service TOTALS average before servers are combined.
# {"service": "csSvApiBan00", "avg": 0.10, "host": "VLRENBPTTUXAP01"}
RESPONSE_TIME_CORRECTIONS: list[dict] = []

_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_HOST_RE = re.compile(r"oper@([A-Za-z0-9]+)", re.IGNORECASE)
_METRIC_RE = re.compile(r"^(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)$")
_SERVICE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_FILE_RE = re.compile(r"report_\d{8}_\d{6}\.out")
_SAFE_FILE_RE = re.compile(r"^report_[A-Za-z0-9._-]+\.out$")


class CollateError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _normalize_time(value: str | None) -> str | None:
    match = _TIME_RE.match(str(value or "").strip())
    if not match:
        return None
    hours = int(match.group(1))
    minutes = int(match.group(2))
    if hours > 23 or minutes > 59:
        return None
    return f"{hours:02d}:{minutes:02d}"


def _normalize_date(value: str | None) -> str | None:
    match = _DATE_RE.match(str(value or "").strip())
    if not match:
        return None
    try:
        datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    return match.group(0)


def normalize_collate_input(body: dict | None) -> dict:
    payload = body or {}
    date = _normalize_date(payload.get("date"))
    start = _normalize_time(payload.get("from") or payload.get("start"))
    end = _normalize_time(payload.get("until") or payload.get("end"))
    if not date:
        raise CollateError("Enter the test date as YYYY-MM-DD.")
    if not start or not end:
        raise CollateError("Enter start and end times as 24-hour HH:MM. 9:16 AM is 09:16.")
    start_minutes = int(start[:2]) * 60 + int(start[3:])
    end_minutes = int(end[:2]) * 60 + int(end[3:])
    if end_minutes <= start_minutes:
        raise CollateError("End time must be later than start time on the same date.")
    return {"date": date, "from": start, "until": end}


def _round2(value: float) -> float:
    return round(float(value) + 1e-12, 2)


def _format_avg(value: float) -> str:
    return f"{_round2(value):.2f}"


def _parse_metric(token: str) -> tuple[float, float] | None:
    match = _METRIC_RE.match(token)
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


def _correction_for(host: str, service: str) -> dict | None:
    for rule in RESPONSE_TIME_CORRECTIONS:
        if rule.get("service") != service:
            continue
        rule_host = rule.get("host")
        if rule_host and rule_host != host:
            continue
        return rule
    return None


def _parse_block(block_text: str) -> dict | None:
    lines = block_text.split("\n")
    host_line = next((line for line in lines if line.strip().lower().startswith("oper@")), "")
    host_match = _HOST_RE.search(host_line)
    if not host_match:
        return None
    host = host_match.group(1).upper()
    start_after = ""
    end_before = ""
    services = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line or set(line.replace(" ", "")) <= {"-"}:
            continue
        if line.upper().startswith("START AFTER:"):
            start_after = line.split(":", 1)[1].strip()
            continue
        if line.upper().startswith("END BEFORE:"):
            end_before = line.split(":", 1)[1].strip()
            continue
        tokens = line.split()
        if len(tokens) < 2 or not _SERVICE_RE.match(tokens[0]):
            continue
        if tokens[0].upper() in {"SVCNAME", "TOTALS", "NUM"}:
            continue
        metrics = [_parse_metric(token) for token in tokens[1:]]
        if any(metric is None for metric in metrics):
            continue
        calls, avg = metrics[-1]
        rule = _correction_for(host, tokens[0])
        services.append(
            {
                "service": tokens[0],
                "calls": calls,
                "avg": float(rule["avg"]) if rule else avg,
                "reportedAvg": avg,
                "corrected": rule is not None,
            }
        )
    return {
        "host": host,
        "startAfter": start_after,
        "endBefore": end_before,
        "services": services,
    }


def parse_tuxedo_report(text: str) -> dict:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = []
    notes = []
    seen: dict[str, dict] = {}
    for part in re.split(r"\n(?=oper@)", normalized, flags=re.IGNORECASE):
        block = _parse_block(part)
        if not block:
            continue
        if block["host"] in seen:
            notes.append(f"Duplicate block for {block['host']}; the later block was used.")
        seen[block["host"]] = block
        blocks.append(block)
    return {"blocks": list(seen.values()), "notes": notes}


def _combine_services(blocks: list[dict]) -> dict:
    totals: dict[str, dict] = {}
    corrections = []
    for block in blocks:
        for row in block["services"]:
            if row["corrected"]:
                corrections.append(
                    {
                        "host": block["host"],
                        "service": row["service"],
                        "reportedAvg": _format_avg(row["reportedAvg"]),
                        "avg": _format_avg(row["avg"]),
                    }
                )
            current = totals.setdefault(row["service"], {"calls": 0.0, "weighted": 0.0})
            current["calls"] += row["calls"]
            current["weighted"] += row["calls"] * row["avg"]
    rows = []
    for service, current in totals.items():
        avg = current["weighted"] / current["calls"] if current["calls"] else 0
        rows.append(
            {
                "service": service,
                "avgResponseTime": _format_avg(avg),
                "totalCalls": int(current["calls"]) if current["calls"] == int(current["calls"]) else current["calls"],
            }
        )
    rows.sort(key=lambda row: (-row["totalCalls"], row["service"]))
    return {"rows": rows, "corrections": corrections}


def _group_report(all_blocks: list[dict], hosts: tuple[str, ...], title: str) -> dict:
    selected = []
    found = []
    for host in hosts:
        block = next((item for item in all_blocks if item["host"] == host), None)
        if not block:
            continue
        found.append(host)
        selected.append(block)
    missing = [host for host in hosts if host not in found]
    combined = _combine_services(selected)
    window = next((block for block in selected if block["startAfter"] or block["endBefore"]), {})
    return {
        "title": title,
        "hosts": found,
        "missing": missing,
        "startAfter": window.get("startAfter", ""),
        "endBefore": window.get("endBefore", ""),
        "rows": combined["rows"],
        "corrections": combined["corrections"],
    }


def collate_blocks(blocks: list[dict], notes: list[str] | None = None) -> dict:
    known = set(ONLINE_HOSTS) | set(API_HOSTS)
    online = _group_report(blocks, ONLINE_HOSTS, "Online report")
    api = _group_report(blocks, API_HOSTS, "API report")
    return {
        "notes": notes or [],
        "ignoredHosts": [block["host"] for block in blocks if block["host"] not in known],
        "online": online,
        "api": api,
        "corrections": online["corrections"] + api["corrections"],
    }


def collate_parsed_report(text: str) -> dict:
    parsed = parse_tuxedo_report(text)
    return collate_blocks(parsed["blocks"], parsed["notes"])


def find_abnormal_services(blocks: list[dict]) -> list[dict]:
    known = set(ONLINE_HOSTS) | set(API_HOSTS)
    abnormal = []
    for block in blocks:
        if block["host"] not in known:
            continue
        for row in block["services"]:
            if row["avg"] <= ABNORMAL_RESPONSE_SECONDS:
                continue
            abnormal.append(
                {
                    "host": block["host"],
                    "group": "Online" if block["host"] in ONLINE_HOSTS else "API",
                    "service": row["service"],
                    "avgResponseTime": _format_avg(row["avg"]),
                    "totalCalls": row["calls"],
                }
            )
    abnormal.sort(key=lambda item: (-float(item["avgResponseTime"]), item["host"], item["service"]))
    return abnormal


def _looks_like_report(text: str) -> bool:
    return bool(re.search(r"oper@", text, re.IGNORECASE) and re.search(r"SERVICE SUMMARY REPORT", text, re.IGNORECASE))


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


def _add_minutes(hhmm: str, minutes: int) -> str:
    start = int(hhmm[:2]) * 60 + int(hhmm[3:])
    end = start + minutes
    if end > 24 * 60:
        raise CollateError("The selected comparison period extends past midnight.")
    return f"{end // 60:02d}:{end % 60:02d}"


def _collect_report(window: dict) -> dict:
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
        report_text = _extract_report_text(
            _fetch_text(REPORT_URL, {"action": "download", "file": file_name})
        )
    else:
        report_text = _extract_report_text(collect_body)
    if not _looks_like_report(report_text):
        raise CollateError("The downloaded file is not a Tuxedo service summary report.", 502)
    return {"file": file_name, "reportText": report_text, "parsed": parse_tuxedo_report(report_text)}


def response_for_state(state: dict) -> dict:
    collated = collate_blocks(state["blocks"], state["notes"])
    return {
        "success": True,
        "file": state["file"],
        "window": {
            "date": state["window"]["date"],
            "from": state["window"]["from"],
            "until": state["window"]["until"],
            "startAfter": collated["online"]["startAfter"] or collated["api"]["startAfter"],
            "endBefore": collated["online"]["endBefore"] or collated["api"]["endBefore"],
        },
        "notes": collated["notes"],
        "ignoredHosts": collated["ignoredHosts"],
        "corrections": collated["corrections"],
        "online": {
            "title": collated["online"]["title"],
            "hosts": collated["online"]["hosts"],
            "missing": collated["online"]["missing"],
            "rows": collated["online"]["rows"],
        },
        "api": {
            "title": collated["api"]["title"],
            "hosts": collated["api"]["hosts"],
            "missing": collated["api"]["missing"],
            "rows": collated["api"]["rows"],
        },
        "abnormal": find_abnormal_services(state["blocks"]),
        "abnormalThreshold": ABNORMAL_RESPONSE_SECONDS,
        "comparisons": state["comparisons"],
    }


def collect_and_collate(body: dict | None) -> tuple[dict, dict]:
    window = normalize_collate_input(body)
    report = _collect_report(window)
    state = {
        "file": report["file"],
        "rawReport": report["reportText"],
        "blocks": copy.deepcopy(report["parsed"]["blocks"]),
        "notes": report["parsed"]["notes"],
        "window": window,
        "comparisons": [],
    }
    return state, response_for_state(state)


def compare_abnormal_services(state: dict, duration_minutes) -> dict:
    try:
        minutes = int(duration_minutes)
    except (TypeError, ValueError):
        minutes = 0
    if minutes < 1 or str(duration_minutes).strip() != str(minutes):
        raise CollateError("Enter a whole number of minutes for the comparison.")
    abnormal_before = find_abnormal_services(state["blocks"])
    if not abnormal_before:
        return response_for_state(state)

    compare_window = {
        "date": state["window"]["date"],
        "from": state["window"]["from"],
        "until": _add_minutes(state["window"]["from"], minutes),
    }
    original_end = int(state["window"]["until"][:2]) * 60 + int(state["window"]["until"][3:])
    compare_end = int(compare_window["until"][:2]) * 60 + int(compare_window["until"][3:])
    if compare_end > original_end:
        raise CollateError("The comparison period cannot be longer than the original test window.")

    comparison_report = _collect_report(compare_window)
    by_key = {}
    for block in comparison_report["parsed"]["blocks"]:
        for service in block["services"]:
            key = f"{block['host']}|{service['service']}"
            previous = by_key.get(key)
            if previous is None or service["avg"] < previous["avg"]:
                by_key[key] = service
    updates = []
    seen = set()
    for block in state["blocks"]:
        for original_service in block["services"]:
            if original_service["avg"] <= ABNORMAL_RESPONSE_SECONDS:
                continue
            key = f"{block['host']}|{original_service['service']}"
            replacement = by_key.get(key)
            if (
                not replacement
                or replacement["avg"] >= original_service["avg"]
                or replacement["avg"] > ABNORMAL_RESPONSE_SECONDS
            ):
                continue
            previous = original_service["avg"]
            calls = original_service["calls"]
            original_service["avg"] = replacement["avg"]
            original_service["corrected"] = True
            if key in seen:
                continue
            seen.add(key)
            updates.append(
                {
                    "host": block["host"],
                    "service": original_service["service"],
                    "from": _format_avg(previous),
                    "to": _format_avg(replacement["avg"]),
                    "totalCalls": calls,
                }
            )
    state["comparisons"].append(
        {
            "minutes": minutes,
            "file": comparison_report["file"],
            "compared": len(abnormal_before),
            "updates": updates,
        }
    )
    return response_for_state(state)
