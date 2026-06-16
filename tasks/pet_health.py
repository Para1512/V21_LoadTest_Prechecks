"""Fetch and parse PET1-V21 CGI HTML for TUX OLN / TUX API server status."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup

NPE_BASE = (os.environ.get("NPE_SERVICES_CGI_BASE") or "http://npe-services:8888/cgi-bin").rstrip("/")
PET_PATH = (os.environ.get("PET1_V21_HEALTH_CGI") or "PET1-V21").lstrip("/")
PET_HEALTH_URL = (os.environ.get("PET1_V21_HEALTH_URL") or "").strip() or f"{NPE_BASE}/{PET_PATH}"
REQUEST_TIMEOUT_MS = int(os.environ.get("PET_HEALTH_REQUEST_TIMEOUT_MS") or "60000")


def normalize_status(raw: str) -> str:
    s = (raw or "").strip().lower()
    if not s:
        return "unknown"
    if re.match(r"^(up|online|running|ok|active|started|good|live)$", s):
        return "up"
    if re.match(r"^(down|offline|stopped|dead|inactive|fail|failed|error|bad)$", s):
        return "down"
    return "unknown"


def infer_group_from_host(server: str) -> str:
    u = (server or "").upper()
    if "TUXAP" in u:
        return "PRAPI"
    if "COLAP" in u:
        return "TUX"
    return "TUX"


def slice_after(html: str, start_re: re.Pattern[str], end_re: re.Pattern[str] | None) -> str:
    m = start_re.search(html)
    if not m:
        return ""
    tail = html[m.end() :]
    if end_re is None:
        return tail
    m2 = end_re.search(tail)
    return tail[: m2.start()] if m2 else tail


def _row_label(group: str) -> str:
    return "TUX OLN" if group == "TUX" else "TUX API"


def parse_tables_in_fragment(fragment: str, fixed_group: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(fragment, "html.parser")
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        cells = [c for c in cells if c]
        if len(cells) < 2:
            continue
        server, status_raw = cells[0], cells[-1]
        if re.match(r"^(server|host|machine|name)$", server, re.I) and re.match(r"^status$", status_raw, re.I):
            continue
        if re.match(r"^status$", server, re.I):
            continue
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]+$", server) or len(server) < 4:
            continue
        key = f"{fixed_group}:{server}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "group": fixed_group,
                "server": server,
                "label": _row_label(fixed_group),
                "status": status_raw,
                "normalized": normalize_status(status_raw),
            }
        )
    return rows


def parse_all_tables_infer(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        cells = [c for c in cells if c]
        if len(cells) < 2:
            continue
        server, status_raw = cells[0], cells[-1]
        if re.match(r"^(server|host|machine|name)$", server, re.I):
            continue
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]+$", server) or len(server) < 4:
            continue
        group = infer_group_from_host(server)
        key = f"{group}:{server}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "group": group,
                "server": server,
                "label": _row_label(group),
                "status": status_raw,
                "normalized": normalize_status(status_raw),
            }
        )
    return rows


def parse_loose_plain(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup
    compact = re.sub(r"\s+", " ", body.get_text(" ", strip=True))
    pat = re.compile(
        r"\b([A-Z]{2,}[A-Z0-9]*(?:PT)?(?:COLAP|TUXAP)[A-Z0-9]*)\b\s*(?:\|\s*|[-–:]\s*|\s+)\s*(UP|DOWN)\b",
        re.I,
    )
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for m in pat.finditer(compact):
        server = m.group(1).upper()
        status_raw = m.group(2)
        group = infer_group_from_host(server)
        key = f"{group}:{server}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "group": group,
                "server": server,
                "label": _row_label(group),
                "status": status_raw,
                "normalized": normalize_status(status_raw),
            }
        )
    return rows


def parse_pet_health_html(html: str) -> list[dict[str, Any]]:
    ol_html = slice_after(html, re.compile(r"TUX\s+OLN(?:\s+Servers?)?", re.I), re.compile(r"TUX\s+API(?:\s+Servers?)?", re.I))
    api_html = slice_after(html, re.compile(r"TUX\s+API(?:\s+Servers?)?", re.I), None)

    servers: list[dict[str, Any]] = []
    if ol_html.strip():
        servers.extend(parse_tables_in_fragment(ol_html, "TUX"))
    if api_html.strip():
        servers.extend(parse_tables_in_fragment(api_html, "PRAPI"))

    if not servers:
        servers = parse_all_tables_infer(html)
    if not servers:
        servers = parse_loose_plain(html)

    dedup: dict[str, dict[str, Any]] = {}
    for r in servers:
        dedup[f'{r["group"]}:{r["server"]}'] = r
    return list(dedup.values())


def build_warnings(servers: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for s in servers:
        if s.get("normalized") == "down":
            out.append(
                f'Warning: {s["server"]} ({s["label"]}) is DOWN (reported: {s["status"]}).'
            )
    return out


def run_pet_health_check() -> dict[str, Any]:
    timeout = max(REQUEST_TIMEOUT_MS / 1000.0, 5.0)
    try:
        r = requests.get(PET_HEALTH_URL, timeout=timeout)
    except requests.RequestException as e:
        return {"success": False, "message": str(e) or "PET1-V21 request failed.", "petUrl": PET_HEALTH_URL}

    if not r.ok:
        return {
            "success": False,
            "message": f"PET1-V21 request failed: HTTP {r.status_code}",
            "petUrl": PET_HEALTH_URL,
        }

    body = r.text or ""
    servers = parse_pet_health_html(body)
    if not servers:
        excerpt = re.sub(r"\s+", " ", body).strip()[:500]
        return {
            "success": False,
            "message": "Could not parse TUX OLN / TUX API server rows from PET1-V21 response.",
            "petUrl": PET_HEALTH_URL,
            "previewExcerpt": excerpt,
        }

    warnings = build_warnings(servers)
    has_unknown = any(s.get("normalized") == "unknown" for s in servers)
    all_up = all(s.get("normalized") == "up" for s in servers)

    return {
        "success": True,
        "type": "server-health-pet",
        "header": "Server Health Check (PET1-V21)",
        "petUrl": PET_HEALTH_URL,
        "servers": servers,
        "allUp": all_up,
        "warnings": warnings,
        "hasUnknown": has_unknown,
        "checkedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
