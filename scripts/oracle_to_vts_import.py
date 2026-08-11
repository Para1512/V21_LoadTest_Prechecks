#!/usr/bin/env python3
"""
Oracle → VTS single-file import (CLI).

Examples:
  # Import existing export file
  python scripts/oracle_to_vts_import.py --file exports/vts1_remediate_xxx/P_BAN_P_SUBSCRIBER.txt

  # Run ban_subscriber Oracle query and import directly (no manual VTS upload)
  python scripts/oracle_to_vts_import.py --query ban_subscriber --clear
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tasks.env_loader import load_project_env

load_project_env(create_from_example=True)

from tasks import oracle_queries as oq
from tasks import vts_client as vts

VTS1_URL = os.environ.get("VTS1_URL", "http://vm0196ptlrg001:2244/").strip()
VTS2_URL = os.environ.get("VTS2_URL", "http://vm0196ptlrg001:2245/").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Oracle data into VTS 1 (single CSV file).")
    parser.add_argument("--file", help="Existing TXT/CSV export to import")
    parser.add_argument(
        "--query",
        choices=oq.QUERY_KEYS,
        help="Run saved Oracle query key and import rows directly",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear target VTS columns before import (use with --query)",
    )
    args = parser.parse_args()

    if not args.file and not args.query:
        parser.error("Provide --file or --query")

    if args.file:
        path = Path(args.file)
        if not path.is_file():
            print(f"File not found: {path}")
            return 1
        columns = None
        imp = vts.import_txt(VTS1_URL, path, columns=columns)
        _print_result(imp)
        return 0 if imp.get("verified") else 1

    if args.query == "p_sim":
        meta = oq.QUERY_META["p_sim"]
        sql = oq.load_queries().get("p_sim", "").strip()
        if not sql:
            print("No SQL for p_sim")
            return 1
        if args.clear:
            for url, label in (
                (os.environ.get("VTS1_URL", "http://vm0196ptlrg001:2244/"), "VTS 1"),
                (os.environ.get("VTS2_URL", "http://vm0196ptlrg001:2245/"), "VTS 2"),
            ):
                vts.clear_column(url.strip(), "P_SIM")
                print(f"Cleared P_SIM on {label}")
        from tasks import data_loading as dl

        proj = ROOT
        out_dir = proj / "exports" / "cli_p_sim_import"
        out_dir.mkdir(parents=True, exist_ok=True)
        entry = dl._remediate_p_sim_dual(sql, out_dir, proj, messages := [])
        for line in messages:
            print(line)
        return 0 if entry.get("success") else 1

    if args.query == "p_ban_c_vts2":
        sql = oq.load_queries().get("p_ban_c_vts2", "").strip()
        if not sql:
            print("No SQL for p_ban_c_vts2")
            return 1
        from tasks import data_loading as dl

        out_dir = ROOT / "exports" / "cli_p_ban_c_import"
        out_dir.mkdir(parents=True, exist_ok=True)
        entry = dl._remediate_p_ban_c_vts2(sql, out_dir, ROOT, messages := [])
        for line in messages:
            print(line)
        return 0 if entry.get("success") else 1

    if args.query == "p_new_imei_vts2":
        sql = oq.load_queries().get("p_new_imei_vts2", "").strip()
        if not sql:
            print("No SQL for p_new_imei_vts2")
            return 1
        from tasks import data_loading as dl

        out_dir = ROOT / "exports" / "cli_p_new_imei_import"
        out_dir.mkdir(parents=True, exist_ok=True)
        entry = dl._remediate_p_new_imei_vts2(sql, out_dir, ROOT, messages := [])
        for line in messages:
            print(line)
        return 0 if entry.get("success") else 1

    if args.query == "p_ban_h_vts2":
        queries = oq.load_queries()
        sql = queries.get("p_ban_h_vts2", "").strip()
        ban_sql = queries.get("ban_subscriber", "").strip()
        if not sql:
            print("No SQL for p_ban_h_vts2")
            return 1
        if not ban_sql:
            print("No SQL for ban_subscriber (required for Trigger 1+6)")
            return 1
        from tasks import data_loading as dl

        out_dir = ROOT / "exports" / "cli_p_ban_h_import"
        out_dir.mkdir(parents=True, exist_ok=True)
        entry = dl._remediate_p_ban_h_vts2(
            sql, ban_sql, out_dir, ROOT, messages := []
        )
        for line in messages:
            print(line)
        return 0 if entry.get("success") else 1

    meta = oq.QUERY_META[args.query]
    sql = oq.load_queries().get(args.query, "").strip()
    if not sql:
        print(f"No SQL for query key: {args.query}")
        return 1

    if args.clear:
        for col in meta["clearColumns"]:
            vts.clear_column(VTS1_URL, col)
            print(f"Cleared {col}")

    print(f"Running Oracle query ({args.query}) …")
    rows, _cols = oq.run_sql(sql)
    print(f"Rows: {len(rows)}")
    if not rows:
        print("No rows returned.")
        return 1

    headers, vts_rows = oq.rows_for_vts_import(args.query, rows)
    print(f"Importing into VTS 1 via POST /data/import_csv …")
    imp = vts.import_oracle_rows(
        VTS1_URL,
        headers,
        vts_rows,
        filename=meta["txtFile"],
    )
    _print_result(imp)
    return 0 if imp.get("verified") else 1


def _print_result(imp: dict) -> None:
    print(imp.get("message") or ("OK" if imp.get("verified") else "FAILED"))
    verify = imp.get("verification") or {}
    for row in verify.get("rows") or []:
        status = "OK" if row.get("passed") else "FAIL"
        print(
            f"  {status} {row.get('column')}: expected {row.get('expected')}, "
            f"actual {row.get('actual')}"
        )
    upload = imp.get("upload") or {}
    if upload.get("strategy"):
        print(f"  Strategy: {upload['strategy']}")
    if upload.get("jobId"):
        print(f"  Job id: {upload['jobId']}")
    if upload.get("responseBody") and not imp.get("verified"):
        print(f"  VTS response: {upload['responseBody'][:300]}")


if __name__ == "__main__":
    raise SystemExit(main())
