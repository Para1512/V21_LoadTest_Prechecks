"""VTS data API helpers for Task 1 pre-checks, validation, and column clears."""

from __future__ import annotations

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


def import_csv_url(base_url: str) -> str:
    return f"{vts_api_root(base_url)}/data/import_csv"


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


def import_txt(base_url: str, txt_path: Path | str) -> dict[str, Any]:
    """Upload a CSV/TXT file into VTS via import_csv."""
    path = Path(txt_path)
    if not path.is_file():
        raise RuntimeError(f"Import file not found: {path}")
    url = import_csv_url(base_url)
    try:
        with path.open("rb") as fh:
            response = requests.post(
                url,
                files={"file": (path.name, fh, "text/plain")},
                timeout=VTS_REQUEST_TIMEOUT_S * 6,
            )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"VTS import failed for {path.name}: {exc}") from exc
    detail: Any = None
    try:
        detail = response.json()
    except ValueError:
        detail = (response.text or "").strip()[:500]
    return {
        "file": path.name,
        "httpStatus": response.status_code,
        "success": True,
        "detail": detail,
    }


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
