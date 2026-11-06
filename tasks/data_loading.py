"""
Task 1 — Data loading: VTS pre-checks, validation, clear, and Oracle P_BAN reload.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tasks import oracle_extract as ora
from tasks import oracle_queries as oq
from tasks import vts_client as vts

VTS1_URL = os.environ.get("VTS1_URL", "http://vwrenbptlrgap06:2244/").strip()
VTS2_URL = os.environ.get("VTS2_URL", "http://vwrenbptlrgap06:2245/").strip()

VTS1_CLEAR_COLUMNS_STEP1 = list(vts.VTS1_CLEAR_COLUMNS)
VTS2_CLEAR_COLUMNS_STEP1 = list(vts.VTS2_CLEAR_COLUMNS)
VTS1_CHECK_COLUMNS = vts.VTS1_ALIGN_COLUMNS
VTS2_MIN_COUNT_COLUMNS = vts.VTS2_MIN_COUNTS


def query_defaults() -> dict[str, Any]:
    p = dict(ora.DEFAULT_QUERY_PARAMS)
    return {
        "defaults": p,
        "displaySql": ora.build_main_query_sql(p),
        "oracleQueries": oq.queries_for_api(),
        "vtsLimits": vts.min_counts_for_api(),
    }


def get_vts_limits() -> dict[str, Any]:
    return vts.min_counts_for_api()


def save_vts_limits(body: dict[str, Any]) -> dict[str, Any]:
    vts1 = body.get("vts1") if isinstance(body.get("vts1"), dict) else None
    vts2 = body.get("vts2") if isinstance(body.get("vts2"), dict) else None
    if vts1 is None and vts2 is None:
        # Allow flat payload: { "vts1": {...} } already handled; also accept nested under limits
        limits = body.get("limits") if isinstance(body.get("limits"), dict) else None
        if limits:
            vts1 = limits.get("vts1") if isinstance(limits.get("vts1"), dict) else None
            vts2 = limits.get("vts2") if isinstance(limits.get("vts2"), dict) else None
    return vts.save_min_counts(vts1=vts1, vts2=vts2)


def reset_vts_limits() -> dict[str, Any]:
    return vts.reset_min_counts()


def get_oracle_queries() -> dict[str, Any]:
    return oq.queries_for_api()


def save_oracle_queries(updates: dict[str, str]) -> dict[str, Any]:
    merged = oq.save_queries(updates)
    return {"success": True, "queries": merged, "meta": oq.queries_for_api()["meta"]}


def remediation_issues(
    precheck: dict[str, Any] | None = None,
    prep: dict[str, Any] | None = None,
) -> list[str]:
    """
    Detect remediation keys. Trigger 1 (P_BAN/P_SUBSCRIBER) and Trigger 6
    (P_BAN_H / history) share one combined remediation: p_ban_h_vts2.
    """
    issues: list[str] = []
    ban_or_history = False
    if precheck:
        vts1_failed = precheck.get("vts1", {}).get("failedColumns") or []
        vts2_failed = precheck.get("vts2", {}).get("failedColumns") or []
        if "P_BAN" in vts1_failed or "P_SUBSCRIBER" in vts1_failed:
            ban_or_history = True
        if "P_BAN_H" in vts2_failed:
            ban_or_history = True
        if "P_SIM" in vts1_failed or "P_SIM" in vts2_failed:
            issues.append("p_sim")
        if "P_IMEI_IP14128PUR" in vts1_failed:
            issues.append("p_imei_ip14128pur")
        if "P_BAN_C" in vts2_failed:
            issues.append("p_ban_c_vts2")
        if "P_NEW_IMEI" in vts2_failed:
            issues.append("p_new_imei_vts2")
    if prep:
        for v in prep.get("validationDetails") or []:
            if not v.get("passed"):
                if v.get("id") in (
                    "vts1-ban-subscriber",
                    "vts2-history",
                    "vts2-p_ban_h",
                ):
                    ban_or_history = True
                elif v.get("id") in ("vts1-p_sim", "vts2-p_sim") and "p_sim" not in issues:
                    issues.append("p_sim")
                elif v.get("id") == "vts1-p_imei_ip14128pur" and "p_imei_ip14128pur" not in issues:
                    issues.append("p_imei_ip14128pur")
                elif v.get("id") == "vts2-current" and "p_ban_c_vts2" not in issues:
                    issues.append("p_ban_c_vts2")
                elif v.get("id") == "vts2-p_new_imei" and "p_new_imei_vts2" not in issues:
                    issues.append("p_new_imei_vts2")
    if ban_or_history:
        issues.append("p_ban_h_vts2")
    return issues


vts1_remediation_issues = remediation_issues


# User-facing guidance when a trigger still fails after Oracle re-run / prep.
TRIGGER_GUIDANCE: list[dict[str, Any]] = [
    {
        "id": "trigger_1_6",
        "label": "Trigger 1 / 6 - P_BAN, P_SUBSCRIBER & P_BAN_H",
        "message": (
            "Change SKU in the query as current SKU & PP combination has not enough data "
            "or start data PREP."
        ),
        "issue_keys": ("p_ban_h_vts2", "ban_subscriber"),
        "validation_ids": ("vts1-ban-subscriber", "vts2-history", "vts2-p_ban_h"),
        "columns": ("P_BAN", "P_SUBSCRIBER", "P_BAN_H"),
    },
    {
        "id": "trigger_2",
        "label": "Trigger 2 - P_SIM",
        "message": "Not enough SIM data is available in DB or change item_ID.",
        "issue_keys": ("p_sim",),
        "validation_ids": ("vts1-p_sim", "vts2-p_sim"),
        "columns": ("P_SIM",),
    },
    {
        "id": "trigger_3",
        "label": "Trigger 3 - P_IMEI_IP14128PUR",
        "message": "Not enough data for current model, pls change SKU.",
        "issue_keys": ("p_imei_ip14128pur",),
        "validation_ids": ("vts1-p_imei_ip14128pur",),
        "columns": ("P_IMEI_IP14128PUR",),
    },
    {
        "id": "trigger_4",
        "label": "Trigger 4 - P_BAN_C (cancelled BANs)",
        "message": "Not enough data for cancelled Ban's, pls create data.",
        "issue_keys": ("p_ban_c_vts2",),
        "validation_ids": ("vts2-current",),
        "columns": ("P_BAN_C",),
    },
    {
        "id": "trigger_5",
        "label": "Trigger 5 - P_NEW_IMEI",
        "message": "Not enough data for current model, pls change SKU.",
        "issue_keys": ("p_new_imei_vts2",),
        "validation_ids": ("vts2-p_new_imei",),
        "columns": ("P_NEW_IMEI",),
    },
]


def build_user_guidance(
    precheck: dict[str, Any] | None = None,
    prep: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Map still-failing columns/validations to short operator guidance messages."""
    failed_columns: set[str] = set()
    failed_validation_ids: set[str] = set()
    if precheck:
        failed_columns.update(precheck.get("vts1", {}).get("failedColumns") or [])
        failed_columns.update(precheck.get("vts2", {}).get("failedColumns") or [])
    if prep:
        for v in prep.get("validationDetails") or []:
            if not v.get("passed"):
                failed_validation_ids.add(str(v.get("id") or ""))
                for row in v.get("rows") or []:
                    col = row.get("column")
                    if col and row.get("passed") is False:
                        failed_columns.add(str(col))

    issue_keys = set(remediation_issues(precheck=precheck, prep=prep))
    guidance: list[dict[str, Any]] = []
    for item in TRIGGER_GUIDANCE:
        hit = bool(issue_keys.intersection(item["issue_keys"]))
        hit = hit or bool(failed_validation_ids.intersection(item["validation_ids"]))
        hit = hit or bool(failed_columns.intersection(item["columns"]))
        if hit:
            guidance.append(
                {
                    "id": item["id"],
                    "label": item["label"],
                    "message": item["message"],
                }
            )
    return guidance


def build_validation_summary(
    precheck: dict[str, Any] | None = None,
    prep: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Clean validation rows for UI (no technical log noise)."""
    rows: list[dict[str, Any]] = []
    if precheck:
        for side, label in (("vts1", "VTS 1 pre-check"), ("vts2", "VTS 2 pre-check")):
            block = precheck.get(side) or {}
            for check in block.get("checks") or []:
                rows.append(
                    {
                        "group": label,
                        "name": check.get("column"),
                        "count": check.get("count"),
                        "required": check.get("required"),
                        "passed": bool(check.get("ok")),
                    }
                )
    if prep:
        for v in prep.get("validationDetails") or []:
            rows.append(
                {
                    "group": "Validation",
                    "name": v.get("name") or v.get("id"),
                    "count": v.get("referenceCount"),
                    "required": v.get("rule"),
                    "passed": bool(v.get("passed")),
                    "message": v.get("message") or "",
                }
            )
    return rows


def _import_vts_data(
    base_url: str,
    vts_label: str,
    headers: list[str],
    vts_rows: list[tuple[str, ...]],
    txt_path: Path,
    filename: str,
    messages: list[str],
    *,
    quick: bool = False,
) -> dict[str, Any]:
    """POST /data/import_csv (fileToUpload) with Oracle rows; fall back to saved TXT."""
    messages.append(f"  Importing into {vts_label} (POST /data/import_csv) …")
    imp = vts.import_oracle_rows(
        base_url, headers, vts_rows, filename=filename, quick=quick
    )
    if not imp.get("verified"):
        messages.append(f"  Direct import failed on {vts_label} — retrying from TXT …")
        imp = vts.import_csv_file(base_url, txt_path, headers, quick=quick)
    return imp


def _run_history_batch(
    history_sql_template: str,
    batch_no: int,
    batch_bans: list[str],
) -> dict[str, Any]:
    """Run one P_BAN_H Oracle batch (1000 BANs) on its own connection."""
    batch_sql = oq.build_p_ban_h_sql(history_sql_template, batch_bans)
    t0 = time.monotonic()
    rows, _cols = oq.run_sql(batch_sql)
    return {
        "batch": batch_no,
        "banCount": len(batch_bans),
        "rowCount": len(rows),
        "elapsedSec": round(time.monotonic() - t0, 1),
        "rows": rows,
    }


def _remediate_p_sim_dual(
    sql: str,
    out_dir: Path,
    proj: Path,
    messages: list[str],
) -> dict[str, Any]:
    """
    Validation 2 (VTS 1 & VTS 2 — P_SIM):
    Trigger: VTS1 P_SIM < 8000 OR VTS2 P_SIM < 5000.
    Clear P_SIM on both → Oracle (50k rows) → first 10k to VTS2, next 40k to VTS1.
    Import uses same fileToUpload POST /data/import_csv as Validation 1.
    """
    entry: dict[str, Any] = {
        "issue": "p_sim",
        "label": oq.QUERY_META["p_sim"]["label"],
        "success": False,
        "clearDetails": [],
        "txtFile": None,
        "rowCount": 0,
        "import": None,
        "imports": [],
    }
    headers = ["P_SIM"]
    need_total = oq.P_SIM_QUERY_ROW_LIMIT
    need_vts2 = oq.P_SIM_VTS2_ROW_LIMIT
    need_vts1 = oq.P_SIM_VTS1_ROW_LIMIT

    for url, label in ((VTS1_URL, "VTS 1"), (VTS2_URL, "VTS 2")):
        vts.clear_column(url, "P_SIM")
        entry["clearDetails"].append({"vts": label, "column": "P_SIM", "success": True})
        messages.append(f"  Cleared {label} — P_SIM: Passed")

    messages.append("  Running Oracle P_SIM query (rownum <= 50000) …")
    rows, _cols = oq.run_sql(sql)
    messages.append(f"  Query returned {len(rows)} row(s).")
    if len(rows) < need_total:
        entry["message"] = (
            f"Oracle P_SIM query returned {len(rows)} rows; expected {need_total}."
        )
        messages.append(f"  FAILED — {entry['message']}")
        return entry

    vts2_rows_raw, vts1_rows_raw = oq.split_p_sim_rows(rows)
    if len(vts2_rows_raw) != need_vts2 or len(vts1_rows_raw) != need_vts1:
        entry["message"] = (
            f"Split failed — VTS2 needs {need_vts2}, VTS1 needs {need_vts1}; "
            f"got {len(vts2_rows_raw)} and {len(vts1_rows_raw)}."
        )
        messages.append(f"  FAILED — {entry['message']}")
        return entry

    vts2_path = out_dir / "P_SIM_VTS2.txt"
    vts1_path = out_dir / "P_SIM_VTS1.txt"
    vts2_count = oq.write_p_sim_column_txt(vts2_path, vts2_rows_raw)
    vts1_count = oq.write_p_sim_column_txt(vts1_path, vts1_rows_raw)
    entry["txtFile"] = f"{vts2_path.name}, {vts1_path.name}"
    entry["rowCount"] = vts2_count + vts1_count
    messages.append(
        f"  Wrote {vts2_path.name} ({vts2_count} rows, header P_SIM) for VTS 2."
    )
    messages.append(
        f"  Wrote {vts1_path.name} ({vts1_count} rows, header P_SIM) for VTS 1 "
        f"(rows 10001–50000, no overlap with VTS 2)."
    )

    _, vts2_import_rows = oq.rows_for_vts_import("p_sim", vts2_rows_raw)
    _, vts1_import_rows = oq.rows_for_vts_import("p_sim", vts1_rows_raw)

    messages.append("  Import order: VTS 2 first (10,000), then VTS 1 (40,000).")
    imp2 = _import_vts_data(
        VTS2_URL, "VTS 2", headers, vts2_import_rows, vts2_path, vts2_path.name, messages
    )
    imp1 = _import_vts_data(
        VTS1_URL, "VTS 1", headers, vts1_import_rows, vts1_path, vts1_path.name, messages
    )
    entry["imports"] = [
        {
            "vts": "VTS 2",
            "txtFile": str(vts2_path.relative_to(proj)),
            "rowCount": vts2_count,
            "import": imp2,
        },
        {
            "vts": "VTS 1",
            "txtFile": str(vts1_path.relative_to(proj)),
            "rowCount": vts1_count,
            "import": imp1,
        },
    ]
    entry["import"] = {"vts1": imp1, "vts2": imp2}

    ok2 = bool(imp2.get("verified"))
    ok1 = bool(imp1.get("verified"))
    if ok2 and ok1:
        entry["success"] = True
        entry["message"] = (
            f"Validation 2 complete — VTS 2 P_SIM={vts2_count}, "
            f"VTS 1 P_SIM={vts1_count} (disjoint sets)."
        )
        messages.append(f"  {entry['message']}")
        return entry

    entry["message"] = "P_SIM import failed on one or both VTS instances."
    messages.append(f"  FAILED — {entry['message']}")
    for imp, label, expected in (
        (imp2, "VTS 2", need_vts2),
        (imp1, "VTS 1", need_vts1),
    ):
        if imp.get("verified"):
            continue
        messages.append(f"    {label}: {imp.get('message') or 'import failed'}")
        for row in (imp.get("verification") or {}).get("rows") or []:
            messages.append(
                f"      {row.get('column')}: expected {expected}, "
                f"actual {row.get('actual')}"
            )
    return entry


def _remediate_p_imei_ip14128pur(
    sql: str,
    out_dir: Path,
    proj: Path,
    messages: list[str],
) -> dict[str, Any]:
    """
    Validation 3 (VTS 1 — P_IMEI_IP14128PUR):
    Trigger: P_IMEI_IP14128PUR count < 8000.
    Clear column → Oracle query (up to 20k IMEI rows) → TXT with header → VTS1 import.
    Uses same fileToUpload POST /data/import_csv as Validation 1.
    """
    meta = oq.QUERY_META["p_imei_ip14128pur"]
    col = "P_IMEI_IP14128PUR"
    entry: dict[str, Any] = {
        "issue": "p_imei_ip14128pur",
        "label": meta["label"],
        "success": False,
        "clearDetails": [],
        "txtFile": None,
        "rowCount": 0,
        "import": None,
    }

    vts.clear_column(VTS1_URL, col)
    entry["clearDetails"].append({"vts": "VTS 1", "column": col, "success": True})
    messages.append(f"  Cleared VTS 1 — {col}: Passed")

    messages.append("  Running Oracle P_IMEI query (rownum <= 20000) …")
    rows, _cols = oq.run_sql(sql)
    messages.append(f"  Query returned {len(rows)} row(s).")
    if not rows:
        entry["message"] = "Oracle P_IMEI query returned no rows."
        messages.append(f"  FAILED — {entry['message']}")
        return entry

    txt_path = out_dir / meta["txtFile"]
    row_count = oq.write_remediation_txt(txt_path, "p_imei_ip14128pur", rows, _cols)
    entry["txtFile"] = str(txt_path.relative_to(proj))
    entry["rowCount"] = row_count
    messages.append(
        f"  Wrote {meta['txtFile']} ({row_count} data rows + header {col})."
    )

    headers, vts_rows = oq.rows_for_vts_import("p_imei_ip14128pur", rows)
    imp = _import_vts_data(
        VTS1_URL,
        "VTS 1",
        headers,
        vts_rows,
        txt_path,
        meta["txtFile"],
        messages,
    )
    entry["import"] = imp
    if not imp.get("verified"):
        entry["message"] = imp.get("message") or "VTS import failed verification."
        messages.append(f"  FAILED — {entry['message']}")
        upload = imp.get("upload") or {}
        if upload.get("responseBody"):
            messages.append(f"    VTS response: {upload['responseBody'][:200]}")
        for row in (imp.get("verification") or {}).get("rows") or []:
            messages.append(
                f"    {row.get('column')}: expected {row.get('expected')}, "
                f"actual {row.get('actual')}"
            )
        return entry

    entry["success"] = True
    entry["message"] = (
        f"Validation 3 complete — imported {row_count} rows into VTS 1 {col}."
    )
    messages.append(f"  {entry['message']}")
    return entry


def _remediate_p_ban_c_vts2(
    sql: str,
    out_dir: Path,
    proj: Path,
    messages: list[str],
) -> dict[str, Any]:
    """
    Validation 4 (VTS 2 — P_BAN_C & current columns):
    Trigger: P_BAN_C < 1000 OR current column counts not aligned with P_BAN_C.
    Clear 8 columns → Oracle query → drop rows with != 8 fields →
    combined TXT → VTS2 import (fileToUpload).
    """
    meta = oq.QUERY_META["p_ban_c_vts2"]
    columns = list(meta["clearColumns"])
    entry: dict[str, Any] = {
        "issue": "p_ban_c_vts2",
        "label": meta["label"],
        "success": False,
        "clearDetails": [],
        "txtFile": None,
        "rowCount": 0,
        "import": None,
    }

    for col in columns:
        vts.clear_column(VTS2_URL, col)
        entry["clearDetails"].append({"vts": "VTS 2", "column": col, "success": True})
        messages.append(f"  Cleared VTS 2 — {col}: Passed")

    messages.append("  Running Oracle P_BAN_C (current) query …")
    rows, _cols = oq.run_sql(sql)
    messages.append(f"  Query returned {len(rows)} row(s).")
    if not rows:
        entry["message"] = "Oracle P_BAN_C query returned no rows."
        messages.append(f"  FAILED — {entry['message']}")
        return entry

    # Drop any row with more (or fewer) than 8 CSV fields before import.
    kept_rows, kept_count, skipped_count = oq.filter_p_ban_c_import_rows(rows)
    if skipped_count:
        messages.append(
            f"  Skipped {skipped_count} row(s) with more/fewer than 8 fields "
            f"(not written to import file)."
        )
    messages.append(f"  Rows kept for import: {kept_count}.")
    if not kept_rows:
        entry["message"] = (
            "No valid 8-field rows left after filtering oversized/undersized SQL rows."
        )
        messages.append(f"  FAILED — {entry['message']}")
        return entry

    headers, vts_rows = oq.rows_for_vts_import("p_ban_c_vts2", kept_rows)
    if not vts_rows:
        entry["message"] = "Could not parse query rows into 8 VTS columns."
        messages.append(f"  FAILED — {entry['message']}")
        return entry

    txt_path = out_dir / meta["txtFile"]
    row_count = oq.write_remediation_txt(txt_path, "p_ban_c_vts2", kept_rows, _cols)
    if row_count != len(vts_rows):
        row_count = len(vts_rows)
    entry["txtFile"] = str(txt_path.relative_to(proj))
    entry["rowCount"] = row_count
    entry["skippedOversizedRows"] = skipped_count
    messages.append(
        f"  Wrote {meta['txtFile']} ({row_count} data rows + header "
        f"{','.join(headers)})."
    )

    imp = _import_vts_data(
        VTS2_URL,
        "VTS 2",
        headers,
        vts_rows,
        txt_path,
        meta["txtFile"],
        messages,
    )
    entry["import"] = imp
    if not imp.get("verified"):
        entry["message"] = imp.get("message") or "VTS import failed verification."
        messages.append(f"  FAILED — {entry['message']}")
        upload = imp.get("upload") or {}
        if upload.get("responseBody"):
            messages.append(f"    VTS response: {upload['responseBody'][:200]}")
        for row in (imp.get("verification") or {}).get("rows") or []:
            messages.append(
                f"    {row.get('column')}: expected {row.get('expected')}, "
                f"actual {row.get('actual')}"
            )
        return entry

    entry["success"] = True
    entry["message"] = (
        f"Validation 4 complete — imported {row_count} rows into VTS 2 "
        f"({len(columns)} aligned columns)."
    )
    messages.append(f"  {entry['message']}")
    return entry


def _remediate_p_new_imei_vts2(
    sql: str,
    out_dir: Path,
    proj: Path,
    messages: list[str],
) -> dict[str, Any]:
    """
    Validation 5 (VTS 2 — P_NEW_IMEI):
    Trigger: P_NEW_IMEI count < 3000 (no P_BAN_H comparison).
    Clear column → Oracle query (up to 4k IMEI rows) → TXT with header → VTS2 import.
    """
    meta = oq.QUERY_META["p_new_imei_vts2"]
    col = "P_NEW_IMEI"
    entry: dict[str, Any] = {
        "issue": "p_new_imei_vts2",
        "label": meta["label"],
        "success": False,
        "clearDetails": [],
        "txtFile": None,
        "rowCount": 0,
        "import": None,
    }

    vts.clear_column(VTS2_URL, col)
    entry["clearDetails"].append({"vts": "VTS 2", "column": col, "success": True})
    messages.append(f"  Cleared VTS 2 — {col}: Passed")

    messages.append("  Running Oracle P_NEW_IMEI query (rownum <= 4000) …")
    rows, _cols = oq.run_sql(sql)
    messages.append(f"  Query returned {len(rows)} row(s).")
    if not rows:
        entry["message"] = "Oracle P_NEW_IMEI query returned no rows."
        messages.append(f"  FAILED — {entry['message']}")
        return entry

    txt_path = out_dir / meta["txtFile"]
    row_count = oq.write_remediation_txt(txt_path, "p_new_imei_vts2", rows, _cols)
    entry["txtFile"] = str(txt_path.relative_to(proj))
    entry["rowCount"] = row_count
    messages.append(
        f"  Wrote {meta['txtFile']} ({row_count} data rows + header {col})."
    )

    headers, vts_rows = oq.rows_for_vts_import("p_new_imei_vts2", rows)
    imp = _import_vts_data(
        VTS2_URL,
        "VTS 2",
        headers,
        vts_rows,
        txt_path,
        meta["txtFile"],
        messages,
    )
    entry["import"] = imp
    if not imp.get("verified"):
        entry["message"] = imp.get("message") or "VTS import failed verification."
        messages.append(f"  FAILED — {entry['message']}")
        upload = imp.get("upload") or {}
        if upload.get("responseBody"):
            messages.append(f"    VTS response: {upload['responseBody'][:200]}")
        for row in (imp.get("verification") or {}).get("rows") or []:
            messages.append(
                f"    {row.get('column')}: expected {row.get('expected')}, "
                f"actual {row.get('actual')}"
            )
        return entry

    entry["success"] = True
    entry["message"] = (
        f"Validation 5 complete — imported {row_count} rows into VTS 2 {col}."
    )
    messages.append(f"  {entry['message']}")
    return entry


def _remediate_p_ban_h_vts2(
    history_sql_template: str,
    ban_sql: str,
    out_dir: Path,
    proj: Path,
    messages: list[str],
) -> dict[str, Any]:
    """
    Combined Trigger 1 + Validation 6:
    1) Clear VTS1 P_BAN/P_SUBSCRIBER + VTS2 history columns
    2) Run P_BAN Oracle query; reserve last 3000 BANs for history
    3) Import remaining pairs to VTS1
    4) Run history SQL 3× in parallel (1000 BANs each)
    5) Combine ~3000 output rows → one file → import VTS2 once
    """
    meta = oq.QUERY_META["p_ban_h_vts2"]
    history_columns = list(meta["clearColumns"])
    source_headers = ["P_BAN", "P_SUBSCRIBER"]
    need_total = oq.P_BAN_H_VTS2_TOTAL
    batch_size = oq.P_BAN_H_VTS2_BATCH_SIZE
    entry: dict[str, Any] = {
        "issue": "p_ban_h_vts2",
        "label": "VTS 1 P_BAN/P_SUBSCRIBER + VTS 2 history (Trigger 1+6)",
        "success": False,
        "clearDetails": [],
        "txtFile": None,
        "rowCount": 0,
        "import": None,
        "batches": [],
        "vts1Import": None,
    }

    # Clear VTS1 BAN columns and VTS2 history columns before reload.
    for col in source_headers:
        vts.clear_column(VTS1_URL, col)
        entry["clearDetails"].append({"vts": "VTS 1", "column": col, "success": True})
        messages.append(f"  Cleared VTS 1 — {col}: Passed")
    for col in history_columns:
        vts.clear_column(VTS2_URL, col)
        entry["clearDetails"].append({"vts": "VTS 2", "column": col, "success": True})
        messages.append(f"  Cleared VTS 2 — {col}: Passed")

    if not ban_sql.strip():
        entry["message"] = "P_BAN / P_SUBSCRIBER SQL query is empty."
        messages.append(f"  FAILED — {entry['message']}")
        return entry

    messages.append("  Running Oracle P_BAN / P_SUBSCRIBER query …")
    ban_rows, _ban_cols = oq.run_sql(ban_sql)
    messages.append(f"  P_BAN query returned {len(ban_rows)} row(s).")
    if not ban_rows:
        entry["message"] = "Oracle P_BAN query returned no rows."
        messages.append(f"  FAILED — {entry['message']}")
        return entry

    _, paired = oq.rows_for_vts_import("ban_subscriber", ban_rows)
    messages.append(f"  Parsed P_BAN/P_SUBSCRIBER pairs: {len(paired)}.")
    if len(paired) < need_total:
        entry["message"] = (
            f"P_BAN query produced {len(paired)} pairs; need at least {need_total} "
            f"to reserve last {need_total} for P_BAN_H."
        )
        messages.append(f"  FAILED — {entry['message']}")
        return entry

    reserved = paired[-need_total:]
    remaining = paired[:-need_total]
    reserved_bans = [str(row[0]).strip() for row in reserved if str(row[0] or "").strip()]
    batch_count = oq.P_BAN_H_VTS2_BATCHES
    if len(reserved_bans) < need_total:
        entry["message"] = (
            f"Only {len(reserved_bans)} BAN values in reserved block; need {need_total}."
        )
        messages.append(f"  FAILED — {entry['message']}")
        return entry

    # Prepare 3×1000 BAN batches (1st column only from reserved pairs).
    history_batches: list[tuple[int, list[str]]] = []
    for batch_no in range(1, batch_count + 1):
        start = (batch_no - 1) * batch_size
        batch_bans = reserved_bans[start : start + batch_size]
        if len(batch_bans) < batch_size:
            entry["message"] = (
                f"Batch {batch_no} has only {len(batch_bans)} BANs; need {batch_size}."
            )
            messages.append(f"  FAILED — {entry['message']}")
            return entry
        history_batches.append((batch_no, batch_bans))

    messages.append(
        f"  P_BAN_H: run history query {batch_count} times × {batch_size} BANs "
        f"(in parallel), combine output, import once to VTS 2. "
        f"Also importing {len(remaining)} remaining pairs to VTS 1."
    )

    # Run 3 history queries in parallel while VTS1 import runs.
    all_rows: list[tuple[Any, ...]] = []
    batch_results: list[dict[str, Any]] = []
    history_error: str | None = None

    def _collect_history() -> None:
        nonlocal all_rows, batch_results, history_error
        ordered: dict[int, dict[str, Any]] = {}
        try:
            with ThreadPoolExecutor(max_workers=batch_count) as pool:
                futures = {
                    pool.submit(
                        _run_history_batch,
                        history_sql_template,
                        batch_no,
                        batch_bans,
                    ): batch_no
                    for batch_no, batch_bans in history_batches
                }
                for fut in as_completed(futures):
                    batch_no = futures[fut]
                    try:
                        result = fut.result()
                    except Exception as exc:
                        history_error = f"Oracle history batch {batch_no} failed: {exc}"
                        return
                    ordered[batch_no] = result
                    messages.append(
                        f"  History batch {batch_no}/{batch_count}: "
                        f"{result['rowCount']} row(s) in {result['elapsedSec']}s."
                    )
            for batch_no in range(1, batch_count + 1):
                result = ordered[batch_no]
                batch_results.append(
                    {
                        "batch": batch_no,
                        "banCount": result["banCount"],
                        "rowCount": result["rowCount"],
                        "elapsedSec": result["elapsedSec"],
                    }
                )
                all_rows.extend(result["rows"])
        except Exception as exc:
            history_error = str(exc)

    remaining_path = out_dir / "P_BAN_P_SUBSCRIBER.txt"
    rem_lines = [",".join(source_headers)]
    for ban, sub in remaining:
        rem_lines.append(f"{ban},{sub}")
    remaining_path.write_bytes(("\r\n".join(rem_lines) + "\r\n").encode("utf-8"))
    rem_count = len(remaining)
    messages.append(
        f"  Wrote {remaining_path.name} ({rem_count} data rows + header "
        f"{','.join(source_headers)})."
    )

    with ThreadPoolExecutor(max_workers=1) as hist_pool:
        hist_future = hist_pool.submit(_collect_history)

        if remaining:
            vts1_imp = _import_vts_data(
                VTS1_URL,
                "VTS 1",
                source_headers,
                remaining,
                remaining_path,
                remaining_path.name,
                messages,
                quick=True,
            )
        else:
            verify = vts.verify_column_counts(
                VTS1_URL, {col: 0 for col in source_headers}
            )
            vts1_imp = {
                "verified": bool(verify.get("verified")),
                "message": "VTS 1 left empty (all pairs reserved for P_BAN_H).",
                "verification": verify,
            }
            messages.append(f"  {vts1_imp['message']}")

        hist_future.result()

    entry["vts1Import"] = {
        "txtFile": str(remaining_path.relative_to(proj)),
        "rowCount": rem_count,
        "import": vts1_imp,
    }
    entry["batches"] = batch_results

    if not vts1_imp.get("verified"):
        entry["message"] = (
            vts1_imp.get("message") or "VTS 1 P_BAN/P_SUBSCRIBER import failed."
        )
        messages.append(f"  FAILED — {entry['message']}")
        return entry

    if history_error:
        entry["message"] = (
            f"{history_error} VTS 1 already imported {rem_count} pairs; "
            "P_BAN_H was not imported."
        )
        messages.append(f"  FAILED — {entry['message']}")
        return entry

    if not all_rows:
        entry["message"] = (
            "Oracle history query returned no rows across 3 batches. "
            f"VTS 1 already imported {rem_count} pairs; P_BAN_H not imported."
        )
        messages.append(f"  FAILED — {entry['message']}")
        return entry

    # Combine ~3000 output rows → one file → one VTS2 import.
    headers, vts_rows = oq.rows_for_vts_import("p_ban_h_vts2", all_rows)
    if not vts_rows:
        entry["message"] = (
            "Could not parse history query rows into 7 VTS columns. "
            f"VTS 1 already imported {rem_count} pairs; P_BAN_H not imported."
        )
        messages.append(f"  FAILED — {entry['message']}")
        return entry

    txt_path = out_dir / meta["txtFile"]
    row_count = oq.write_remediation_txt(txt_path, "p_ban_h_vts2", all_rows, [])
    if row_count != len(vts_rows):
        row_count = len(vts_rows)
    entry["txtFile"] = str(txt_path.relative_to(proj))
    entry["rowCount"] = row_count
    messages.append(
        f"  Combined history output: {row_count} row(s) → {meta['txtFile']} "
        f"(header {','.join(headers)})."
    )

    imp = _import_vts_data(
        VTS2_URL,
        "VTS 2",
        headers,
        vts_rows,
        txt_path,
        meta["txtFile"],
        messages,
        quick=True,
    )
    entry["import"] = imp
    if not imp.get("verified"):
        entry["message"] = (
            (imp.get("message") or "VTS import failed verification.")
            + f" VTS 1 already imported {rem_count} pairs; P_BAN_H import failed."
        )
        messages.append(f"  FAILED — {entry['message']}")
        return entry

    entry["success"] = True
    entry["message"] = (
        f"Trigger 1+6 complete — VTS 1: {rem_count} pairs; "
        f"VTS 2: {row_count} history rows from 3×{batch_size} Oracle queries."
    )
    messages.append(f"  {entry['message']}")
    return entry


def run_vts1_remediation(
    issues: list[str] | None = None,
    *,
    precheck: dict[str, Any] | None = None,
    prep: dict[str, Any] | None = None,
    query_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Oracle extract + TXT export + VTS import for failed VTS1 validations."""
    issue_list = list(issues or vts1_remediation_issues(precheck=precheck, prep=prep))
    if not issue_list:
        return {
            "success": True,
            "type": "data-loading-vts1-remediation",
            "message": "No VTS 1 remediation required.",
            "results": [],
            "messages": [],
        }

    messages: list[str] = []
    results: list[dict[str, Any]] = []
    queries = {**oq.load_queries(), **(query_overrides or {})}

    proj = Path(__file__).resolve().parent.parent
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = proj / "exports" / f"vts_remediate_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    messages.append("=== VTS remediation (Oracle → TXT → import) ===")

    for issue_key in issue_list:
        meta = oq.QUERY_META.get(issue_key)
        if not meta:
            continue
        sql = queries.get(issue_key, "").strip()

        if issue_key == "p_sim":
            messages.append(f"--- {meta['label']} ---")
            if not sql:
                results.append(
                    {
                        "issue": issue_key,
                        "label": meta["label"],
                        "success": False,
                        "message": "SQL query is empty.",
                    }
                )
                messages.append("  FAILED — SQL query is empty.")
                continue
            try:
                entry = _remediate_p_sim_dual(sql, out_dir, proj, messages)
            except Exception as exc:
                entry = {
                    "issue": issue_key,
                    "label": meta["label"],
                    "success": False,
                    "message": str(exc),
                }
                messages.append(f"  FAILED — {exc}")
            results.append(entry)
            continue

        if issue_key == "p_imei_ip14128pur":
            messages.append(f"--- {meta['label']} ---")
            if not sql:
                results.append(
                    {
                        "issue": issue_key,
                        "label": meta["label"],
                        "success": False,
                        "message": "SQL query is empty.",
                    }
                )
                messages.append("  FAILED — SQL query is empty.")
                continue
            try:
                entry = _remediate_p_imei_ip14128pur(sql, out_dir, proj, messages)
            except Exception as exc:
                entry = {
                    "issue": issue_key,
                    "label": meta["label"],
                    "success": False,
                    "message": str(exc),
                }
                messages.append(f"  FAILED — {exc}")
            results.append(entry)
            continue

        if issue_key == "p_ban_c_vts2":
            messages.append(f"--- {meta['label']} ---")
            if not sql:
                results.append(
                    {
                        "issue": issue_key,
                        "label": meta["label"],
                        "success": False,
                        "message": "SQL query is empty.",
                    }
                )
                messages.append("  FAILED — SQL query is empty.")
                continue
            try:
                entry = _remediate_p_ban_c_vts2(sql, out_dir, proj, messages)
            except Exception as exc:
                entry = {
                    "issue": issue_key,
                    "label": meta["label"],
                    "success": False,
                    "message": str(exc),
                }
                messages.append(f"  FAILED — {exc}")
            results.append(entry)
            continue

        if issue_key == "p_new_imei_vts2":
            messages.append(f"--- {meta['label']} ---")
            if not sql:
                results.append(
                    {
                        "issue": issue_key,
                        "label": meta["label"],
                        "success": False,
                        "message": "SQL query is empty.",
                    }
                )
                messages.append("  FAILED — SQL query is empty.")
                continue
            try:
                entry = _remediate_p_new_imei_vts2(sql, out_dir, proj, messages)
            except Exception as exc:
                entry = {
                    "issue": issue_key,
                    "label": meta["label"],
                    "success": False,
                    "message": str(exc),
                }
                messages.append(f"  FAILED — {exc}")
            results.append(entry)
            continue

        if issue_key == "p_ban_h_vts2":
            messages.append(
                "--- VTS 1 P_BAN/P_SUBSCRIBER + VTS 2 history (Trigger 1+6) ---"
            )
            ban_sql = queries.get("ban_subscriber", "").strip()
            if not sql:
                results.append(
                    {
                        "issue": issue_key,
                        "label": meta["label"],
                        "success": False,
                        "message": "History SQL query is empty.",
                    }
                )
                messages.append("  FAILED — History SQL query is empty.")
                continue
            if not ban_sql:
                results.append(
                    {
                        "issue": issue_key,
                        "label": meta["label"],
                        "success": False,
                        "message": "P_BAN / P_SUBSCRIBER SQL query is empty.",
                    }
                )
                messages.append("  FAILED — P_BAN / P_SUBSCRIBER SQL query is empty.")
                continue
            try:
                entry = _remediate_p_ban_h_vts2(
                    sql, ban_sql, out_dir, proj, messages
                )
            except Exception as exc:
                entry = {
                    "issue": issue_key,
                    "label": meta["label"],
                    "success": False,
                    "message": str(exc),
                }
                messages.append(f"  FAILED — {exc}")
            results.append(entry)
            continue

        entry: dict[str, Any] = {
            "issue": issue_key,
            "label": meta["label"],
            "success": False,
            "clearDetails": [],
            "txtFile": None,
            "rowCount": 0,
            "import": None,
        }
        messages.append(f"--- {meta['label']} ---")

        if not sql:
            entry["message"] = "SQL query is empty."
            messages.append(f"  FAILED — {entry['message']}")
            results.append(entry)
            continue

        try:
            for col in meta["clearColumns"]:
                vts.clear_column(VTS1_URL, col)
                clear_row = {"vts": "VTS 1", "column": col, "success": True}
                entry["clearDetails"].append(clear_row)
                messages.append(f"  Cleared {col}: Passed")

            messages.append("  Running Oracle query …")
            rows, _cols = oq.run_sql(sql)
            messages.append(f"  Query returned {len(rows)} row(s).")
            if not rows:
                entry["message"] = "Oracle query returned no rows."
                messages.append(f"  FAILED — {entry['message']}")
                results.append(entry)
                continue

            txt_path = out_dir / meta["txtFile"]
            row_count = oq.write_remediation_txt(txt_path, issue_key, rows, _cols)
            entry["txtFile"] = str(txt_path.relative_to(proj))
            entry["rowCount"] = row_count
            messages.append(f"  Wrote {meta['txtFile']} ({row_count} data rows + header).")

            headers, vts_rows = oq.rows_for_vts_import(issue_key, rows)
            imp = _import_vts_data(
                VTS1_URL,
                "VTS 1",
                headers,
                vts_rows,
                txt_path,
                meta["txtFile"],
                messages,
            )
            entry["import"] = imp
            if not imp.get("verified"):
                entry["message"] = imp.get("message") or "VTS import failed verification."
                messages.append(f"  FAILED — {entry['message']}")
                upload = imp.get("upload") or {}
                if upload.get("responseBody"):
                    messages.append(f"    VTS response: {upload['responseBody'][:200]}")
                if imp.get("verification"):
                    for row in imp["verification"].get("rows") or []:
                        messages.append(
                            f"    {row.get('column')}: expected {row.get('expected')}, "
                            f"actual {row.get('actual')}"
                        )
                results.append(entry)
                continue
            messages.append(
                f"  VTS import verified — "
                + ", ".join(
                    f"{c}={imp.get('expectedCounts', {}).get(c)}"
                    for c in meta["clearColumns"]
                )
            )
            entry["success"] = True
            entry["message"] = f"Imported {row_count} rows into VTS 1."
            messages.append(f"  {entry['message']}")
        except Exception as exc:
            entry["message"] = str(exc)
            messages.append(f"  FAILED — {exc}")
        results.append(entry)

    all_ok = all(r.get("success") for r in results)
    return {
        "success": all_ok,
        "type": "data-loading-vts1-remediation",
        "message": "VTS remediation completed." if all_ok else "VTS remediation had failures.",
        "results": results,
        "exportDir": str(out_dir.relative_to(proj)),
        "messages": messages,
        "issues": issue_list,
    }


def run_task1_flow(query_overrides: dict[str, str] | None = None) -> dict[str, Any]:
    """Full Task 1: pre-check → VTS1 remediate/retry → prep → VTS1 remediate/retry."""
    messages: list[str] = []
    remediation_runs: list[dict[str, Any]] = []

    precheck = run_vts_precheck()
    messages.extend(precheck.get("messages") or [])

    issues = remediation_issues(precheck=precheck)
    if issues:
        rem = run_vts1_remediation(issues, query_overrides=query_overrides)
        remediation_runs.append(rem)
        messages.extend(rem.get("messages") or [])
        if rem.get("success"):
            precheck = run_vts_precheck()
            messages.extend(precheck.get("messages") or [])

    if not precheck.get("canProceed"):
        messages.append(
            "Pre-check failed — removing old test data anyway (mandatory)."
        )
        clear_details = _clear_old_test_columns(messages)
        prep = {
            "success": False,
            "type": "data-loading-vts-prep",
            "message": "Pre-check failed. Old test data was still cleared.",
            "validationPassed": False,
            "dataDetails": {},
            "validationDetails": [],
            "clearDetails": clear_details,
            "messages": [],
            "prepCompleted": False,
        }
        return {
            "success": False,
            "phase": "insufficient",
            "precheck": precheck,
            "prep": prep,
            "remediation": remediation_runs,
            "messages": messages,
            "guidance": build_user_guidance(precheck=precheck),
            "validationSummary": build_validation_summary(precheck=precheck),
            "queryDefaults": query_defaults(),
        }

    prep = run_vts_prep()
    messages.extend(prep.get("messages") or [])

    if not prep.get("success"):
        issues = remediation_issues(prep=prep)
        if issues:
            rem = run_vts1_remediation(issues, query_overrides=query_overrides)
            remediation_runs.append(rem)
            messages.extend(rem.get("messages") or [])
            if rem.get("success"):
                prep = run_vts_prep()
                messages.extend(prep.get("messages") or [])

        if not prep.get("success"):
            return {
                "success": False,
                "phase": "error",
                "precheck": precheck,
                "prep": prep,
                "remediation": remediation_runs,
                "messages": messages,
                "guidance": build_user_guidance(precheck=precheck, prep=prep),
                "validationSummary": build_validation_summary(
                    precheck=precheck, prep=prep
                ),
                "queryDefaults": query_defaults(),
            }

    return {
        "success": True,
        "phase": "prep-done",
        "precheck": precheck,
        "prep": prep,
        "remediation": remediation_runs,
        "messages": messages,
        "guidance": [],
        "validationSummary": build_validation_summary(precheck=precheck, prep=prep),
        "queryDefaults": query_defaults(),
    }


def _clear_old_test_columns(messages: list[str]) -> list[dict[str, Any]]:
    """Clear legacy test columns on VTS 1 and VTS 2. Always attempted."""
    messages.append("=== Removing old test data ===")
    clear_details: list[dict[str, Any]] = []
    targets = (
        ("VTS 2", VTS2_URL, vts.VTS2_CLEAR_COLUMNS),
        ("VTS 1", VTS1_URL, vts.VTS1_CLEAR_COLUMNS),
    )
    for label, url, columns in targets:
        for col in columns:
            try:
                vts.clear_column(url, col)
                clear_details.append({"vts": label, "column": col, "success": True})
                messages.append(f"  Cleared {label} — {col}: Passed")
            except Exception as exc:
                clear_details.append(
                    {
                        "vts": label,
                        "column": col,
                        "success": False,
                        "message": str(exc),
                    }
                )
                messages.append(f"  Cleared {label} — {col}: Failed — {exc}")
    if clear_details and all(row["success"] for row in clear_details):
        messages.append("Old test data has been removed.")
    else:
        messages.append("Old test data clear finished with one or more failures.")
    return clear_details


def _prep_validation_columns() -> tuple[list[str], list[str]]:
    vts1_cols = list(vts.VTS1_ALIGN_COLUMNS) + ["P_SIM", "P_IMEI_IP14128PUR"]
    vts2_cols = (
        list(vts.VTS2_ALIGN_GROUP_C)
        + list(vts.VTS2_ALIGN_GROUP_H)
        + ["P_NEW_IMEI", "P_SIM"]
    )
    return vts1_cols, vts2_cols


def _alignment_validation_entry(align: dict[str, Any], *, id: str, name: str) -> dict[str, Any]:
    counts = align.get("counts") or {}
    ref = align.get("referenceCount")
    rows = [
        {
            "column": col,
            "count": counts.get(col),
            "passed": counts.get(col) == ref if ref is not None else None,
        }
        for col in align.get("columns") or list(counts.keys())
    ]
    return {
        "id": id,
        "name": name,
        "passed": bool(align.get("aligned")),
        "rule": "All listed column counts must be equal",
        "referenceCount": ref,
        "rows": rows,
        "message": align.get("message", ""),
    }


def _min_count_validation_entry(
    counts_per_column: dict[str, Any],
    column: str,
    required: int,
    *,
    id: str,
    name: str,
) -> dict[str, Any]:
    count = vts.column_count(counts_per_column, column)
    passed = count is not None and count >= required
    status = "PASSED" if passed else "FAILED"
    return {
        "id": id,
        "name": name,
        "passed": passed,
        "rule": f"{column} >= {required}",
        "rows": [{"column": column, "count": count, "passed": passed}],
        "message": f"{name}: {status} — count {count if count is not None else 'missing'}, required >= {required}",
    }


def run_vts_precheck() -> dict[str, Any]:
    """Query VTS1/VTS2 get_page APIs and verify minimum column counts."""
    messages: list[str] = []
    vts1_mins = vts.get_vts1_min_counts()
    vts2_mins = vts.get_vts2_min_counts()
    messages.append(f"Checking VTS 1 at {VTS1_URL} …")
    vts1_rules = ", ".join(f"{col} >= {req}" for col, req in vts1_mins.items())
    messages.append(f"  VTS 1 pre-check rules: {vts1_rules}")
    vts1_payload = vts.fetch_get_page(VTS1_URL)
    vts1_counts = vts1_payload["countsPerColumn"]
    vts1_result = vts.check_min_counts(vts1_counts, vts1_mins, label="VTS 1")
    messages.extend(vts1_result["messages"])

    messages.append(f"Checking VTS 2 at {VTS2_URL} …")
    vts2_rules = ", ".join(f"{col} >= {req}" for col, req in vts2_mins.items())
    messages.append(f"  VTS 2 pre-check rules: {vts2_rules}")
    vts2_payload = vts.fetch_get_page(VTS2_URL)
    vts2_counts = vts2_payload["countsPerColumn"]
    vts2_result = vts.check_min_counts(vts2_counts, vts2_mins, label="VTS 2")
    messages.extend(vts2_result["messages"])

    can_proceed = vts1_result["safe"] and vts2_result["safe"]
    all_passed = [
        f"VTS 1:{col}" for col in vts1_result.get("passedColumns", [])
    ] + [f"VTS 2:{col}" for col in vts2_result.get("passedColumns", [])]
    all_failed = [
        f"VTS 1:{col}" for col in vts1_result.get("failedColumns", [])
    ] + [f"VTS 2:{col}" for col in vts2_result.get("failedColumns", [])]

    messages.append("=== Pre-check summary ===")
    messages.append(
        f"Passed ({len(all_passed)}): {', '.join(all_passed) if all_passed else 'none'}"
    )
    messages.append(
        f"Failed ({len(all_failed)}): {', '.join(all_failed) if all_failed else 'none'}"
    )
    if can_proceed:
        messages.append(
            "All pre-checks passed — proceeding to validations and removing old test data."
        )
    else:
        messages.append(
            "Pre-check failed — RUN will auto-clear, query Oracle, and import into VTS where applicable."
        )

    return {
        "success": True,
        "type": "data-loading-vts-precheck",
        "vts1": {**vts1_result, "url": VTS1_URL},
        "vts2": {**vts2_result, "url": VTS2_URL},
        "passedColumns": all_passed,
        "failedColumns": all_failed,
        "canProceed": can_proceed,
        "queryDefaults": query_defaults(),
        "messages": messages,
    }


def run_vts_prep() -> dict[str, Any]:
    """Validate VTS column alignment, clear old test columns, mark prep complete."""
    messages: list[str] = []
    validation_details: list[dict[str, Any]] = []
    vts1_cols, vts2_cols = _prep_validation_columns()

    messages.append("=== Data details (countsPerColumn) ===")
    messages.append(f"Validation counts for VTS 1 are read from {VTS1_URL}")
    vts1_payload = vts.fetch_get_page(VTS1_URL)
    vts1_counts = vts1_payload["countsPerColumn"]
    vts1_snapshot = vts.counts_snapshot(vts1_counts, vts1_cols)
    for col, count in vts1_snapshot.items():
        messages.append(f"  VTS 1 — {col}: {count if count is not None else 'missing'}")

    messages.append(f"Validation counts for VTS 2 are read from {VTS2_URL}")
    vts2_payload = vts.fetch_get_page(VTS2_URL)
    vts2_counts = vts2_payload["countsPerColumn"]
    vts2_snapshot = vts.counts_snapshot(vts2_counts, vts2_cols)
    for col, count in vts2_snapshot.items():
        messages.append(f"  VTS 2 — {col}: {count if count is not None else 'missing'}")

    data_details = {
        "vts1": {
            "label": "VTS 1",
            "url": VTS1_URL,
            "counts": vts1_snapshot,
        },
        "vts2": {
            "label": "VTS 2",
            "url": VTS2_URL,
            "counts": vts2_snapshot,
        },
    }

    messages.append("=== Validations ===")
    vts1_align = vts.check_columns_equal(
        vts1_counts, vts.VTS1_ALIGN_COLUMNS, label="VTS 1 (P_BAN & P_SUBSCRIBER)"
    )
    v1_entry = _alignment_validation_entry(
        vts1_align, id="vts1-ban-subscriber", name="VTS 1 — P_BAN & P_SUBSCRIBER alignment"
    )
    validation_details.append(v1_entry)
    messages.append(v1_entry["message"])

    vts1_mins = vts.get_vts1_min_counts()
    vts2_mins = vts.get_vts2_min_counts()
    for col in ("P_SIM", "P_IMEI_IP14128PUR"):
        required = vts1_mins[col]
        min_entry = _min_count_validation_entry(
            vts1_counts,
            col,
            required,
            id=f"vts1-{col.lower()}",
            name=f"VTS 1 — {col} >= {required}",
        )
        validation_details.append(min_entry)
        messages.append(min_entry["message"])

    vts2_p_sim = _min_count_validation_entry(
        vts2_counts,
        "P_SIM",
        vts2_mins["P_SIM"],
        id="vts2-p_sim",
        name=f"VTS 2 — P_SIM >= {vts2_mins['P_SIM']}",
    )
    validation_details.append(vts2_p_sim)
    messages.append(vts2_p_sim["message"])

    vts2_p_new_imei = _min_count_validation_entry(
        vts2_counts,
        "P_NEW_IMEI",
        vts2_mins["P_NEW_IMEI"],
        id="vts2-p_new_imei",
        name=f"VTS 2 — P_NEW_IMEI >= {vts2_mins['P_NEW_IMEI']}",
    )
    validation_details.append(vts2_p_new_imei)
    messages.append(vts2_p_new_imei["message"])

    vts2_align_c = vts.check_columns_equal(
        vts2_counts, vts.VTS2_ALIGN_GROUP_C, label="VTS 2 (current)"
    )
    v2c_entry = _alignment_validation_entry(
        vts2_align_c, id="vts2-current", name="VTS 2 — current column alignment"
    )
    validation_details.append(v2c_entry)
    messages.append(v2c_entry["message"])

    vts2_align_h = vts.check_columns_equal(
        vts2_counts, vts.VTS2_ALIGN_GROUP_H, label="VTS 2 (history)"
    )
    v2h_entry = _alignment_validation_entry(
        vts2_align_h, id="vts2-history", name="VTS 2 — history column alignment"
    )
    validation_details.append(v2h_entry)
    messages.append(v2h_entry["message"])

    vts2_p_ban_h = _min_count_validation_entry(
        vts2_counts,
        "P_BAN_H",
        vts2_mins["P_BAN_H"],
        id="vts2-p_ban_h",
        name=f"VTS 2 — P_BAN_H >= {vts2_mins['P_BAN_H']}",
    )
    validation_details.append(vts2_p_ban_h)
    messages.append(vts2_p_ban_h["message"])

    all_passed = all(v["passed"] for v in validation_details)

    failure_message: str | None = None
    if not v1_entry["passed"]:
        failure_message = "P_BAN & P_SUBSCRIBER data is not aligned."
    else:
        for v in validation_details:
            if v["id"] in (
                "vts1-p_sim",
                "vts2-p_sim",
                "vts1-p_imei_ip14128pur",
                "vts2-p_new_imei",
                "vts2-p_ban_h",
            ) and not v["passed"]:
                failure_message = v["message"]
                break
        if failure_message is None and (not v2c_entry["passed"] or not v2h_entry["passed"]):
            detail = []
            if not v2c_entry["passed"]:
                detail.append("current subscriber/device columns")
            if not v2h_entry["passed"]:
                detail.append("history columns")
            failure_message = f"VTS 2 data is not aligned ({' and '.join(detail)})."

    if failure_message:
        messages.append(
            "Validation did not pass — removing old test data anyway (mandatory)."
        )

    clear_details = _clear_old_test_columns(messages)
    clear_ok = bool(clear_details) and all(row.get("success") for row in clear_details)

    if failure_message:
        fail_prep = {
            "success": False,
            "type": "data-loading-vts-prep",
            "message": failure_message,
            "validationPassed": False,
            "dataDetails": data_details,
            "validationDetails": validation_details,
            "clearDetails": clear_details,
            "messages": messages,
            "prepCompleted": False,
        }
        fail_prep["queryDefaults"] = query_defaults()
        return fail_prep

    return {
        "success": clear_ok and all_passed,
        "type": "data-loading-vts-prep",
        "validationPassed": all_passed,
        "dataDetails": data_details,
        "validationDetails": validation_details,
        "clearDetails": clear_details,
        "messages": messages,
        "prepCompleted": clear_ok,
        "message": (
            "Old test data has been removed."
            if clear_ok
            else "Validations passed, but clearing old test data had failures."
        ),
    }


def run_p_ban_reload(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Manual P_BAN / P_SUBSCRIBER reload using form params to build SQL."""
    query_params = ora.validate_query_params({**ora.DEFAULT_QUERY_PARAMS, **(params or {})})
    sql = ora.build_main_query_sql(query_params)
    overrides = {"ban_subscriber": sql}
    rem = run_vts1_remediation(["ban_subscriber"], query_overrides=overrides)
    result = rem.get("results", [{}])[0] if rem.get("results") else {}
    return {
        "success": bool(rem.get("success")),
        "type": "data-loading-p-ban-reload",
        "message": result.get("message") or rem.get("message", "P_BAN reload finished."),
        "messages": rem.get("messages") or [],
        "clearDetails": result.get("clearDetails") or [],
        "params": query_params,
        "displaySql": sql,
        "exportDir": rem.get("exportDir"),
        "txtFile": result.get("txtFile"),
        "rowCount": result.get("rowCount"),
        "import": result.get("import"),
    }
