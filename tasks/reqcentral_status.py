"""Resolve ReqCentral / Jira-style browse page status for Task 4 Check status."""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

DEFAULT_BROWSE_BASE = "https://reqcentral.com/browse"


def _infer_status_from_html(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.find("title")
    title = (title_el.get_text(strip=True) if title_el else "") or ""

    candidates: list[str] = []
    for tag in soup.find_all(["span", "a", "div", "button"]):
        cls = " ".join(tag.get("class") or [])
        if not cls:
            continue
        if re.search(r"status|lozenge|jira-issue", cls, re.I):
            t = tag.get_text(strip=True)
            if t and len(t) < 80 and not t.startswith("http"):
                candidates.append(t)

    blob = " | ".join([title] + candidates[:25]).lower()

    for c in candidates:
        cl = c.strip().lower()
        if "in progress" in cl or cl == "in progress":
            return {"statusName": c.strip(), "completed": False, "inProgress": True}
        if cl in ("done", "closed", "resolved", "complete", "released"):
            return {"statusName": c.strip(), "completed": True, "inProgress": False}

    if re.search(r"\bin progress\b", blob):
        return {"statusName": "In Progress", "completed": False, "inProgress": True}
    if re.search(r"\b(done|closed|resolved)\b", blob):
        return {"statusName": "Done", "completed": True, "inProgress": False}

    return {
        "statusName": title[:120] or "Unknown",
        "completed": False,
        "inProgress": False,
        "uncertain": True,
    }


def check_reqcentral_issue(issue_key: str) -> dict[str, Any]:
    base = (os.environ.get("REQCENTRAL_BROWSE_BASE") or DEFAULT_BROWSE_BASE).strip().rstrip("/")
    browse_url = f"{base}/{quote(issue_key, safe='')}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    }
    try:
        r = requests.get(browse_url, headers=headers, timeout=30, allow_redirects=True)
    except requests.RequestException as e:
        return {
            "success": False,
            "source": "reqcentral",
            "browseUrl": browse_url,
            "completed": False,
            "inProgress": False,
            "statusName": None,
            "message": f"Could not load ReqCentral page: {e}",
        }

    if r.status_code != 200:
        return {
            "success": True,
            "source": "reqcentral",
            "browseUrl": browse_url,
            "completed": False,
            "inProgress": False,
            "statusName": None,
            "message": f"ReqCentral returned HTTP {r.status_code}. Open the link manually to verify.",
        }

    inferred = _infer_status_from_html(r.text or "")
    return {
        "success": True,
        "source": "reqcentral",
        "browseUrl": browse_url,
        "completed": inferred["completed"],
        "inProgress": inferred["inProgress"],
        "statusName": inferred.get("statusName"),
        "message": (
            None
            if inferred["completed"] or inferred["inProgress"]
            else (
                "Could not confidently read status from the page (layout or login wall). "
                "Open the ReqCentral link and confirm on the board."
                if inferred.get("uncertain")
                else None
            )
        ),
    }
