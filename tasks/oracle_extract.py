"""Oracle main extract for P_BAN / P_SUBSCRIBER reload (PET1_CUST)."""

from __future__ import annotations

import re
from typing import Any

from tasks.oracle_client import connect

DEFAULT_QUERY_PARAMS: dict[str, Any] = {
    "skuLike": "IP14128MDN",
    "memoHavingCount": 12,
    "soc": "WBRM422BG",
    "sysCreationDate": "2026-01-20 00:00:01",
}


def validate_query_params(p: dict[str, Any]) -> dict[str, Any]:
    sku = str(p.get("skuLike") or DEFAULT_QUERY_PARAMS["skuLike"]).strip()
    if not re.match(r"^[A-Za-z0-9_-]{1,40}$", sku):
        raise ValueError("Invalid skuLike (letters, digits, hyphen, underscore only).")
    mc = int(p.get("memoHavingCount", DEFAULT_QUERY_PARAMS["memoHavingCount"]))
    if mc < 1 or mc > 999:
        raise ValueError("memoHavingCount must be between 1 and 999.")
    soc = str(p.get("soc") or DEFAULT_QUERY_PARAMS["soc"]).strip().upper()
    if not re.match(r"^[A-Z0-9_]{1,30}$", soc):
        raise ValueError("Invalid SOC (alphanumeric and underscore only).")
    dt = str(p.get("sysCreationDate") or DEFAULT_QUERY_PARAMS["sysCreationDate"]).strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", dt):
        raise ValueError("sysCreationDate must be YYYY-MM-DD HH24:MI:SS")
    return {"skuLike": sku, "memoHavingCount": mc, "soc": soc, "sysCreationDate": dt}


def build_main_query_sql(params: dict[str, Any]) -> str:
    p = validate_query_params(params)
    return (
        "SELECT a.ban || ',' || b.Subscriber_no\n"
        "FROM billing_account a, subscriber b, physical_device c, service_agreement d\n"
        "WHERE a.ban_status = 'O'\n"
        "  AND d.ban = a.customer_id\n"
        f"  AND d.SOC = '{p['soc']}'\n"
        "  AND a.account_type = 'I'\n"
        "  AND a.ban = c.customer_id\n"
        "  AND c.customer_id = b.customer_id\n"
        "  AND c.SKU LIKE 'IP%'\n"
        "  AND a.account_sub_type = 'R'\n"
        "  AND b.product_type = 'C'\n"
        "  AND b.sub_status = 'A'\n"
        "  AND b.sub_status_last_act = 'NAC'\n"
        f"  AND a.sys_creation_date >= TO_DATE('{p['sysCreationDate']}', 'YYYY-MM-DD HH24:MI:SS')\n"
        f"  AND a.ban IN (SELECT memo_ban FROM memo GROUP BY memo_ban HAVING COUNT(*) = {p['memoHavingCount']})\n"
        f"  AND c.sku LIKE '%{p['skuLike']}%'\n"
        "  AND a.customer_id = b.customer_id\n"
        "ORDER BY a.sys_creation_date DESC"
    )


def _main_query_sql_binds(params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    p = validate_query_params(params)
    sql = (
        "SELECT a.ban || ',' || b.Subscriber_no AS ban_sub\n"
        "FROM billing_account a, subscriber b, physical_device c, service_agreement d\n"
        "WHERE a.ban_status = 'O'\n"
        "  AND d.ban = a.customer_id\n"
        "  AND d.SOC = :soc\n"
        "  AND a.account_type = 'I'\n"
        "  AND a.ban = c.customer_id\n"
        "  AND c.customer_id = b.customer_id\n"
        "  AND c.SKU LIKE 'IP%'\n"
        "  AND a.account_sub_type = 'R'\n"
        "  AND b.product_type = 'C'\n"
        "  AND b.sub_status = 'A'\n"
        "  AND b.sub_status_last_act = 'NAC'\n"
        "  AND a.sys_creation_date >= TO_DATE(:dt, 'YYYY-MM-DD HH24:MI:SS')\n"
        "  AND a.ban IN (SELECT memo_ban FROM memo GROUP BY memo_ban HAVING COUNT(*) = :memo_cnt)\n"
        "  AND c.sku LIKE :sku_like\n"
        "  AND a.customer_id = b.customer_id\n"
        "ORDER BY a.sys_creation_date DESC"
    )
    binds = {
        "soc": p["soc"],
        "dt": p["sysCreationDate"],
        "memo_cnt": p["memoHavingCount"],
        "sku_like": f"%{p['skuLike']}%",
    }
    return sql, binds


def split_ban_sub(cell: str) -> tuple[str, str]:
    cell = (cell or "").strip()
    if "," not in cell:
        return cell, ""
    ban, sub = cell.split(",", 1)
    return ban.strip(), sub.strip()


def run_main_extract(params: dict[str, Any] | None = None) -> list[str]:
    sql, binds = _main_query_sql_binds({**DEFAULT_QUERY_PARAMS, **(params or {})})
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, binds)
            return [str(r[0]) for r in cur.fetchall()]
    finally:
        conn.close()


def write_ban_subscriber_txt(path, rows_raw: list[str]) -> int:
    """Write Notepad-ready file: header P_BAN,P_SUBSCRIBER then data rows."""
    from pathlib import Path

    out = Path(path)
    with out.open("w", encoding="utf-8", newline="\n") as f:
        f.write("P_BAN,P_SUBSCRIBER\n")
        count = 0
        for cell in rows_raw:
            ban, sub = split_ban_sub(str(cell))
            f.write(f"{ban},{sub}\n")
            count += 1
    return count
