"""VTS data API helpers for Task 1 pre-checks, validation, and column clears."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

VTS_REQUEST_TIMEOUT_S = 45

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
) -> dict[str, Any]:
    url = import_csv_url(base_url, colname)
    with file_path.open("rb") as fh:
        response = requests.post(
            url,
            files={field_name: (file_path.name, fh, "text/csv")},
            timeout=VTS_REQUEST_TIMEOUT_S * 6,
        )
    ok, message, detail = _parse_import_response(response)
    return {
        "column": colname,
        "file": file_path.name,
        "httpStatus": response.status_code,
        "success": ok,
        "message": message,
        "detail": detail,
        "strategy": f"colname={colname or 'none'}, field={field_name}, header={'yes' if 'hdr' in file_path.name else 'no'}",
    }


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
    strategies = (
        (True, "file"),
        (False, "file"),
        (True, "csv"),
        (False, "csv"),
    )
    for include_header, field_name in strategies:
        tag = "hdr" if include_header else "plain"
        path = work_dir / f"import_{colname}_{tag}_{field_name}.txt"
        _write_column_file(path, colname, values, include_header=include_header)
        result = _post_import_file(base_url, colname, path, field_name=field_name)
        check = verify_column_counts(base_url, {colname: expected_count})
        row = (check.get("rows") or [{}])[0]
        result["countCheck"] = row
        result["verified"] = bool(row.get("passed"))
        attempts.append(result)
        if result.get("verified"):
            result["success"] = True
            result["attempts"] = attempts
            return result

    return {
        "success": False,
        "column": colname,
        "message": "Import did not increase VTS column count (tried colname + header/plain variants)",
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


def import_columns(
    base_url: str,
    columns: list[str],
    source_txt: Path | str,
    *,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Import data into VTS — one column at a time via import_csv?colname=.
    Verifies countsPerColumn after import; fails if counts stay at 0.
    """
    path = Path(source_txt)
    if not path.is_file():
        raise RuntimeError(f"Import file not found: {path}")

    col_values = read_columns_from_export(path, columns)
    expected = {col: len(col_values[col]) for col in columns}
    if sum(expected.values()) == 0:
        raise RuntimeError(f"No data rows found in {path.name}")

    wd = work_dir or path.parent
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
    verify = verify_column_counts(base_url, expected)

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
                "HTTP import returned OK but VTS counts are still wrong (often need import_csv?colname=COLUMN)"
                if http_ok
                else "VTS import HTTP/request failed"
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
