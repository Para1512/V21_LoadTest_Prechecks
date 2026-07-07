"""
Task 1 — Data loading: VTS pre-checks, validation, clear, and Oracle P_BAN reload.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tasks import oracle_extract as ora
from tasks import oracle_queries as oq
from tasks import vts_client as vts

VTS1_URL = os.environ.get("VTS1_URL", "http://vm0196ptlrg001:2244/").strip()
VTS2_URL = os.environ.get("VTS2_URL", "http://vm0196ptlrg001:2245/").strip()

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
    }


def get_oracle_queries() -> dict[str, Any]:
    return oq.queries_for_api()


def save_oracle_queries(updates: dict[str, str]) -> dict[str, Any]:
    merged = oq.save_queries(updates)
    return {"success": True, "queries": merged, "meta": oq.queries_for_api()["meta"]}


def vts1_remediation_issues(
    precheck: dict[str, Any] | None = None,
    prep: dict[str, Any] | None = None,
) -> list[str]:
    issues: list[str] = []
    if precheck:
        failed = precheck.get("vts1", {}).get("failedColumns") or []
        if "P_BAN" in failed:
            issues.append("ban_subscriber")
        if "P_SIM" in failed:
            issues.append("p_sim")
        if "P_IMEI_IP14128PUR" in failed:
            issues.append("p_imei_ip14128pur")
    if prep:
        for v in prep.get("validationDetails") or []:
            if not v.get("passed"):
                if v.get("id") == "vts1-ban-subscriber" and "ban_subscriber" not in issues:
                    issues.append("ban_subscriber")
                elif v.get("id") == "vts1-p_sim" and "p_sim" not in issues:
                    issues.append("p_sim")
                elif v.get("id") == "vts1-p_imei_ip14128pur" and "p_imei_ip14128pur" not in issues:
                    issues.append("p_imei_ip14128pur")
    return issues


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
    out_dir = proj / "exports" / f"vts1_remediate_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    messages.append("=== VTS 1 remediation (Oracle → TXT → VTS import) ===")

    for issue_key in issue_list:
        meta = oq.QUERY_META.get(issue_key)
        if not meta:
            continue
        sql = queries.get(issue_key, "").strip()
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

            imp = vts.import_txt(VTS1_URL, txt_path, columns=meta["clearColumns"])
            entry["import"] = imp
            if not imp.get("success"):
                entry["message"] = imp.get("message") or "VTS import failed verification."
                messages.append(f"  FAILED — {entry['message']}")
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
        "message": "VTS 1 remediation completed." if all_ok else "VTS 1 remediation had failures.",
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

    issues = vts1_remediation_issues(precheck=precheck)
    if issues:
        rem = run_vts1_remediation(issues, query_overrides=query_overrides)
        remediation_runs.append(rem)
        messages.extend(rem.get("messages") or [])
        if rem.get("success"):
            precheck = run_vts_precheck()
            messages.extend(precheck.get("messages") or [])

    if not precheck.get("canProceed"):
        return {
            "success": False,
            "phase": "insufficient",
            "precheck": precheck,
            "remediation": remediation_runs,
            "messages": messages,
            "pBanReloadNeeded": p_ban_reload_needed(precheck=precheck),
            "queryDefaults": query_defaults(),
        }

    prep = run_vts_prep()
    messages.extend(prep.get("messages") or [])

    if not prep.get("success"):
        issues = vts1_remediation_issues(prep=prep)
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
                "pBanReloadNeeded": p_ban_reload_needed(precheck=precheck, prep=prep),
                "queryDefaults": query_defaults(),
            }

    return {
        "success": True,
        "phase": "prep-done",
        "precheck": precheck,
        "prep": prep,
        "remediation": remediation_runs,
        "messages": messages,
        "queryDefaults": query_defaults(),
    }


def p_ban_reload_needed(
    precheck: dict[str, Any] | None = None,
    prep: dict[str, Any] | None = None,
) -> bool:
    if precheck:
        failed = precheck.get("vts1", {}).get("failedColumns") or []
        if "P_BAN" in failed or "P_SUBSCRIBER" in failed:
            return True
    if prep and not prep.get("validationPassed"):
        for v in prep.get("validationDetails") or []:
            if v.get("id") == "vts1-ban-subscriber" and not v.get("passed"):
                return True
    return False


def _prep_validation_columns() -> tuple[list[str], list[str]]:
    vts1_cols = list(vts.VTS1_ALIGN_COLUMNS) + ["P_SIM", "P_IMEI_IP14128PUR"]
    vts2_cols = list(vts.VTS2_ALIGN_GROUP_C) + list(vts.VTS2_ALIGN_GROUP_H) + ["P_NEW_IMEI"]
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
    messages.append("Checking VTS 1 …")
    vts1_rules = ", ".join(
        f"{col} >= {req}" for col, req in vts.VTS1_MIN_COUNTS.items()
    )
    messages.append(f"  VTS 1 pre-check rules: {vts1_rules}")
    vts1_payload = vts.fetch_get_page(VTS1_URL)
    vts1_counts = vts1_payload["countsPerColumn"]
    vts1_result = vts.check_min_counts(
        vts1_counts, vts.VTS1_MIN_COUNTS, label="VTS 1"
    )
    messages.extend(vts1_result["messages"])

    messages.append("Checking VTS 2 …")
    vts2_payload = vts.fetch_get_page(VTS2_URL)
    vts2_counts = vts2_payload["countsPerColumn"]
    vts2_result = vts.check_min_counts(
        vts2_counts, vts.VTS2_MIN_COUNTS, label="VTS 2"
    )
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
            "Cannot continue until all failed columns meet minimum counts. "
            "Plan data load/import for failed columns in a follow-up step."
        )

    return {
        "success": True,
        "type": "data-loading-vts-precheck",
        "vts1": vts1_result,
        "vts2": vts2_result,
        "passedColumns": all_passed,
        "failedColumns": all_failed,
        "canProceed": can_proceed,
        "pBanReloadNeeded": p_ban_reload_needed(precheck={
            "vts1": vts1_result,
        }),
        "queryDefaults": query_defaults(),
        "messages": messages,
    }


def run_vts_prep() -> dict[str, Any]:
    """Validate VTS column alignment, clear old test columns, mark prep complete."""
    messages: list[str] = []
    validation_details: list[dict[str, Any]] = []
    vts1_cols, vts2_cols = _prep_validation_columns()

    messages.append("=== Data details (countsPerColumn) ===")
    vts1_payload = vts.fetch_get_page(VTS1_URL)
    vts1_counts = vts1_payload["countsPerColumn"]
    vts1_snapshot = vts.counts_snapshot(vts1_counts, vts1_cols)
    for col, count in vts1_snapshot.items():
        messages.append(f"  VTS 1 — {col}: {count if count is not None else 'missing'}")

    vts2_payload = vts.fetch_get_page(VTS2_URL)
    vts2_counts = vts2_payload["countsPerColumn"]
    vts2_snapshot = vts.counts_snapshot(vts2_counts, vts2_cols)
    for col, count in vts2_snapshot.items():
        messages.append(f"  VTS 2 — {col}: {count if count is not None else 'missing'}")

    data_details = {
        "vts1": {
            "label": "VTS 1",
            "counts": vts1_snapshot,
        },
        "vts2": {
            "label": "VTS 2",
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

    for col, required in (("P_SIM", 8000), ("P_IMEI_IP14128PUR", 8000)):
        min_entry = _min_count_validation_entry(
            vts1_counts,
            col,
            required,
            id=f"vts1-{col.lower()}",
            name=f"VTS 1 — {col} >= {required}",
        )
        validation_details.append(min_entry)
        messages.append(min_entry["message"])

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

    p_new_check = vts.check_p_new_imei_gte_ban_h(vts2_counts)
    p_new_entry = {
        "id": "vts2-new-imei",
        "name": "VTS 2 — P_NEW_IMEI vs P_BAN_H",
        "passed": bool(p_new_check.get("passed")),
        "rule": "P_NEW_IMEI count must be >= P_BAN_H count",
        "rows": [
            {"column": "P_BAN_H", "count": p_new_check.get("pBanH")},
            {"column": "P_NEW_IMEI", "count": p_new_check.get("pNewImei")},
        ],
        "required": p_new_check.get("required"),
        "message": p_new_check.get("message", ""),
    }
    validation_details.append(p_new_entry)
    messages.append(p_new_entry["message"])

    all_passed = all(v["passed"] for v in validation_details)

    def _prep_fail(message: str) -> dict[str, Any]:
        fail_prep = {
            "success": False,
            "type": "data-loading-vts-prep",
            "message": message,
            "validationPassed": False,
            "dataDetails": data_details,
            "validationDetails": validation_details,
            "clearDetails": [],
            "messages": messages,
            "prepCompleted": False,
        }
        fail_prep["pBanReloadNeeded"] = p_ban_reload_needed(prep=fail_prep)
        fail_prep["queryDefaults"] = query_defaults()
        return fail_prep

    if not v1_entry["passed"]:
        return _prep_fail("P_BAN & P_SUBSCRIBER data is not aligned.")
    for v in validation_details:
        if v["id"] in ("vts1-p_sim", "vts1-p_imei_ip14128pur") and not v["passed"]:
            return _prep_fail(v["message"])
    if not v2c_entry["passed"] or not v2h_entry["passed"]:
        detail = []
        if not v2c_entry["passed"]:
            detail.append("current subscriber/device columns")
        if not v2h_entry["passed"]:
            detail.append("history columns")
        return _prep_fail(f"VTS 2 data is not aligned ({' and '.join(detail)}).")
    if not p_new_entry["passed"]:
        return _prep_fail(p_new_entry["message"])

    messages.append("=== Removing old test data ===")
    clear_details: list[dict[str, Any]] = []
    for col in vts.VTS2_CLEAR_COLUMNS:
        result = vts.clear_column(VTS2_URL, col)
        entry = {
            "vts": "VTS 2",
            "column": col,
            "success": True,
        }
        clear_details.append(entry)
        messages.append(f"  Cleared VTS 2 — {col}: Passed")
    for col in vts.VTS1_CLEAR_COLUMNS:
        result = vts.clear_column(VTS1_URL, col)
        entry = {
            "vts": "VTS 1",
            "column": col,
            "success": True,
        }
        clear_details.append(entry)
        messages.append(f"  Cleared VTS 1 — {col}: Passed")

    messages.append("Old test data has been removed.")
    return {
        "success": True,
        "type": "data-loading-vts-prep",
        "validationPassed": all_passed,
        "dataDetails": data_details,
        "validationDetails": validation_details,
        "clearDetails": clear_details,
        "messages": messages,
        "prepCompleted": True,
        "message": "Old test data has been removed.",
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
