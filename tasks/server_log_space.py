"""Task 3: automated log path usage via NPE CGI + optional clean when >70%."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import quote

import requests

NPE_BASE = (os.environ.get("NPE_SERVICES_CGI_BASE") or "http://npe-services:8888/cgi-bin").rstrip("/")
FETCH_V21 = f"{NPE_BASE}/fetchPetV21FsUsage"
FETCH_APILINK = f"{NPE_BASE}/fetchPetAPILinkFsUsage"
CLEAN_V21 = f"{NPE_BASE}/cleanPetV21Logs"
CLEAN_APILINK = os.environ.get("LOG_CLEAN_APILINK_CGI") or f"{NPE_BASE}/cleanPetAPILinkLogs"

REQUEST_TIMEOUT_MS = int(os.environ.get("LOG_SPACE_REQUEST_TIMEOUT_MS", "120000"))
REQUEST_TIMEOUT_S = max(REQUEST_TIMEOUT_MS / 1000.0, 5.0)

SERVER_DEFINITIONS: list[dict[str, str]] = [
    {"host": "VLRENBPTCOLAP01", "group": "TUX", "kind": "v21"},
    {"host": "VLRENBPTCOLAP02", "group": "TUX", "kind": "v21"},
    *[
        {"host": h, "group": "PRAPI", "kind": "v21"}
        for h in [
            "VLRENBPTTUXAP01",
            "VLRENBPTTUXAP02",
            "VLRENBPTTUXAP03",
            "VLRENBPTTUXAP04",
            "VLRENBPTTUXAP05",
            "VLRENBPTTUXAP06",
            "VLRENBPTTUXAP07",
            "VLRENBPTTUXAP08",
            "VLRENBPTTUXAP09",
            "VLRENBPTTUXAP10",
        ]
    ],
    *[
        {"host": h, "group": "APILINK", "kind": "apilink"}
        for h in [
            "LDCPETAPICL01",
            "LDCPETAPICL02",
            "LDCPETAPICL03",
            "LDCPETAPICL04",
            "LDCPETAPICL05",
            "LDCPETAPICL06",
            "LDCPETAPICL07",
            "LDCPETAPICL08",
        ]
    ],
]


def _usage_url(host: str, kind: str) -> str:
    base = FETCH_APILINK if kind == "apilink" else FETCH_V21
    return f"{base}?host={quote(host, safe='')}"


def _clean_url(host: str, kind: str, dt: str) -> str:
    base = CLEAN_APILINK if kind == "apilink" else CLEAN_V21
    return f"{base}?host={quote(host, safe='')}&gzip=off&datetime={quote(dt, safe='')}"


def _format_cutoff_datetime(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M")


def _cutoff_datetime_days_back(days_back: float) -> datetime:
    d = datetime.now() - timedelta(seconds=days_back * 86400.0)
    return d.replace(hour=23, minute=59, second=0, microsecond=0)


def _build_days_back_sequence() -> list[float]:
    seq = [15.0, 7.0]
    last = 7.0
    min_step = 1.0 / 48.0
    for _ in range(24):
        last = last / 2.0
        if last < min_step:
            break
        seq.append(last)
    return seq


def _interpret_clean_success(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    try:
        j = json.loads(raw)
        s = str(j.get("status") or j.get("Status") or j.get("state") or "").lower()
        if s in ("success", "ok"):
            return True
        if j.get("success") is True:
            return True
    except (json.JSONDecodeError, TypeError):
        pass
    return bool(re.search(r'"status"\s*:\s*"success"', raw, re.I)) or raw.lower() == "success"


def _http_get(url: str) -> tuple[bool, int, str]:
    r = requests.get(url, timeout=REQUEST_TIMEOUT_S)
    return r.ok, r.status_code, r.text


def fetch_usage_for_host(host: str, kind: str, group: str) -> dict[str, Any]:
    url = _usage_url(host, kind)
    try:
        ok, status, body = _http_get(url)
        if not ok:
            return {"host": host, "group": group, "kind": kind, "usage": None, "error": f"HTTP {status}"}
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return {"host": host, "group": group, "kind": kind, "usage": None, "error": "Invalid JSON from usage endpoint"}
        raw = parsed.get("fsUsage") or parsed.get("fsusage") or parsed.get("usage")
        if raw is None:
            return {"host": host, "group": group, "kind": kind, "usage": None, "error": "Missing fsUsage in response"}
        try:
            n = int(re.sub(r"%", "", str(raw).strip()))
        except ValueError:
            return {
                "host": host,
                "group": group,
                "kind": kind,
                "usage": None,
                "error": f"Unparseable fsUsage: {raw!r}",
            }
        return {"host": host, "group": group, "kind": kind, "usage": n}
    except requests.RequestException as e:
        return {"host": host, "group": group, "kind": kind, "usage": None, "error": str(e)[:500]}


def _fetch_all_usages_parallel() -> list[dict[str, Any]]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=20) as ex:
        futs = {
            ex.submit(fetch_usage_for_host, d["host"], d["kind"], d["group"]): d for d in SERVER_DEFINITIONS
        }
        for fut in as_completed(futs):
            rows.append(fut.result())
    rows.sort(key=lambda r: (r.get("group") or "", r.get("host") or ""))
    return rows


def summarize_usages(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    with_usage = [r for r in rows if isinstance(r.get("usage"), int)]
    warn = [r for r in with_usage if r["usage"] > 60]
    critical = [r for r in with_usage if r["usage"] > 70]
    return with_usage, warn, critical


def run_clean_for_host(
    host: str,
    kind: str,
    group: str,
    days_back: float,
    push: Callable[[str], None],
    emit: Callable[[dict[str, Any]], None],
) -> bool:
    dt = _format_cutoff_datetime(_cutoff_datetime_days_back(days_back))
    url = _clean_url(host, kind, dt)
    push(
        f"Removing files on {host} ({group}) until {dt} (≈ {days_back} day(s) back at 23:59)…"
    )
    emit(
        {
            "event": "clean_start",
            "cutoffDatetime": dt,
            "hosts": [host],
            "group": group,
        }
    )
    try:
        ok, status, body = _http_get(url)
        body_ok = _interpret_clean_success(body)
        success = bool(ok and body_ok)
        if not ok:
            push(f"Clean request failed for {host}: HTTP {status} — {body[:500]}")
        elif not body_ok:
            push(f"Clean response for {host} did not indicate success. Body (truncated): {body[:500]}")
        else:
            push(f"Clean completed for {host} (service reported success).")
        return success
    except requests.RequestException as e:
        push(f"Clean request error for {host}: {e}")
        return False
    except Exception as e:
        push(f"Clean request error for {host}: {e}")
        return False


def run_server_log_space_check() -> dict[str, Any]:
    activity_log: list[str] = []
    stream_events: list[dict[str, Any]] = []
    last_usage_updated: str | None = None

    def emit(ev: dict[str, Any]) -> None:
        stream_events.append(ev)

    def push(msg: str) -> None:
        activity_log.append(msg)
        emit({"event": "log", "message": msg})

    def fetch_all_usages(silent: bool) -> list[dict[str, Any]]:
        nonlocal last_usage_updated
        if not silent:
            push("Checking usage for TUX, PRAPI, and APILINK servers (parallel fetch)…")
        rows = _fetch_all_usages_parallel()
        last_usage_updated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        emit({"event": "usage_snapshot", "servers": rows, "updatedAt": last_usage_updated})
        return rows

    rows = fetch_all_usages(False)
    _, warn, critical = summarize_usages(rows)

    if warn:
        push(
            f"Warning: {len(warn)} host(s) above 60% log path usage: "
            + ", ".join(f"{w['host']} ({w['usage']}%)" for w in warn)
        )
    else:
        push("No hosts above 60% usage.")

    errors = [r for r in rows if r.get("error")]
    if errors:
        push(
            f"Note: {len(errors)} host(s) could not be read: "
            + "; ".join(f"{e['host']}: {e['error']}" for e in errors)
        )

    cleaned_hosts: list[dict[str, Any]] = []
    initial_critical_hosts = [r["host"] for r in critical]

    for host_name in initial_critical_hosts:
        host_row = next((r for r in rows if r["host"] == host_name), None)
        if not host_row or not isinstance(host_row.get("usage"), int) or host_row["usage"] <= 70:
            continue
        host = host_row["host"]
        kind = host_row["kind"]
        group = host_row["group"]
        push(f"--- {host} ({group}) is above 70%; starting automated clean cycle ---")
        days_seq = _build_days_back_sequence()
        for i, days_back in enumerate(days_seq):
            ok = run_clean_for_host(host, kind, group, days_back, push, emit)
            if not ok:
                push(f"Stopping clean attempts for {host} after failed request.")
                cleaned_hosts.append({"host": host, "failed": True})
                emit({"event": "clean_end", "host": host, "success": False})
                break
            push("Re-checking usage for all servers after clean…")
            rows = fetch_all_usages(True)
            emit({"event": "clean_end", "host": host, "success": True})
            host_row = next((r for r in rows if r["host"] == host), None)
            u = host_row["usage"] if host_row and isinstance(host_row.get("usage"), int) else None
            push(f"{host} usage is now: {'unknown' if u is None else f'{u}%'}")
            if u is not None and u < 60:
                push(f"{host} is below 60%; stopping clean cycle for this host.")
                cleaned_hosts.append({"host": host, "stoppedAtDaysBack": days_back, "finalUsage": u})
                break
            if i == len(days_seq) - 1:
                push(f"{host} still at or above 60% after maximum clean steps.")
                cleaned_hosts.append(
                    {"host": host, "stoppedAtDaysBack": days_back, "finalUsage": u, "exhausted": True}
                )

    if not initial_critical_hosts:
        push("No hosts above 70%; no clean requests were sent.")

    _, final_warn, _ = summarize_usages(rows)
    final_warning_message = None
    if final_warn:
        final_warning_message = "Still above 60% on: " + ", ".join(
            f"{w['host']} ({w['usage']}%)" for w in final_warn
        )
        push(f"Final warning: {final_warning_message}")

    return {
        "success": True,
        "type": "server-log-space",
        "header": "Servers Log Space — automated check",
        "hasUsageWarning": bool(final_warn),
        "hasCriticalOver70": bool(initial_critical_hosts),
        "lowSpaceThreshold": 60,
        "criticalThreshold": 70,
        "finalWarningMessage": final_warning_message,
        "lastUsageUpdatedAt": last_usage_updated
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "servers": rows,
        "activityLog": activity_log,
        "cleanedHosts": cleaned_hosts,
        "_stream_events": stream_events,
    }


__all__ = ["run_server_log_space_check"]
