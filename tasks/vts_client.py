"""VTS data API helpers for Task 1 pre-checks, validation, and column clears."""

from __future__ import annotations

import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import requests

VTS_REQUEST_TIMEOUT_S = 45
VTS_IMPORT_TIMEOUT_S = 120
VTS_JOB_POLL_INTERVAL_S = 0.2
VTS_JOB_POLL_TIMEOUT_S = 15.0
VTS_COUNT_VERIFY_TIMEOUT_S = 20.0
VTS_COUNT_VERIFY_INTERVAL_S = 0.5

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
    timeout_s: float = VTS_COUNT_VERIFY_TIMEOUT_S,
    interval_s: float = VTS_COUNT_VERIFY_INTERVAL_S,
) -> dict[str, Any]:
    """VTS counts can lag briefly after import — short poll on get_page."""
    last = verify_column_counts(base_url, expected)
    if last.get("verified"):
        return last
    actuals = [row.get("actual") or 0 for row in last.get("rows") or []]
    if actuals and all(int(a) == 0 for a in actuals):
        return last

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(interval_s)
        last = verify_column_counts(base_url, expected)
        if last.get("verified"):
            return last
    return last


def _poll_job_status(base_url: str, job_id: int) -> dict[str, Any]:
    """VTS import is async — poll /data/job_status until the job finishes (usually <1s)."""
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
        "success": None,
        "inconclusive": True,
        "jobId": job_id,
        "polls": polls,
        "detail": last_detail,
        "message": f"job_status not conclusive after {VTS_JOB_POLL_TIMEOUT_S:.0f}s — verifying counts",
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
    if lower and re.search(r"\b(error|failed|exception)\b", lower):
        if not re.search(r"\b0\s+errors?\b", lower):
            return False, body_text[:500], detail

    return True, "HTTP OK (verify counts on VTS)", detail


def _normalize_csv_bytes(path: Path) -> bytes:
    """VTS on Windows expects CRLF line endings in uploaded CSV/TXT."""
    text = path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    if "\r\n" not in text:
        text = text.replace("\n", "\r\n")
    return text.encode("utf-8")


def _upload_attempts(
    url: str,
    content: bytes,
    filename: str,
) -> list[tuple[str, Callable[[], requests.Response]]]:
    """Build POST variants — manual VTS uses multipart file upload to /data/import_csv."""
    attempts: list[tuple[str, Callable[[], requests.Response]]] = []
    csv_name = filename if filename.lower().endswith(".csv") else f"{Path(filename).stem}.csv"

    def _add(label: str, fn: Callable[[], requests.Response]) -> None:
        attempts.append((label, fn))

    # Manual VTS UI — most common shape first.
    _add(
        "multipart field=file (browser default)",
        lambda: requests.post(
            url,
            files={"file": (filename, BytesIO(content))},
            timeout=VTS_IMPORT_TIMEOUT_S,
        ),
    )
    _add(
        f"multipart field=file name={csv_name}",
        lambda: requests.post(
            url,
            files={"file": (csv_name, BytesIO(content), "text/csv")},
            timeout=VTS_IMPORT_TIMEOUT_S,
        ),
    )

    for field in ("file", "csv", "upload"):
        for name in (filename, csv_name):
            for mime in (None, "text/csv", "application/octet-stream"):
                if field == "file" and name == filename and mime is None:
                    continue  # already added above
                label = f"multipart field={field} name={name}"
                if mime:
                    label += f" mime={mime}"

                def _post(
                    f: str = field,
                    n: str = name,
                    m: str | None = mime,
                ) -> requests.Response:
                    bio = BytesIO(content)
                    if m:
                        return requests.post(
                            url,
                            files={f: (n, bio, m)},
                            timeout=VTS_IMPORT_TIMEOUT_S,
                        )
                    return requests.post(
                        url,
                        files={f: (n, bio)},
                        timeout=VTS_IMPORT_TIMEOUT_S,
                    )

                _add(label, _post)

    for content_type in ("text/csv", "text/plain", "application/octet-stream"):
        ct = content_type

        def _raw(ct: str = content_type) -> requests.Response:
            return requests.post(
                url,
                data=content,
                headers={"Content-Type": ct},
                timeout=VTS_IMPORT_TIMEOUT_S,
            )

        _add(f"raw body Content-Type={content_type}", _raw)

    return attempts


def _run_import_upload(
    base_url: str,
    content: bytes,
    filename: str,
    columns: list[str],
    *,
    colname: str | None = None,
) -> dict[str, Any]:
    """Try every POST shape to /data/import_csv until VTS column counts match."""
    url = import_csv_url(base_url, colname)
    col_values = read_columns_from_bytes(content, columns)
    expected = {col: len(col_values[col]) for col in columns}
    attempts: list[dict[str, Any]] = []

    for label, post_fn in _upload_attempts(url, content, filename):
        try:
            response = post_fn()
        except requests.RequestException as exc:
            attempts.append(
                {
                    "strategy": label,
                    "importUrl": url,
                    "success": False,
                    "message": str(exc),
                }
            )
            continue

        ok, message, detail = _parse_import_response(response)
        entry: dict[str, Any] = {
            "strategy": label,
            "importUrl": url,
            "httpStatus": response.status_code,
            "success": ok,
            "message": message,
            "detail": detail,
            "responseBody": (response.text or "")[:500],
        }
        job_id = _extract_job_id(detail, response)
        if job_id is not None:
            entry["jobId"] = job_id
            poll = _poll_job_status(base_url, job_id)
            entry["jobPoll"] = poll
        attempts.append(entry)

        if not response.ok:
            continue

        verify = _wait_for_column_counts(base_url, expected)
        entry["verification"] = verify
        if verify.get("verified"):
            return {
                "importUrl": url,
                "strategy": label,
                "httpStatus": response.status_code,
                "success": True,
                "verified": True,
                "message": "Import verified on VTS",
                "detail": detail,
                "responseBody": entry.get("responseBody"),
                "jobId": entry.get("jobId"),
                "jobPoll": entry.get("jobPoll"),
                "verification": verify,
                "attempts": attempts,
            }

    last = attempts[-1] if attempts else {}
    verify = verify_column_counts(base_url, expected)
    return {
        "importUrl": url,
        "success": False,
        "verified": False,
        "message": last.get("message") or "All import upload attempts failed",
        "detail": last.get("detail"),
        "responseBody": last.get("responseBody"),
        "verification": verify,
        "attempts": attempts,
    }


def import_csv_bytes(
    base_url: str,
    content: bytes,
    filename: str,
    columns: list[str],
    *,
    colname: str | None = None,
) -> dict[str, Any]:
    """
    Import a single CSV/TXT file into VTS (P_BAN,P_SUBSCRIBER header + rows).
    POST /data/import_csv then verify counts on get_page.
    """
    col_values = read_columns_from_bytes(content, columns)
    expected = {col: len(col_values[col]) for col in columns}
    if max(expected.values(), default=0) == 0:
        raise RuntimeError(f"No data rows found in upload ({filename})")

    result = _run_import_upload(
        base_url, content, filename, columns, colname=colname
    )
    if not result.get("verified") and colname is None and len(columns) == 1:
        result = _run_import_upload(
            base_url, content, filename, columns, colname=columns[0]
        )

    verified = bool(result.get("verified"))
    verify = result.get("verification") or verify_column_counts(base_url, expected)
    return {
        "success": verified,
        "verified": verified,
        "httpOk": any(a.get("httpStatus") == 200 for a in result.get("attempts") or []),
        "columns": columns,
        "expectedCounts": expected,
        "mode": "single-file",
        "upload": result,
        "verification": verify,
        "file": filename,
        "message": (
            "Import verified on VTS (single file → /data/import_csv)"
            if verified
            else (
                f"VTS import failed — {result.get('message')}"
                if not result.get("attempts")
                else "VTS accepted upload but column counts did not update"
            )
        ),
    }


def import_csv_file(
    base_url: str,
    file_path: Path | str,
    columns: list[str],
) -> dict[str, Any]:
    """Import one export file from disk (Oracle remediation TXT)."""
    path = Path(file_path)
    if not path.is_file():
        raise RuntimeError(f"Import file not found: {path}")
    content = _normalize_csv_bytes(path)
    return import_csv_bytes(base_url, content, path.name, columns, colname=None)


def read_columns_from_bytes(content: bytes, columns: list[str]) -> dict[str, list[str]]:
    text = content.decode("utf-8")
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").splitlines() if ln.strip()]
    return _parse_column_lines(lines, columns)


def _parse_column_lines(lines: list[str], columns: list[str]) -> dict[str, list[str]]:
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

    for line in lines:
        parts = [p.strip() for p in line.split(",", len(columns) - 1)]
        if len(parts) < len(columns):
            continue
        for col, val in zip(columns, parts):
            if val:
                out[col].append(val)
    return out


def read_columns_from_export(path: Path, columns: list[str]) -> dict[str, list[str]]:
    """Parse combined CSV (header + rows) or single-column file into per-column value lists."""
    return read_columns_from_bytes(path.read_bytes(), columns)


def import_columns(
    base_url: str,
    columns: list[str],
    source_txt: Path | str,
    *,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    """Import one file into VTS via POST /data/import_csv (single-file, all columns)."""
    _ = work_dir  # kept for API compatibility
    return import_csv_file(base_url, source_txt, columns)


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
    return import_csv_file(base_url, path, columns)


def build_csv_bytes(headers: list[str], rows: list[tuple[str, ...]]) -> bytes:
    """Build CRLF CSV bytes for VTS single-file import."""
    lines = [",".join(headers)]
    for row in rows:
        lines.append(",".join(str(c or "").strip() for c in row))
    text = "\r\n".join(lines) + "\r\n"
    return text.encode("utf-8")


def import_oracle_rows(
    base_url: str,
    headers: list[str],
    rows: list[tuple[Any, ...]],
    *,
    filename: str = "import.csv",
) -> dict[str, Any]:
    """Import Oracle query rows directly into VTS without writing a persistent file."""
    str_rows = [tuple(str(c or "").strip() for c in row) for row in rows]
    content = build_csv_bytes(headers, str_rows)
    return import_csv_bytes(base_url, content, filename, headers, colname=None)


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
