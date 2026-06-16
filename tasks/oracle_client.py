"""Oracle DB client — same connection as SQL Developer PET1_CUST (python-oracledb)."""

from __future__ import annotations

import os
from typing import Any


from tasks.env_loader import env_diagnostics, load_project_env

# Ensure .env is loaded when oracle_client is imported outside app.py (e.g. CLI script)
load_project_env(create_from_example=True)


def connection_config() -> dict[str, Any]:
    """Read ORACLE_* from environment (.env loaded by app.py)."""
    return {
        "user": (os.environ.get("ORACLE_USER") or "").strip(),
        "password_set": bool((os.environ.get("ORACLE_PASSWORD") or "").strip()),
        "host": (os.environ.get("ORACLE_HOST") or "").strip(),
        "port": (os.environ.get("ORACLE_PORT") or "1532").strip(),
        "service_name": (os.environ.get("ORACLE_SERVICE_NAME") or "").strip(),
    }


def config_checkpoints() -> list[dict[str, Any]]:
    """Validate .env / env vars before attempting connect."""
    cfg = connection_config()
    checks = [
        {
            "id": "oracle-user",
            "name": "ORACLE_USER set",
            "passed": bool(cfg["user"]),
            "detail": cfg["user"] or "missing",
        },
        {
            "id": "oracle-password",
            "name": "ORACLE_PASSWORD set",
            "passed": cfg["password_set"],
            "detail": "set" if cfg["password_set"] else "missing",
        },
        {
            "id": "oracle-host",
            "name": "ORACLE_HOST set",
            "passed": bool(cfg["host"]),
            "detail": cfg["host"] or "missing",
        },
        {
            "id": "oracle-port",
            "name": "ORACLE_PORT set",
            "passed": bool((os.environ.get("ORACLE_PORT") or "").strip()),
            "detail": (os.environ.get("ORACLE_PORT") or "").strip() or "missing (default 1532 used on connect)",
        },
        {
            "id": "oracle-service",
            "name": "ORACLE_SERVICE_NAME set",
            "passed": bool(cfg["service_name"]),
            "detail": cfg["service_name"] or "missing",
        },
    ]
    return checks


def connect():
    import oracledb

    cfg = connection_config()
    if not all([cfg["user"], cfg["password_set"], cfg["host"], cfg["service_name"]]):
        raise RuntimeError(
            "Oracle is not configured. Set ORACLE_USER, ORACLE_PASSWORD, ORACLE_HOST, "
            "ORACLE_PORT (default 1532), ORACLE_SERVICE_NAME in .env"
        )
    password = os.environ.get("ORACLE_PASSWORD", "")
    port = int(cfg["port"] or "1532")
    dsn = oracledb.makedsn(cfg["host"], port, service_name=cfg["service_name"])
    return oracledb.connect(user=cfg["user"], password=password, dsn=dsn)


def test_connection() -> dict[str, Any]:
    """
    Run connection checkpoints for portal backend / CLI script.
    Does not log or return the password.
    """
    messages: list[str] = []
    checkpoints: list[dict[str, Any]] = []

    messages.append("=== Oracle connection checkpoints ===")
    env_checks = config_checkpoints()
    checkpoints.extend(env_checks)
    for c in env_checks:
        status = "PASSED" if c["passed"] else "FAILED"
        messages.append(f"  {status} — {c['name']}: {c['detail']}")

    if not all(c["passed"] for c in env_checks):
        diag = env_diagnostics()
        env_hint = (
            f"Expected .env at: {diag.get('env_path')} "
            f"(exists: {diag.get('env_exists')}). "
        )
        if not diag.get("env_exists"):
            env_hint += (
                "Run: copy .env.example .env in the project folder, or restart python app.py "
                "(it auto-creates .env from .env.example on first start). "
            )
        else:
            env_hint += "File exists but ORACLE_* lines may be commented or empty — open .env in Notepad. "
        env_hint += ".env is not stored in GitHub."
        return {
            "success": False,
            "type": "oracle-connection-test",
            "connected": False,
            "checkpoints": checkpoints,
            "messages": messages,
            "message": f"Oracle .env configuration incomplete. {env_hint}",
            "envDiagnostics": diag,
        }

    cfg = connection_config()
    messages.append(f"  Target: {cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['service_name']}")

    try:
        import oracledb  # noqa: F401
        checkpoints.append({
            "id": "oracledb-import",
            "name": "python-oracledb installed",
            "passed": True,
            "detail": "import ok",
        })
        messages.append("  PASSED — python-oracledb installed")
    except ImportError as exc:
        checkpoints.append({
            "id": "oracledb-import",
            "name": "python-oracledb installed",
            "passed": False,
            "detail": str(exc),
        })
        messages.append(f"  FAILED — python-oracledb not installed: {exc}")
        messages.append("  Fix: python.exe -m pip install oracledb")
        return {
            "success": False,
            "type": "oracle-connection-test",
            "connected": False,
            "checkpoints": checkpoints,
            "messages": messages,
            "message": "Install oracledb: python.exe -m pip install oracledb",
        }

    try:
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM DUAL")
                dual = cur.fetchone()
            checkpoints.append({
                "id": "oracle-dual",
                "name": "SELECT 1 FROM DUAL",
                "passed": dual == (1,),
                "detail": str(dual),
            })
            messages.append(f"  PASSED — SELECT 1 FROM DUAL → {dual}")

            with conn.cursor() as cur:
                cur.execute("SELECT USER FROM DUAL")
                db_user = cur.fetchone()
            checkpoints.append({
                "id": "oracle-user-session",
                "name": "Session USER",
                "passed": bool(db_user and db_user[0]),
                "detail": db_user[0] if db_user else "unknown",
            })
            messages.append(f"  PASSED — connected as {db_user[0] if db_user else 'unknown'}")
        finally:
            conn.close()

        messages.append("=== Result: Oracle connection OK from this machine ===")
        return {
            "success": True,
            "type": "oracle-connection-test",
            "connected": True,
            "checkpoints": checkpoints,
            "messages": messages,
            "message": f"Connected to Oracle as {db_user[0] if db_user else cfg['user']}.",
            "config": {
                "user": cfg["user"],
                "host": cfg["host"],
                "port": cfg["port"],
                "service_name": cfg["service_name"],
            },
        }
    except Exception as exc:
        checkpoints.append({
            "id": "oracle-connect",
            "name": "Open connection",
            "passed": False,
            "detail": str(exc),
        })
        messages.append(f"  FAILED — connection error: {exc}")
        messages.append("  Tips: confirm SQL Developer PET1_CUST works on this VDI; check VPN/firewall to host:port.")
        return {
            "success": False,
            "type": "oracle-connection-test",
            "connected": False,
            "checkpoints": checkpoints,
            "messages": messages,
            "message": str(exc),
            "config": {
                "user": cfg["user"],
                "host": cfg["host"],
                "port": cfg["port"],
                "service_name": cfg["service_name"],
            },
        }
