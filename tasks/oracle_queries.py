"""Oracle remediation queries for Task 1 VTS1 data loading."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tasks import oracle_extract as ora
from tasks.oracle_client import connect

PROJECT_ROOT = Path(__file__).resolve().parent.parent
QUERIES_PATH = PROJECT_ROOT / "config" / "oracle_queries.json"

DEFAULT_P_SIM_SQL = (
    "SELECT serial_number FROM serial_item_inv "
    "WHERE curr_possession = 'A' AND Item_ownership = 'C' "
    "AND Eq_type = 'G' AND serial_number LIKE '89302720%' "
    "AND item_id = 'SIM401' AND rownum <= 50000"
)

P_SIM_VTS2_ROW_LIMIT = 10000
P_SIM_VTS1_ROW_LIMIT = 40000
P_SIM_QUERY_ROW_LIMIT = 50000

DEFAULT_P_IMEI_SQL = (
    "SELECT /*+ PARALLEL*/ REPLACE(d.ESN_IMEI, ' ', ' ') AS IMEI, "
    "REPLACE(SUBSTR(d.ESN_IMEI, 1, 8), ' ', ' ') AS sstring "
    "FROM dealer_equipment d, item_definition i "
    "WHERE d.item_id = i.item_id "
    "AND D.ESN_IMEI NOT IN ("
    "SELECT s.serial_number FROM serial_item_inv s WHERE S.SERIAL_NUMBER = D.ESN_IMEI) "
    "AND D.ESN_IMEI NOT IN (SELECT UNIT_ESN FROM physical_device) "
    "AND d.esn_imei NOT IN ("
    "SELECT phd.unit_esn FROM physical_device phd WHERE phd.unit_esn = D.ESN_IMEI) "
    "AND d.item_id = 'IP14128STR' "
    "AND NVL(d.franchise_type, 'R') <> 'F' AND rownum <= 20000"
)

P_IMEI_QUERY_ROW_LIMIT = 20000

DEFAULT_P_BAN_C_VTS2_SQL = (
    "SELECT "
    "a.ban || ',' || REPLACE(SUBSTR(b.subscriber_no, 1, 6), ' ', '') || ',' || b.subscriber_no || ',' || "
    "(SELECT LISTAGG(c.unit_esn, ',') WITHIN GROUP (ORDER BY c.unit_esn) "
    "FROM physical_device c WHERE c.customer_id = a.customer_id) || ',' || "
    "(SELECT LISTAGG(c.esn_seq_no, ',') WITHIN GROUP (ORDER BY c.esn_seq_no) "
    "FROM physical_device c WHERE c.customer_id = a.customer_id) || ',' || "
    "(SELECT LISTAGG(c.imsi, ',') WITHIN GROUP (ORDER BY c.imsi) "
    "FROM physical_device c WHERE c.customer_id = a.customer_id) "
    "FROM billing_account a "
    "JOIN subscriber b ON a.customer_id = b.customer_id "
    "WHERE a.ban_status = 'N' "
    "AND a.account_type = 'I' "
    "AND a.account_sub_type = 'R' "
    "AND b.product_type = 'C' "
    "AND b.sub_status = 'C' "
    "AND b.sub_status_last_act = 'CAN' "
    "AND a.sys_update_date >= TO_DATE('2026-07-09 00:00:01', 'YYYY-MM-DD HH24:MI:SS') "
    "AND EXISTS ("
    "SELECT 1 FROM physical_device c "
    "WHERE c.customer_id = a.customer_id AND c.sku LIKE '%IP14128STR%') "
    "AND (SELECT COUNT(DISTINCT c.unit_esn) FROM physical_device c "
    "WHERE c.customer_id = a.customer_id) = 2 "
    "ORDER BY a.sys_update_date ASC"
)

P_BAN_C_VTS2_COLUMNS = [
    "P_BAN_C",
    "P_NPA_NXX_C",
    "P_SUBSCRIBER_C",
    "P_IMEI_C",
    "P_SIM_C",
    "P_ESN_0",
    "P_ESN_1",
    "P_IMSI_C",
]

DEFAULT_P_NEW_IMEI_VTS2_SQL = (
    "SELECT /*+ PARALLEL*/ REPLACE(d.ESN_IMEI, ' ', '') AS IMEI "
    "FROM dealer_equipment d, item_definition i "
    "WHERE d.item_id = i.item_id "
    "AND D.ESN_IMEI NOT IN ("
    "SELECT s.serial_number FROM serial_item_inv s WHERE S.SERIAL_NUMBER = D.ESN_IMEI) "
    "AND D.ESN_IMEI NOT IN (SELECT UNIT_ESN FROM physical_device) "
    "AND d.esn_imei NOT IN ("
    "SELECT phd.unit_esn FROM physical_device phd WHERE phd.unit_esn = D.ESN_IMEI) "
    "AND d.item_id = 'IP14256MDN' "
    "AND NVL(d.franchise_type, 'R') <> 'F' AND rownum <= 4000"
)

P_NEW_IMEI_VTS2_ROW_LIMIT = 4000

QUERY_KEYS = (
    "ban_subscriber",
    "p_sim",
    "p_imei_ip14128pur",
    "p_ban_c_vts2",
    "p_new_imei_vts2",
)

QUERY_META: dict[str, dict[str, Any]] = {
    "ban_subscriber": {
        "id": "ban_subscriber",
        "label": "VTS 1 — P_BAN & P_SUBSCRIBER",
        "clearColumns": ["P_BAN", "P_SUBSCRIBER"],
        "txtFile": "P_BAN_P_SUBSCRIBER.txt",
        "headers": ["P_BAN", "P_SUBSCRIBER"],
    },
    "p_sim": {
        "id": "p_sim",
        "label": "VTS 1 & VTS 2 — P_SIM",
        "clearColumns": ["P_SIM"],
        "txtFile": "P_SIM.txt",
        "headers": ["P_SIM"],
        "dualVts": True,
        "vts2TxtFile": "P_SIM_VTS2.txt",
        "vts1TxtFile": "P_SIM_VTS1.txt",
        "vts2RowLimit": P_SIM_VTS2_ROW_LIMIT,
        "vts1RowLimit": P_SIM_VTS1_ROW_LIMIT,
    },
    "p_imei_ip14128pur": {
        "id": "p_imei_ip14128pur",
        "label": "VTS 1 — P_IMEI_IP14128PUR (Validation 3)",
        "clearColumns": ["P_IMEI_IP14128PUR"],
        "txtFile": "P_IMEI_IP14128PUR.txt",
        "headers": ["P_IMEI_IP14128PUR"],
        "queryRowLimit": P_IMEI_QUERY_ROW_LIMIT,
    },
    "p_ban_c_vts2": {
        "id": "p_ban_c_vts2",
        "label": "VTS 2 — P_BAN_C & current columns (Validation 4)",
        "clearColumns": P_BAN_C_VTS2_COLUMNS,
        "txtFile": "P_BAN_C_VTS2.txt",
        "headers": P_BAN_C_VTS2_COLUMNS,
        "vts": "VTS 2",
    },
    "p_new_imei_vts2": {
        "id": "p_new_imei_vts2",
        "label": "VTS 2 — P_NEW_IMEI (Validation 5)",
        "clearColumns": ["P_NEW_IMEI"],
        "txtFile": "P_NEW_IMEI.txt",
        "headers": ["P_NEW_IMEI"],
        "vts": "VTS 2",
        "queryRowLimit": P_NEW_IMEI_VTS2_ROW_LIMIT,
    },
}


def default_queries() -> dict[str, str]:
    return {
        "ban_subscriber": ora.build_main_query_sql(ora.DEFAULT_QUERY_PARAMS),
        "p_sim": DEFAULT_P_SIM_SQL,
        "p_imei_ip14128pur": DEFAULT_P_IMEI_SQL,
        "p_ban_c_vts2": DEFAULT_P_BAN_C_VTS2_SQL,
        "p_new_imei_vts2": DEFAULT_P_NEW_IMEI_VTS2_SQL,
    }


def _read_saved() -> dict[str, str]:
    if not QUERIES_PATH.is_file():
        return {}
    try:
        data = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for key in QUERY_KEYS:
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    return out


def load_queries() -> dict[str, str]:
    return {**default_queries(), **_read_saved()}


def save_queries(updates: dict[str, str]) -> dict[str, str]:
    saved = _read_saved()
    for key in QUERY_KEYS:
        if key in updates and isinstance(updates[key], str):
            sql = updates[key].strip()
            if sql:
                saved[key] = sql
            elif key in saved:
                del saved[key]
    QUERIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUERIES_PATH.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    return load_queries()


def queries_for_api() -> dict[str, Any]:
    merged = load_queries()
    return {
        "queries": merged,
        "defaults": default_queries(),
        "meta": [
            {
                "id": key,
                "label": QUERY_META[key]["label"],
                "txtFile": QUERY_META[key]["txtFile"],
                "headers": QUERY_META[key]["headers"],
            }
            for key in QUERY_KEYS
        ],
    }


def run_sql(sql: str) -> tuple[list[tuple[Any, ...]], list[str]]:
    text = (sql or "").strip().rstrip(";")
    if not text:
        raise ValueError("SQL query is empty.")
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(text)
            if not cur.description:
                return [], []
            columns = [str(d[0]) for d in cur.description]
            rows = [tuple(r) for r in cur.fetchall()]
            return rows, columns
    finally:
        conn.close()


def split_p_sim_rows(
    rows: list[tuple[Any, ...]],
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """
    Validation 2 split (non-overlapping):
    - rows[0:10000]   → VTS 2 (P_SIM header file)
    - rows[10000:50000] → VTS 1 (P_SIM header file, top 10k removed)
    """
    vts2 = rows[:P_SIM_VTS2_ROW_LIMIT]
    vts1 = rows[P_SIM_VTS2_ROW_LIMIT : P_SIM_VTS2_ROW_LIMIT + P_SIM_VTS1_ROW_LIMIT]
    return vts2, vts1


def write_p_sim_column_txt(path: Path, rows: list[tuple[Any, ...]]) -> int:
    """Write single-column P_SIM file (header + CRLF rows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["P_SIM"]
    for row in rows:
        lines.append(str(row[0] if row else "").strip())
    text = "\r\n".join(lines) + "\r\n"
    path.write_bytes(text.encode("utf-8"))
    return max(0, len(lines) - 1)


def parse_p_ban_c_query_row(row: tuple[Any, ...]) -> tuple[str, ...] | None:
    """
    Parse Oracle row into 8 VTS2 current columns.
    Query returns one concatenated CSV row (ban,npa,subscriber,2x unit_esn,2x esn_seq,imsi).
    """
    if len(row) >= len(P_BAN_C_VTS2_COLUMNS):
        return tuple(str(row[i] or "").strip() for i in range(len(P_BAN_C_VTS2_COLUMNS)))
    if len(row) >= 1:
        cell = str(row[0] or "").strip()
        parts = [p.strip() for p in cell.split(",")]
        if len(parts) >= len(P_BAN_C_VTS2_COLUMNS):
            return tuple(parts[: len(P_BAN_C_VTS2_COLUMNS)])
    return None


def rows_for_vts_import(
    issue_key: str,
    rows: list[tuple[Any, ...]],
) -> tuple[list[str], list[tuple[str, ...]]]:
    """Convert Oracle rows to (headers, data rows) for single-file VTS import."""
    meta = QUERY_META[issue_key]
    headers = list(meta["headers"])
    out: list[tuple[str, ...]] = []
    for row in rows:
        if issue_key == "ban_subscriber":
            if len(row) >= 2:
                out.append((str(row[0] or "").strip(), str(row[1] or "").strip()))
            else:
                ban, sub = ora.split_ban_sub(str(row[0] if row else ""))
                out.append((ban, sub))
        elif issue_key == "p_sim":
            out.append((str(row[0] or "").strip(),))
        elif issue_key == "p_imei_ip14128pur":
            out.append((str(row[0] or "").strip(),))
        elif issue_key == "p_ban_c_vts2":
            parsed = parse_p_ban_c_query_row(row)
            if parsed:
                out.append(parsed)
        elif issue_key == "p_new_imei_vts2":
            out.append((str(row[0] or "").strip(),))
        else:
            out.append(tuple(str(row[i] if i < len(row) else "") for i in range(len(headers))))
    return headers, out


def write_remediation_txt(
    path: Path,
    issue_key: str,
    rows: list[tuple[Any, ...]],
    columns: list[str],
) -> int:
    meta = QUERY_META[issue_key]
    headers = meta["headers"]
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    lines = [",".join(headers)]
    for row in rows:
        if issue_key == "ban_subscriber":
            if len(row) >= 2:
                ban, sub = str(row[0] or "").strip(), str(row[1] or "").strip()
            else:
                ban, sub = ora.split_ban_sub(str(row[0] if row else ""))
            lines.append(f"{ban},{sub}")
        elif issue_key == "p_sim":
            lines.append(f"{str(row[0] or '').strip()}")
        elif issue_key == "p_imei_ip14128pur":
            lines.append(f"{str(row[0] or '').strip()}")
        elif issue_key == "p_ban_c_vts2":
            parsed = parse_p_ban_c_query_row(row)
            if parsed:
                lines.append(",".join(parsed))
                count += 1
            continue
        elif issue_key == "p_new_imei_vts2":
            lines.append(f"{str(row[0] or '').strip()}")
        else:
            vals = [str(row[i] if i < len(row) else "") for i in range(len(headers))]
            lines.append(",".join(vals))
        count += 1
    text = "\r\n".join(lines) + "\r\n"
    path.write_bytes(text.encode("utf-8"))
    return count
