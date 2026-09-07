"""
Automation Portal — Python/Flask backend (same APIs as the former Node server).
Run:  pip install -r requirements.txt
      python app.py
"""

from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import subprocess
import threading
import time
import webbrowser
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

import requests
from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory, stream_with_context
from flask_cors import CORS

from tasks.reqcentral_status import check_reqcentral_issue
from tasks.pet_health import PET_HEALTH_URL, run_pet_health_check
from tasks.server_log_space import run_server_log_space_check
from tasks.env_loader import load_project_env

BASE_DIR = Path(__file__).resolve().parent
_ENV_BOOT = load_project_env(create_from_example=True)
if _ENV_BOOT.get("created_from_example"):
    print(
        f"[env] Created {_ENV_BOOT['env_path']} from .env.example — "
        "edit ORACLE_PASSWORD if login fails, then restart."
    )
elif not _ENV_BOOT.get("env_exists"):
    print(f"[env] WARNING: no .env at {_ENV_BOOT['env_path']}")
PUBLIC_DIR = BASE_DIR / "public"

app = Flask(__name__, static_folder=None)
CORS(app)


def _parse_port(value: str | None, fallback: int) -> int:
    if not value:
        return fallback
    try:
        n = int(str(value).strip())
        if 1 <= n <= 65535:
            return n
    except ValueError:
        pass
    return fallback


PORT = _parse_port(os.environ.get("PORT"), 3000)
HOST = (os.environ.get("HOST") or "0.0.0.0").strip() or "0.0.0.0"
MOBAXTERM_EXE = (os.environ.get("MOBAXTERM_PATH") or r"C:\Moba\MobaXterm_Personal_24.0.exe").strip()
TUX_BOUNCE_COMMAND = os.environ.get("TUX_BOUNCE_COMMAND", "get tuxedo bounce status")
HUP_OFFER_COUNT_URL = os.environ.get(
    "HUP_OFFER_COUNT_URL",
    "http://npe-services:8888/cgi-bin/fetchPetV21HupOfferCount",
).strip()
HUP_OFFER_CLEAN_URL = os.environ.get(
    "HUP_OFFER_CLEAN_URL",
    "http://npe-services:8888/cgi-bin/cleanPetV21HupOfferTable",
).strip()
HUP_OFFER_STATUS_URL = os.environ.get(
    "HUP_OFFER_STATUS_URL",
    "http://npe-services:8888/cgi-bin/checkPetV21HupOfferCleanUpJobStatus",
).strip()


LG_DISK_SERVERS = {
    "10.61.139.26": "VWRENBPTLRCAP01",
    "10.61.139.27": "VWRENBPTLRGAP02",
    "10.61.139.28": "VWRENBPTLRGAP03",
    "10.61.139.29": "VWRENBPTLRGAP04",
    "10.61.139.30": "VWRENBPTLRGAP05",
    "10.61.139.31": "VWRENBPTLRGAP06",
    "10.61.129.119": "VM0196PTLRG009",
}

TUX_BOUNCE_SERVERS = [
    {"login": "petole1a@VLRENBPTCOLAP01", "server": "VLRENBPTCOLAP01", "label": "TUXEDO"},
    {"login": "petole2a@VLRENBPTCOLAP02", "server": "VLRENBPTCOLAP02", "label": "TUXEDO"},
]


def exec_powershell(command: str, timeout: int = 25) -> str:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip() or "PowerShell failed"
        raise RuntimeError(err)
    return (result.stdout or "").strip()


def delay(seconds: float) -> None:
    time.sleep(seconds)


def fetch_npe_json(url: str, params: dict | None = None) -> dict:
    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("NPE service returned an unexpected non-object response.")
    return payload


def launch_mobaxterm() -> bool:
    ps_path = MOBAXTERM_EXE.replace("'", "''")
    ps_command = (
        f"if (Test-Path '{ps_path}') {{ Start-Process -FilePath '{ps_path}' | Out-Null; 'started' }} else {{ 'missing' }}"
    )
    try:
        result = exec_powershell(ps_command, timeout=25)
        return "started" in result
    except Exception:
        return False


def run_ssh_script(login: str, shell_script: str) -> str:
    escaped = shell_script.replace('"', '`"')
    ps_lines = [
        '$ErrorActionPreference = "Stop"',
        f'$login = "{login}"',
        f'$inputPayload = "{escaped}"',
        "$raw = $inputPayload | ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 $login 2>&1",
        '($raw -join "`n")',
    ]
    ps_command = "\n".join(ps_lines)
    return exec_powershell(ps_command, timeout=120)


def run_tux_bounce(login: str) -> str:
    input_payload = "\n\na\n" + TUX_BOUNCE_COMMAND + "\nexit\n"
    try:
        output = run_ssh_script(login, input_payload)
        normalized = (output or "").strip()
        if not normalized:
            return "FAILED TO CONNECT"
        if re.search(
            r"permission denied|timed out|could not resolve|connection refused|host key verification failed",
            normalized,
            re.I,
        ):
            return "FAILED TO CONNECT"
        if re.search(r"failed|error", normalized, re.I) and not re.search(r"0 failed", normalized, re.I):
            return "BOUNCE COMMAND FAILED"
        return "BOUNCE COMMAND EXECUTED"
    except Exception:
        return "FAILED TO CONNECT"


def get_remote_disk_data(ip: str, machine: str) -> dict:
    """
    Remote disk sizes via DCOM WMI (same stack as wmi.WMI(computer=ip) in PyQt).
    Avoids Get-CimInstance over WinRM, which fails for raw IPs unless TrustedHosts/HTTPS.
    """
    try:
        import wmi as wmi_mod  # type: ignore[import-untyped]
    except ImportError:
        return {
            "ip": ip,
            "machine": machine,
            "c_total": "Error",
            "c_free": "Error",
            "d_total": "Error",
            "d_free": "Error",
            "queryError": "Install Windows WMI deps: pip install wmi pywin32",
        }

    user = os.environ.get("LG_WMI_USER", "").strip()
    password = os.environ.get("LG_WMI_PASSWORD", "")
    kwargs: dict = {"computer": ip}
    if user:
        dom = os.environ.get("LG_WMI_DOMAIN", "").strip()
        if "\\" in user:
            kwargs["user"] = user
        elif dom:
            kwargs["user"] = f"{dom}\\{user}"
        else:
            kwargs["user"] = user
        kwargs["password"] = password

    try:
        conn = wmi_mod.WMI(**kwargs)
        c_total = c_free = d_total = d_free = None
        for disk in conn.Win32_LogicalDisk(DriveType=3):
            size = int(getattr(disk, "Size", None) or 0)
            free = int(getattr(disk, "FreeSpace", None) or 0)
            if size <= 0:
                continue
            total_gb = round(size / (1024**3), 2)
            free_gb = round(free / (1024**3), 2)
            dev = (getattr(disk, "DeviceID", None) or "").upper()
            if dev.startswith("C:"):
                c_total, c_free = total_gb, free_gb
            elif dev.startswith("D:"):
                d_total, d_free = total_gb, free_gb

        return {
            "ip": ip,
            "machine": machine,
            "c_total": c_total,
            "c_free": c_free,
            "d_total": d_total,
            "d_free": d_free,
        }
    except Exception as e:
        err = str(e).strip() or repr(e)
        return {
            "ip": ip,
            "machine": machine,
            "c_total": "Error",
            "c_free": "Error",
            "d_total": "Error",
            "d_free": "Error",
            "queryError": err[:800],
        }


def simulate_task_run(task_name: str, purpose: str) -> str:
    started = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    delay(0.9)
    finished = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    return "\n".join(
        [
            f"Task: {task_name}",
            f"Purpose: {purpose}",
            f"Started: {started}",
            f"Finished: {finished}",
            "Result: Simulated run completed (no external systems were called).",
            "Note: Replace this placeholder with your real data load or sanity job when you wire it in.",
        ]
    )


def send_portal_email(to_addr: str, cc_addr: str, subject: str, text: str) -> str:
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    from_addr = os.environ.get("SMTP_FROM") or user

    if not host or not user or not password or not from_addr:
        raise RuntimeError(
            "SMTP is not configured. Set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, and SMTP_FROM."
        )

    msg = EmailMessage()
    msg["Message-ID"] = make_msgid()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    if cc_addr:
        msg["Cc"] = cc_addr
    msg.set_content(text)

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as smtp:
            smtp.starttls(context=context)
            smtp.login(user, password)
            smtp.send_message(msg)

    return msg["Message-ID"]


@app.route("/api/email/crm-igw-bounce", methods=["POST"])
def email_crm():
    try:
        mid = send_portal_email(
            to_addr='"#RCI_V21 NPE Support" <RCIV21NPESupport@rci.rogers.com>',
            cc_addr='"QE Performance Engineering" <PerformanceTesting@rci.rogers.com>',
            subject="Rogers V21 | PET | Test Preparatory Activities",
            text="\n".join(
                [
                    "Hi @RCI_V21 NPE Support,",
                    "",
                    "Can you please reboot CRM IGW Domain 1 Servers.",
                    "",
                    "Thanks & Regards",
                    "Para Chaitanya",
                ]
            ),
        )
        return jsonify(
            {
                "success": True,
                "message": "CRM IGW Bounce email triggered successfully.",
                "messageId": mid,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "message": f"Failed to send CRM IGW Bounce email: {e}"}), 500


@app.route("/api/oracle/test-connection", methods=["POST", "GET"])
def oracle_test_connection():
    from tasks.oracle_client import test_connection

    try:
        result = test_connection()
        status = 200 if result.get("connected") else 503
        return jsonify(result), status
    except Exception as e:
        return (
            jsonify(
                {
                    "success": False,
                    "type": "oracle-connection-test",
                    "connected": False,
                    "message": str(e) or "Oracle test failed.",
                }
            ),
            500,
        )


@app.route("/api/tasks/data-loading/query-defaults", methods=["GET"])
def task_data_loading_query_defaults():
    from tasks import data_loading as dl

    return jsonify(dl.query_defaults())


@app.route("/api/tasks/data-loading/oracle-queries", methods=["GET"])
def task_data_loading_oracle_queries_get():
    from tasks import data_loading as dl

    return jsonify(dl.get_oracle_queries())


@app.route("/api/tasks/data-loading/oracle-queries", methods=["PUT"])
def task_data_loading_oracle_queries_put():
    from tasks import data_loading as dl

    body = request.get_json(silent=True) or {}
    queries = body.get("queries") if isinstance(body.get("queries"), dict) else body
    if not isinstance(queries, dict):
        return jsonify({"success": False, "message": "Expected JSON object with query keys."}), 400
    try:
        return jsonify(dl.save_oracle_queries(queries))
    except Exception as e:
        return jsonify({"success": False, "message": str(e) or "Failed to save queries."}), 500


@app.route("/api/tasks/data-loading/vts-limits", methods=["GET"])
def task_data_loading_vts_limits_get():
    from tasks import data_loading as dl

    return jsonify(dl.get_vts_limits())


@app.route("/api/tasks/data-loading/vts-limits", methods=["PUT"])
def task_data_loading_vts_limits_put():
    from tasks import data_loading as dl

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"success": False, "message": "Expected JSON object with vts1/vts2 limits."}), 400
    try:
        return jsonify(dl.save_vts_limits(body))
    except Exception as e:
        return jsonify({"success": False, "message": str(e) or "Failed to save VTS limits."}), 500


@app.route("/api/tasks/data-loading/vts-limits/reset", methods=["POST"])
def task_data_loading_vts_limits_reset():
    from tasks import data_loading as dl

    try:
        return jsonify(dl.reset_vts_limits())
    except Exception as e:
        return jsonify({"success": False, "message": str(e) or "Failed to reset VTS limits."}), 500


@app.route("/api/tasks/data-loading/run-flow", methods=["POST"])
def task_data_loading_run_flow():
    from tasks import data_loading as dl

    body = request.get_json(silent=True) or {}
    overrides = body.get("queries") if isinstance(body.get("queries"), dict) else None
    try:
        result = dl.run_task1_flow(query_overrides=overrides)
        status = 200 if result.get("success") else 400
        return jsonify(result), status
    except Exception as e:
        return (
            jsonify(
                {
                    "success": False,
                    "phase": "error",
                    "message": str(e) or "Task 1 flow failed.",
                }
            ),
            500,
        )


@app.route("/api/tasks/data-loading/p-ban-reload", methods=["POST"])
def task_data_loading_p_ban_reload():
    from tasks import data_loading as dl

    body = request.get_json(silent=True) or {}
    try:
        return jsonify(dl.run_p_ban_reload(body))
    except ValueError as e:
        return jsonify({"success": False, "message": str(e), "type": "data-loading-p-ban-reload"}), 400
    except Exception as e:
        return (
            jsonify(
                {
                    "success": False,
                    "type": "data-loading-p-ban-reload",
                    "message": str(e) or "P_BAN reload failed.",
                }
            ),
            500,
        )


@app.route("/api/tasks/data-loading/vts-precheck", methods=["POST"])
def task_data_loading_vts_precheck():
    from tasks import data_loading as dl

    try:
        return jsonify(dl.run_vts_precheck())
    except Exception as e:
        return (
            jsonify(
                {
                    "success": False,
                    "type": "data-loading-vts-precheck",
                    "message": str(e) or "VTS pre-check failed.",
                    "canProceed": False,
                }
            ),
            500,
        )


@app.route("/api/tasks/data-loading/vts-prep", methods=["POST"])
def task_data_loading_vts_prep():
    from tasks import data_loading as dl

    try:
        result = dl.run_vts_prep()
        if not result.get("success"):
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return (
            jsonify(
                {
                    "success": False,
                    "type": "data-loading-vts-prep",
                    "message": str(e) or "VTS prep failed.",
                    "prepCompleted": False,
                }
            ),
            500,
        )


@app.route("/api/tasks/hup-offer/count", methods=["GET"])
def task_hup_offer_count():
    try:
        return jsonify(fetch_npe_json(HUP_OFFER_COUNT_URL))
    except Exception as e:
        return jsonify(
            {
                "success": False,
                "message": f"Failed to fetch HUP_OFFER count: {str(e) or repr(e)}",
            }
        ), 502


@app.route("/api/tasks/hup-offer/clean", methods=["POST"])
def task_hup_offer_clean():
    try:
        return jsonify(fetch_npe_json(HUP_OFFER_CLEAN_URL))
    except Exception as e:
        return jsonify(
            {
                "success": False,
                "message": f"Failed to start HUP_OFFER cleanup: {str(e) or repr(e)}",
            }
        ), 502


@app.route("/api/tasks/hup-offer/status", methods=["GET"])
def task_hup_offer_status():
    pid = (request.args.get("pid") or "").strip()
    job_id = (request.args.get("jobId") or "").strip()
    if not pid or not job_id:
        return jsonify({"success": False, "message": "pid and jobId are required."}), 400
    try:
        return jsonify(fetch_npe_json(HUP_OFFER_STATUS_URL, {"pid": pid, "jobId": job_id}))
    except Exception as e:
        return jsonify(
            {
                "success": False,
                "message": f"Failed to check HUP_OFFER cleanup status: {str(e) or repr(e)}",
            }
        ), 502


@app.route("/api/tasks/lg-disk-check/run", methods=["POST"])
def task_lg_disk():
    results = []
    for ip, machine in LG_DISK_SERVERS.items():
        results.append(get_remote_disk_data(ip, machine))
    has_critical = any(
        (isinstance(r.get("c_free"), (int, float)) and r["c_free"] < 20)
        or (isinstance(r.get("d_free"), (int, float)) and r["d_free"] < 20)
        for r in results
    )
    return jsonify(
        {
            "success": True,
            "type": "lg-disk-table",
            "header": "LG Disk Space Check Results",
            "hasCriticalWarning": has_critical,
            "results": results,
        }
    )


@app.route("/api/open-share", methods=["POST"])
def open_share():
    data = request.get_json(silent=True) or {}
    machine_name = data.get("machineName")
    drive = (data.get("drive") or "").lower()
    if not machine_name or drive not in ("c", "d"):
        return jsonify({"success": False, "message": "Invalid machineName or drive."}), 400
    share_path = f"\\\\{machine_name}\\{drive}$"
    ps_command = f'Start-Process explorer.exe "{share_path}"'
    try:
        exec_powershell(ps_command, timeout=25)
        return jsonify({"success": True, "path": share_path})
    except Exception as e:
        return jsonify({"success": False, "message": f"Failed to open share: {e}"}), 500


@app.route("/api/tasks/server-log-check/run", methods=["POST"])
def task_server_log():
    accept = (request.headers.get("Accept") or "").lower()
    wants_stream = (
        "application/x-ndjson" in accept
        or "application/ndjson" in accept
        or request.args.get("stream") == "1"
    )

    if wants_stream:

        @stream_with_context
        def generate_ndjson():
            try:
                result = run_server_log_space_check()
                for ev in result.pop("_stream_events", []) or []:
                    yield json.dumps(ev) + "\n"
                yield json.dumps({"event": "done", "data": result}) + "\n"
            except Exception as err:
                yield json.dumps({"event": "error", "message": str(err) or "Server log space check failed."}) + "\n"

        return Response(
            generate_ndjson(),
            mimetype="application/x-ndjson; charset=utf-8",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    try:
        payload = run_server_log_space_check()
        payload.pop("_stream_events", None)
        return jsonify(payload)
    except Exception as e:
        return jsonify(
            {
                "success": False,
                "message": str(e) or "Server log space check failed.",
            }
        ), 500


@app.route("/api/tasks/server-bounce/run", methods=["POST"])
def task_server_bounce():
    moba_started = launch_mobaxterm()
    delay(1.2)
    servers = []
    for item in TUX_BOUNCE_SERVERS:
        status = run_tux_bounce(item["login"])
        servers.append(
            {
                "group": "TUX Servers",
                "server": item["server"],
                "label": item["label"],
                "status": status,
            }
        )
    return jsonify(
        {
            "success": True,
            "type": "server-bounce-list",
            "mobaStarted": moba_started,
            "mobaPath": MOBAXTERM_EXE,
            "servers": servers,
        }
    )


@app.route("/api/tasks/server-health-check/run", methods=["POST"])
def task_server_health():
    try:
        payload = run_pet_health_check()
        if not payload.get("success"):
            return jsonify(payload), 502
        return jsonify(payload)
    except Exception as e:
        return (
            jsonify(
                {
                    "success": False,
                    "message": str(e) or "Server health check failed.",
                    "petUrl": PET_HEALTH_URL,
                }
            ),
            500,
        )


@app.route("/api/tasks/trigger-sanity/run", methods=["POST"])
def task_trigger_sanity():
    output = simulate_task_run("Trigger Sanity", "Run post-actions sanity validation.")
    return jsonify({"success": True, "output": output})


@app.route("/api/jira/issue-status", methods=["POST"])
def jira_issue_status():
    data = request.get_json(silent=True) or {}
    issue_key = (data.get("issueKey") or data.get("jiraId") or "").strip().upper()
    if not re.match(r"^[A-Z][A-Z0-9_]*-\d+$", issue_key):
        return jsonify({"success": False, "message": "Enter a valid issue key like RNER-197725."}), 400

    payload = check_reqcentral_issue(issue_key)
    if payload.get("success") is False:
        return jsonify(payload), 502
    return jsonify(payload)


@app.route("/", methods=["GET"])
def index():
    return send_file(PUBLIC_DIR / "index.html")


@app.route("/<path:subpath>", methods=["GET"])
def public_files(subpath: str):
    if subpath.startswith("api"):
        abort(404)
    target = (PUBLIC_DIR / subpath).resolve()
    try:
        target.relative_to(PUBLIC_DIR.resolve())
    except ValueError:
        abort(403)
    if not target.is_file():
        abort(404)
    return send_from_directory(PUBLIC_DIR, subpath)


def _maybe_open_browser(url: str) -> None:
    ob = os.environ.get("OPEN_BROWSER", "")
    if ob == "1" or (ob != "0" and os.name == "nt"):
        try:
            webbrowser.open(url)
        except Exception:
            pass


def main() -> None:
    url = f"http://127.0.0.1:{PORT}/"
    print(f"Automation Portal listening on {HOST}:{PORT}")
    print(f"Open in your browser: {url}  (or http://localhost:{PORT}/)")

    if os.environ.get("OPEN_BROWSER", "") == "1" or (
        os.environ.get("OPEN_BROWSER", "") != "0" and os.name == "nt"
    ):
        threading.Timer(0.6, lambda: _maybe_open_browser(url)).start()

    try:
        app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)
    except OSError as e:
        if "address already in use" in str(e).lower() or getattr(e, "winerror", None) == 10048:
            print(
                f"Port {PORT} is already in use. Stop the other app or set PORT in .env to a free port (e.g. PORT=3001)."
            )
        else:
            print("Server failed to start:", e)
        raise


if __name__ == "__main__":
    main()
