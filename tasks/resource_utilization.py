"""Build the RU stats CSV from a Dynatrace dashboard PDF.

Only the servers in the RU stats sheet are included. CPU and memory values
come from the single-value tiles in the PDF.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

CSV_FILE_NAME = "RU_Stats.csv"

CSM_SERVERS = ("VLRENBPTCOLAP01", "VLRENBPTCOLAP02")
PRAPI_SERVERS = tuple(f"VLRENBPTTUXAP{index:02d}" for index in range(1, 11))
ESB_CL_SERVERS = (
    "Idcpetesbcl01",
    "Idcpetesbcl04",
    "Idcpetesbcl05",
    "Idcpetesbcl06",
    "Idcpetesbcl07",
    "Idcpetesbcl08",
    "Idcpetesbcl09",
    "Idcpetesbcl10",
)
ESB_DG_SERVERS = (
    "Idcpetesbdg01",
    "Idcpetesbdg03",
    "Idcpetesbdg05",
    "Idcpetesbdg06",
    "Idcpetesbdg07",
    "Idcpetesbdg08",
    "Idcpetesbdg11",
    "Idcpetesbdg12",
    "Idcpetesbdg13",
    "Idcpetesbdg14",
    "Idcpetesbdg15",
    "Idcpetesbdg16",
    "Idcpetesbdg17",
    "Idcpetesbdg18",
    "Idcpetesbdg19",
    "Idcpetesbdg20",
)

_VALUE_RE = re.compile(r"^(?:(\d+(?:\.\d+)?)\s*%|(\d+(?:\.\d+)?)GiB)$", re.IGNORECASE)
_HOST_RE = re.compile(r"^(?:vlren|ldcpet|idcpet)[a-z0-9.-]+$", re.IGNORECASE)
_REPORT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,80}$")


class ResourceReportError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def normalize_report_name(value: str) -> str:
    name = " ".join(str(value or "").split())
    if not _REPORT_NAME_RE.match(name):
        raise ResourceReportError(
            "Enter a report name such as R1226_Reg_LoadTest. Use letters, numbers, spaces, _ or -."
        )
    return name


def release_prefix(report_name: str) -> str:
    return report_name.split("_", 1)[0].strip()


def host_key(raw: str) -> str:
    base = str(raw or "").split(".", 1)[0].strip().upper()
    # The stats sheet writes Idcpetesb*; the dashboard host is ldcpetesb*.
    if base.startswith("LDC"):
        base = "IDC" + base[3:]
    return base


def _classify_header(line: str) -> tuple[str, str, str] | None:
    if "(Blue)" in line or not line.upper().startswith("PET1 "):
        return None
    lower = line.lower()
    if lower.endswith("average") or lower.endswith("avg"):
        kind = "avg"
    elif lower.endswith("max"):
        kind = "max"
    else:
        return None
    if "csmol" in lower:
        group = "csm"
    elif "prapi" in lower:
        group = "prapi"
    elif "esb cl" in lower:
        group = "esb_cl"
    elif "esb dg" in lower:
        group = "esb_dg"
    else:
        return None
    if "memory" in lower:
        metric = "mem"
    elif "cpu" in lower:
        metric = "cpu"
    else:
        return None
    return group, metric, kind


def _format_value(line: str) -> str:
    match = _VALUE_RE.match(line.strip())
    if not match:
        return line.strip()
    if match.group(1) is not None:
        return f"{float(match.group(1)):.2f}%"
    number = match.group(2)
    if "." in number:
        number = number.rstrip("0").rstrip(".")
    return f"{number}GiB"


def _is_host(line: str) -> bool:
    return bool(_HOST_RE.match(line.strip()))


def parse_dashboard_text(text: str) -> dict[str, dict[str, str]]:
    stats: dict[str, dict[str, str]] = {}
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    index = 0
    while index < len(lines):
        classified = _classify_header(lines[index])
        if not classified:
            index += 1
            continue
        group, metric, kind = classified
        index += 1
        values: list[str] = []
        while index < len(lines) and _VALUE_RE.match(lines[index]):
            values.append(_format_value(lines[index]))
            index += 1
        hosts: list[str] = []
        while index < len(lines) and _is_host(lines[index]):
            hosts.append(lines[index])
            index += 1
        field = f"{metric}_{kind}"
        for host, value in zip(hosts, values):
            row = stats.setdefault(host_key(host), {})
            # A second "Avg" tile for the same metric is the max tile.
            target = field
            if target in row and kind == "avg" and f"{metric}_max" not in row:
                target = f"{metric}_max"
            row.setdefault(target, value)
    return stats


def extract_pdf_text(pdf_bytes: bytes) -> str:
    if not pdf_bytes:
        raise ResourceReportError("Choose a Dynatrace dashboard PDF.")
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ResourceReportError(
            "PDF support is not installed. On the portal machine run: python -m pip install -r requirements.txt",
            500,
        ) from exc
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        raise ResourceReportError("Could not read that file as a PDF.") from exc
    if getattr(reader, "is_encrypted", False):
        raise ResourceReportError("The PDF is encrypted. Export the dashboard without a password.")
    parts = [(page.extract_text() or "") for page in reader.pages]
    text = "\n".join(parts)
    if "PET1" not in text.upper():
        raise ResourceReportError(
            "This PDF does not look like the PET1 Dynatrace dashboard. Upload the dashboard export."
        )
    return text


def _section_rows(
    title: str,
    servers: tuple[str, ...],
    stats: dict[str, dict[str, str]],
    *,
    include_memory: bool,
    missing: list[str],
) -> list[list[str]]:
    if include_memory:
        rows = [
            [title],
            ["Server Name", "CPU Usage%", "", "Memory Usage"],
            ["", "Average", "Maximum", "Average", "Maximum"],
        ]
    else:
        rows = [
            [title],
            ["Server Name", "CPU Usage%"],
            ["", "Average", "Maximum"],
        ]
    for server in servers:
        found = stats.get(host_key(server), {})
        cpu_avg = found.get("cpu_avg", "")
        cpu_max = found.get("cpu_max", "")
        if include_memory:
            mem_avg = found.get("mem_avg", "")
            mem_max = found.get("mem_max", "")
            if not all((cpu_avg, cpu_max, mem_avg, mem_max)):
                missing.append(server)
            rows.append([server, cpu_avg, cpu_max, mem_avg, mem_max])
        else:
            if not cpu_avg or not cpu_max:
                missing.append(server)
            rows.append([server, cpu_avg, cpu_max])
    return rows


def build_resource_csv(report_name: str, pdf_bytes: bytes) -> dict[str, Any]:
    name = normalize_report_name(report_name)
    prefix = release_prefix(name)
    stats = parse_dashboard_text(extract_pdf_text(pdf_bytes))
    if not stats:
        raise ResourceReportError(
            "No CPU or memory values were found in the PDF. Upload the Dynatrace dashboard export."
        )
    missing: list[str] = []
    table: list[list[str]] = [[name]]
    table.extend(
        _section_rows(f"{prefix} CSM OL PET1", CSM_SERVERS, stats, include_memory=True, missing=missing)
    )
    table.extend(
        _section_rows(f"{prefix} PRAPI PET1", PRAPI_SERVERS, stats, include_memory=True, missing=missing)
    )
    table.extend(
        _section_rows(
            f"{prefix} PET1 RESP ESB CL Custer",
            ESB_CL_SERVERS,
            stats,
            include_memory=False,
            missing=missing,
        )
    )
    table.extend(
        _section_rows(
            f"{prefix} PET1 RESP ESB DG Custer",
            ESB_DG_SERVERS,
            stats,
            include_memory=False,
            missing=missing,
        )
    )
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(table)
    log = [
        f"Report name: {name}.",
        "Read the Dynatrace dashboard PDF.",
        "Kept only the CSM online, PRAPI, ESB CL, and ESB DG servers from the RU stats sheet.",
    ]
    if missing:
        log.append(
            "No value in the PDF for: " + ", ".join(missing) + "."
        )
    else:
        log.append("Every requested server was found in the PDF.")
    log.append(f"Built {CSV_FILE_NAME}.")
    return {
        "success": True,
        "fileName": CSV_FILE_NAME,
        "csv": buffer.getvalue(),
        "missing": missing,
        "serverCount": len(CSM_SERVERS) + len(PRAPI_SERVERS) + len(ESB_CL_SERVERS) + len(ESB_DG_SERVERS),
        "log": log,
    }
