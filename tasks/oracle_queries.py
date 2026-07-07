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
    "SELECT /*+ PARALLEL*/ REPLACE(d.ESN_IMEI, ' ', ') AS IMEI, "
    "REPLACE(SUBSTR(d.ESN_IMEI, 1, 8), ' ', ') AS sstring "
    "FROM dealer_equipment d, item_definition i "
    "WHERE d.item_id = i.item_id "
    "AND D.ESN_IMEI NOT IN ("
    "SELECT s.serial_number FROM serial_item_inv s WHERE S.SERIAL_NUMBER = D.ESN_IMEI) "
    "AND D.ESN_IMEI NOT IN (SELECT UNIT_ESN FROM physical_device) "
    "AND d.esn_imei NOT IN ("
    "SELECT phd.unit_esn FROM physical_device phd WHERE phd.unit_esn = D.ESN_IMEI) "
    "AND d.item_id = 'IP14128MDN' "
    "AND NVL(d.franchise_type, 'R') <> 'F' AND rownum <= 10000"
)

QUERY_KEYS = ("ban_subscriber", "p_sim", "p_imei_ip14128pur")

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
        "label": "VTS 1 — P_IMEI_IP14128PUR",
        "clearColumns": ["P_IMEI_IP14128PUR"],
        "txtFile": "P_IMEI_IP14128PUR.txt",
        "headers": ["P_IMEI_IP14128PUR"],
    },
}


def default_queries() -> dict[str, str]:
    return {
        "ban_subscriber": ora.build_main_query_sql(ora.DEFAULT_QUERY_PARAMS),
        "p_sim": DEFAULT_P_SIM_SQL,
        "p_imei_ip14128pur": DEFAULT_P_IMEI_SQL,
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
    """First 10k rows → VTS 2; next 40k → VTS 1."""
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
        else:
            vals = [str(row[i] if i < len(row) else "") for i in range(len(headers))]
            lines.append(",".join(vals))
        count += 1
    text = "\r\n".join(lines) + "\r\n"
    path.write_bytes(text.encode("utf-8"))
    return count
