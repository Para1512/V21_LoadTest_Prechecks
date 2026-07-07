"""VTS data API helpers for Task 1 pre-checks, validation, and column clears."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

VTS_REQUEST_TIMEOUT_S = 45
VTS_JOB_POLL_INTERVAL_S = 2.0
VTS_JOB_POLL_TIMEOUT_S = 300.0

VTS1_MIN_COUNTS: dict[str, int] = {
    "P_BAN": 8000,
    "P_SIM": 8000,
    "P_IMEI_IP14128PUR": 8000,
}

VTS2_MIN_COUNTS: dict[str, int] = {
    "P_BAN_C": 1000,
    "P_BAN_H": 1000,
}

VTS1_ALIGN_COLUMNS = ("P_BAN", "P_SUBSCRIBER")

VTS2_ALIGN_GROUP_C = (
    "P_BAN_C",
    "P_NPA_NXX_C",
    "P_SUBSCRIBER_C",
    "P_IMEI_C",
    "P_SIM_C",
    "P_ESN_0",
    "P_ESN_1",
    "P_IMSI_C",
)

VTS2_ALIGN_GROUP_H = (
    "P_BAN_H",
    "P_SUBSCRIBER_H",
    "P_OLD_IMEI",
    "P_OLD_SIM",
    "P_ESN_SEQ_NO",
    "P_ESN_SEQ_NO1",
    "P_IMSI",
)

VTS1_CLEAR_COLUMNS = [
    "NAC_BAN",
    "PPC_BAN",
    "TOR_BAN",
    "AdSc_BAN",
    "Cancel_BAN",
    "Cancel_SUB_BAN",
    "SmSw_BAN",
    "BnAt_BAN",
    "BYOD_BAN",
    "AAL_BAN",
    "VSY_BAN",
]

VTS2_CLEAR_COLUMNS = ["HUP_BAN", "Resume_BAN"]

VTS1_P_BAN_RELOAD_COLUMNS = ["P_BAN", "P_SUBSCRIBER"]


def vts_api_root(base_url: str) -> str:
    return (base_url or "").strip().rstrip("/")


def get_page_url(base_url: str) -> str:
    return f"{vts_api_root(base_url)}/data/get_page?rows=0&page=1"


def clear_column_url(base_url: str, colname: str) -> str:
    return f"{vts_api_root(base_url)}/data/clear_column?colname={quote(colname, safe='')}"


def job_status_url(base_url: str, job_id: int) -> str:
    return f"{vts_api_root(base_url)}/data/job_status?jobid={job_id}"


def _extract_job_id(detail: Any, response: requests.Response) -> int | None:
    if isinstance(detail, dict):
        for key in ("jobid", "jobId", "job_id", "id"):
            val = detail.get(key)
            if val is not None:
                try:
                    return int(val)
                except (TypeError, ValueError):
                    pass
        for nest in ("job", "data", "result"):
            nested = detail.get(nest)
            if isinstance(nested, dict):
                found = _extract_job_id(nested, response)
                if found is not None:
                    return found
    if isinstance(detail, (int, float)) and not isinstance(detail, bool):
        try:
            return int(detail)
        except (TypeError, ValueError):
            pass
    if isinstance(detail, str):
        stripped = detail.strip()
        if stripped.isdigit():
            return int(stripped)
    text = response.text or ""
    for pattern in (
        r'"jobid"\s*:\s*(\d+)',
        r'"jobId"\s*:\s*(\d+)',
        r'"job_id"\s*:\s*(\d+)',
        r"jobid[=:](\d+)",
        r"jobId[=:](\d+)",
        r"\bjob\s*#?\s*(\d+)\b",
    ):
        m = re.search(pattern, text, re.I)
        if m:
            return int(m.group(1))
    location = response.headers.get("Location") or response.headers.get("location") or ""
    m = re.search(r"jobid=(\d+)", location, re.I)
    if m:
        return int(m.group(1))
    return None


def _job_is_terminal(detail: Any) -> bool:
    if isinstance(detail, dict):
        status = str(detail.get("status") or detail.get("state") or "").lower()
        if status in (
            "complete",
            "completed",
            "done",
            "success",
            "successful",
            "finished",
            "failed",
            "error",
            "cancelled",
            "canceled",
        ):
            return True
        if detail.get("complete") is True or detail.get("finished") is True:
            return True
        if detail.get("done") is True:
            return True
        pct = detail.get("percent") or detail.get("progress") or detail.get("percentage")
        if pct is not None:
            try:
                if float(pct) >= 100:
                    return True
            except (TypeError, ValueError):
                pass
    if isinstance(detail, str):
        lower = detail.lower().strip()
        if lower.isdigit():
            return int(lower) >= 100
        if any(w in lower for w in ("complete", "finished", "failed", "error", "100%")):
            return True
    return False


def _job_is_success(detail: Any) -> bool:
    if isinstance(detail, dict):
        status = str(detail.get("status") or detail.get("state") or "").lower()
        if status in ("failed", "error", "cancelled", "canceled"):
            return False
        if detail.get("success") is False:
            return False
        if detail.get("error"):
            return False
    if isinstance(detail, str) and re.search(r"\b(fail|error)\b", detail, re.I):
        return False
    return True


def _wait_for_column_counts(
    base_url: str,
    expected: dict[str, int],
    *,
    timeout_s: float = 45.0,
    interval_s: float = 2.0,
) -> dict[str, Any]:
    """VTS counts can lag after job_status completes — poll get_page until they match."""
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {"verified": False, "rows": []}
    while time.monotonic() < deadline:
        last = verify_column_counts(base_url, expected)
        if last.get("verified"):
            return last
        time.sleep(interval_s)
    return last


def _poll_job_status(base_url: str, job_id: int) -> dict[str, Any]:
    """VTS import is async — poll /data/job_status until the job finishes."""
    deadline = time.monotonic() + VTS_JOB_POLL_TIMEOUT_S
    last_detail: Any = None
    polls = 0
    while time.monotonic() < deadline:
        polls += 1
        url = job_status_url(base_url, job_id)
        try:
            response = requests.get(url, timeout=VTS_REQUEST_TIMEOUT_S)
            last_detail = response.text
            try:
                last_detail = response.json()
            except ValueError:
                pass
            if not response.ok:
                return {
                    "completed": False,
                    "success": False,
                    "jobId": job_id,
                    "polls": polls,
                    "detail": last_detail,
                    "message": f"job_status HTTP {response.status_code}",
                }
            if _job_is_terminal(last_detail):
                return {
                    "completed": True,
                    "success": _job_is_success(last_detail),
                    "jobId": job_id,
                    "polls": polls,
                    "detail": last_detail,
                    "message": "job completed" if _job_is_success(last_detail) else "job failed",
                }
        except requests.RequestException as exc:
            return {
                "completed": False,
                "success": False,
                "jobId": job_id,
                "polls": polls,
                "detail": str(exc),
                "message": f"job_status request failed: {exc}",
            }
        time.sleep(VTS_JOB_POLL_INTERVAL_S)
    return {
        "completed": False,
        "success": False,
        "jobId": job_id,
        "polls": polls,
        "detail": last_detail,
        "message": f"job_status poll timeout after {VTS_JOB_POLL_TIMEOUT_S:.0f}s",
    }


def import_csv_url(base_url: str, colname: str | None = None) -> str:
    root = f"{vts_api_root(base_url)}/data/import_csv"
    if colname:
        return f"{root}?colname={quote(colname, safe='')}"
    return root


def _parse_import_response(response: requests.Response) -> tuple[bool, str, Any]:
    """Best-effort parse of VTS import response — HTTP 200 alone is not success."""
    body_text = (response.text or "").strip()
    detail: Any = body_text[:800] if body_text else None
    try:
        detail = response.json()
    except ValueError:
        pass

    if not response.ok:
        return False, f"HTTP {response.status_code}", detail

    if isinstance(detail, dict):
        if detail.get("success") is False:
            return False, str(detail.get("error") or detail.get("message") or detail), detail
        if detail.get("error"):
            return False, str(detail["error"]), detail
        if detail.get("success") is True:
            return True, "response success=true", detail

    lower = body_text.lower()
    if lower and re.search(r"\b(error|failed|exception|invalid)\b", lower):
        if not re.search(r"\b0\s+error", lower):
            return False, body_text[:500], detail

    # Ambiguous HTTP 200 — caller must verify counts on VTS.
    return True, "HTTP OK (verify counts on VTS)", detail


def _write_column_file(path: Path, colname: str, values: list[str], *, include_header: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if include_header:
        lines.append(colname)
    lines.extend(v for v in values if v != "")
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n")


def _post_import_file(
    base_url: str,
    colname: str | None,
    file_path: Path,
    *,
    field_name: str,
    upload_name: str | None = None,
) -> dict[str, Any]:
    url = import_csv_url(base_url, colname)
    send_name = upload_name or file_path.name
    mime = "text/csv" if send_name.lower().endswith(".csv") else "text/plain"
    with file_path.open("rb") as fh:
        response = requests.post(
            url,
            files={field_name: (send_name, fh, mime)},
            timeout=VTS_REQUEST_TIMEOUT_S * 6,
        )
    ok, message, detail = _parse_import_response(response)
    result: dict[str, Any] = {
        "importUrl": url,
        "column": colname,
        "file": send_name,
        "httpStatus": response.status_code,
        "success": ok,
        "message": message,
        "detail": detail,
        "strategy": f"POST {url} field={field_name} upload={send_name}",
    }
    job_id = _extract_job_id(detail, response)
    if job_id is not None:
        result["jobId"] = job_id
        poll = _poll_job_status(base_url, job_id)
        result["jobPoll"] = poll
        result["success"] = bool(poll.get("success"))
        result["message"] = poll.get("message") or message
        if not poll.get("success"):
            result["detail"] = poll.get("detail") or detail
    return result


def _try_import_column(
    base_url: str,
    colname: str,
    values: list[str],
    work_dir: Path,
    *,
    expected_count: int,
) -> dict[str, Any]:
    """Try several import_csv shapes; VTS usually needs ?colname= and one value per line."""
    if not values:
        return {"success": False, "message": "No values to import", "column": colname}

    attempts: list[dict[str, Any]] = []
    # Manual VTS: POST /data/import_csv with single-column file (header = colname).
    strategies: list[tuple[bool, str | None, str | None]] = [
        (True, None, None),
        (False, None, None),
        (True, colname, None),
        (False, colname, None),
        (True, colname, ".csv"),
        (False, colname, ".csv"),
    ]
    for include_header, col_param, ext_override in strategies:
        tag = "hdr" if include_header else "plain"
        suffix = ext_override or ".txt"
        path = work_dir / f"import_{colname}_{tag}{suffix}"
        _write_column_file(path, colname, values, include_header=include_header)
        upload_name = f"{colname}{suffix}" if ext_override else path.name
        for field_name in ("file", "csv"):
            result = _post_import_file(
                base_url,
                col_param,
                path,
                field_name=field_name,
                upload_name=upload_name,
            )
            time.sleep(1.0)
            check = _wait_for_column_counts(base_url, {colname: expected_count})
            row = (check.get("rows") or [{}])[0]
            result["countCheck"] = row
            result["verified"] = bool(row.get("passed"))
            result["countWait"] = check
            attempts.append(result)
            if result.get("verified"):
                result["success"] = True
                result["attempts"] = attempts
                return result

    return {
        "success": False,
        "column": colname,
        "message": (
            "Import did not increase VTS column count "
            f"(tried POST {import_csv_url(base_url, None)} and ?colname={colname})"
        ),
        "attempts": attempts,
    }


def read_columns_from_export(path: Path, columns: list[str]) -> dict[str, list[str]]:
    """Parse combined CSV (header + rows) or single-column file into per-column value lists."""
    text = path.read_text(encoding="utf-8")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {col: [] for col in columns}

    out: dict[str, list[str]] = {col: [] for col in columns}
    start = 0
    header_parts = [p.strip().upper() for p in lines[0].split(",")]
    expected = [c.upper() for c in columns]

    if len(columns) > 1 and header_parts[: len(columns)] == expected:
        start = 1
        for line in lines[start:]:
            parts = [p.strip() for p in line.split(",", len(columns) - 1)]
            if len(parts) < len(columns):
                continue
            for col, val in zip(columns, parts):
                if val:
                    out[col].append(val)
        return out

    if len(columns) == 1:
        col = columns[0]
        if lines[0].upper() == col.upper():
            start = 1
        out[col] = [ln.strip() for ln in lines[start:] if ln.strip()]
        return out

    # Combined file without matching header — split by comma.
    for line in lines:
        parts = [p.strip() for p in line.split(",", len(columns) - 1)]
        if len(parts) < len(columns):
            continue
        for col, val in zip(columns, parts):
            if val:
                out[col].append(val)
    return out


def verify_column_counts(
    base_url: str,
    expected: dict[str, int],
    *,
    tolerance: int = 0,
) -> dict[str, Any]:
    """Re-fetch get_page and confirm column counts match expected row counts."""
    payload = fetch_get_page(base_url)
    counts = payload.get("countsPerColumn") or {}
    rows: list[dict[str, Any]] = []
    all_ok = True
    for col, need in expected.items():
        actual = column_count(counts, col)
        ok = actual is not None and need > 0 and actual == need
        if not ok:
            all_ok = False
        rows.append({"column": col, "expected": need, "actual": actual, "passed": ok})
    return {"verified": all_ok, "rows": rows, "countsPerColumn": counts}


def _try_import_combined_file(
    base_url: str,
    source_path: Path,
    columns: list[str],
    expected: dict[str, int],
) -> dict[str, Any] | None:
    """
    Manual VTS UI: POST {VTS1}/data/import_csv (no colname) with combined CSV file,
    then poll /data/job_status?jobid=N.
    """
    attempts: list[dict[str, Any]] = []
    import_url = import_csv_url(base_url, None)

    for field_name in ("file", "csv", "upload"):
        for upload_name in (source_path.name, source_path.with_suffix(".csv").name):
            result = _post_import_file(
                base_url,
                None,
                source_path,
                field_name=field_name,
                upload_name=upload_name,
            )
            attempts.append(result)
            time.sleep(1.0)
            verify = _wait_for_column_counts(base_url, expected)
            if verify.get("verified"):
                return {
                    "mode": "combined",
                    "success": True,
                    "verified": True,
                    "importUrl": import_url,
                    "field": field_name,
                    "upload": result,
                    "verification": verify,
                    "attempts": attempts,
                }
            if result.get("jobId") and result.get("success"):
                verify = _wait_for_column_counts(base_url, expected, timeout_s=90.0)
                if verify.get("verified"):
                    return {
                        "mode": "combined",
                        "success": True,
                        "verified": True,
                        "importUrl": import_url,
                        "field": field_name,
                        "upload": result,
                        "verification": verify,
                        "attempts": attempts,
                    }
    return None


def import_columns(
    base_url: str,
    columns: list[str],
    source_txt: Path | str,
    *,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Import into VTS via POST /data/import_csv (same as manual VTS UI), then job_status poll.
    Multi-column files use plain /data/import_csv; single-column falls back to ?colname=.
    """
    path = Path(source_txt)
    if not path.is_file():
        raise RuntimeError(f"Import file not found: {path}")

    col_values = read_columns_from_export(path, columns)
    expected = {col: len(col_values[col]) for col in columns}
    if sum(expected.values()) == 0:
        raise RuntimeError(f"No data rows found in {path.name}")

    wd = work_dir or path.parent
    combined = None
    if len(columns) > 1:
        combined = _try_import_combined_file(base_url, path, columns, expected)

    if combined:
        return {
            "success": True,
            "verified": True,
            "httpOk": True,
            "columns": columns,
            "expectedCounts": expected,
            "mode": "combined",
            "combinedResult": combined,
            "verification": combined.get("verification"),
            "file": path.name,
            "message": "Import verified on VTS (POST /data/import_csv + job_status)",
        }

    column_results: list[dict[str, Any]] = []
    for col in columns:
        values = col_values[col]
        if not values:
            column_results.append(
                {"success": False, "column": col, "message": "No values for column in export file"}
            )
            continue
        column_results.append(
            _try_import_column(base_url, col, values, wd, expected_count=len(values))
        )

    verified = all(r.get("verified") for r in column_results if r.get("column"))
    http_ok = all(r.get("success") for r in column_results)
    verify = _wait_for_column_counts(base_url, expected, timeout_s=30.0 if verified else 10.0)
    verified = bool(verify.get("verified"))

    return {
        "success": http_ok and verified,
        "verified": verified,
        "httpOk": http_ok,
        "columns": columns,
        "expectedCounts": expected,
        "columnResults": column_results,
        "verification": verify,
        "file": path.name,
        "message": (
            "Import verified on VTS"
            if verified
            else (
                "VTS import job finished but column counts are still wrong — check job_status / file format"
                if http_ok
                else "VTS import HTTP/request or job_status failed"
            )
        ),
    }


def import_txt(base_url: str, txt_path: Path | str, columns: list[str] | None = None) -> dict[str, Any]:
    """Import export file; columns inferred from filename or passed explicitly."""
    path = Path(txt_path)
    if columns is None:
        name = path.name.upper()
        if "P_BAN" in name and "SUBSCRIBER" in name:
            columns = ["P_BAN", "P_SUBSCRIBER"]
        elif "P_SIM" in name:
            columns = ["P_SIM"]
        elif "IMEI" in name:
            columns = ["P_IMEI_IP14128PUR"]
        else:
            columns = []
    if not columns:
        raise RuntimeError(f"Cannot infer VTS columns for import file: {path.name}")
    return import_columns(base_url, columns, path, work_dir=path.parent)


def column_count(counts_per_column: Any, column: str) -> int | None:
    if not isinstance(counts_per_column, dict):
        return None
    if column in counts_per_column:
        try:
            return int(counts_per_column[column])
        except (TypeError, ValueError):
            return None
    target = column.upper()
    for key, value in counts_per_column.items():
        if str(key).upper() == target:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def fetch_get_page(base_url: str) -> dict[str, Any]:
    url = get_page_url(base_url)
    try:
        response = requests.get(url, timeout=VTS_REQUEST_TIMEOUT_S)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"VTS request failed ({url}): {exc}") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"VTS response is not JSON ({url})") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"VTS response shape unexpected ({url})")
    counts = payload.get("countsPerColumn")
    if not isinstance(counts, dict):
        raise RuntimeError(f"VTS response missing countsPerColumn ({url})")
    return payload


def clear_column(base_url: str, colname: str) -> dict[str, Any]:
    url = clear_column_url(base_url, colname)
    try:
        response = requests.get(url, timeout=VTS_REQUEST_TIMEOUT_S)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"clear_column failed for {colname} ({url}): {exc}") from exc
    return {
        "column": colname,
        "url": url,
        "httpStatus": response.status_code,
        "success": True,
    }


def counts_snapshot(
    counts_per_column: dict[str, Any], columns: tuple[str, ...] | list[str]
) -> dict[str, int | None]:
    return {col: column_count(counts_per_column, col) for col in columns}


def check_p_new_imei_gte_ban_h(
    counts_per_column: dict[str, Any],
    *,
    label: str = "VTS 2 (P_NEW_IMEI vs P_BAN_H)",
) -> dict[str, Any]:
    p_ban_h = column_count(counts_per_column, "P_BAN_H")
    p_new_imei = column_count(counts_per_column, "P_NEW_IMEI")
    if p_ban_h is None:
        return {
            "passed": False,
            "label": label,
            "pBanH": p_ban_h,
            "pNewImei": p_new_imei,
            "message": f"{label}: P_BAN_H count is missing from countsPerColumn.",
        }
    if p_new_imei is None:
        return {
            "passed": False,
            "label": label,
            "pBanH": p_ban_h,
            "pNewImei": p_new_imei,
            "message": f"{label}: P_NEW_IMEI count is missing from countsPerColumn.",
        }
    passed = p_new_imei >= p_ban_h
    if passed:
        message = f"{label}: P_NEW_IMEI ({p_new_imei}) >= P_BAN_H ({p_ban_h})."
    else:
        message = (
            f"{label}: P_NEW_IMEI ({p_new_imei}) is less than P_BAN_H ({p_ban_h}); "
            "expected P_NEW_IMEI >= P_BAN_H."
        )
    return {
        "passed": passed,
        "label": label,
        "pBanH": p_ban_h,
        "pNewImei": p_new_imei,
        "required": f"P_NEW_IMEI >= P_BAN_H ({p_ban_h})",
        "message": message,
    }


def check_min_counts(
    counts_per_column: dict[str, Any],
    requirements: dict[str, int],
    *,
    label: str,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    insufficient: list[str] = []
    for column, required in requirements.items():
        count = column_count(counts_per_column, column)
        ok = count is not None and count >= required
        checks.append(
            {
                "column": column,
                "count": count,
                "required": required,
                "ok": ok,
            }
        )
        if not ok:
            insufficient.append(column)
    passed_columns: list[str] = []
    failed_columns: list[str] = []
    for check in checks:
        if check["ok"]:
            passed_columns.append(check["column"])
        else:
            failed_columns.append(check["column"])

    safe = len(failed_columns) == 0
    messages: list[str] = []
    messages.append(f"{label} pre-check — {len(checks)} column(s) checked:")
    for check in checks:
        column = check["column"]
        count = check["count"]
        required = check["required"]
        if check["ok"]:
            messages.append(
                f"  PASSED — {column}: count {count} (required >= {required})"
            )
        elif count is None:
            messages.append(
                f"  FAILED — {column}: missing from countsPerColumn (required >= {required})"
            )
        else:
            messages.append(
                f"  FAILED — {column}: count {count} (required >= {required})"
            )
    if safe:
        messages.append(
            f"{label}: all columns passed — safe for VTS "
            f"({', '.join(passed_columns)})."
        )
    else:
        messages.append(
            f"{label}: passed ({', '.join(passed_columns) or 'none'}); "
            f"failed ({', '.join(failed_columns)})."
        )
    return {
        "safe": safe,
        "checks": checks,
        "passedColumns": passed_columns,
        "failedColumns": failed_columns,
        "insufficientColumns": failed_columns,
        "messages": messages,
    }


def check_columns_equal(
    counts_per_column: dict[str, Any],
    columns: tuple[str, ...],
    *,
    label: str,
) -> dict[str, Any]:
    values: dict[str, int | None] = {col: column_count(counts_per_column, col) for col in columns}
    present = [col for col, val in values.items() if val is not None]
    if len(present) < len(columns):
        missing = [col for col in columns if values[col] is None]
        return {
            "aligned": False,
            "columns": list(columns),
            "counts": values,
            "message": f"{label}: missing counts for {', '.join(missing)}.",
        }
    unique = {values[col] for col in columns}
    aligned = len(unique) == 1
    ref = values[columns[0]]
    if aligned:
        message = f"{label}: all counts equal ({ref})."
    else:
        parts = [f"{col}={values[col]}" for col in columns]
        message = f"{label}: counts are not aligned ({', '.join(parts)})."
    return {
        "aligned": aligned,
        "columns": list(columns),
        "counts": values,
        "referenceCount": ref,
        "message": message,
    }
