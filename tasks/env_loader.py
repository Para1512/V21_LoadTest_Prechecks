"""Load .env from project root (works regardless of current working directory)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"


def load_project_env(*, create_from_example: bool = True) -> dict[str, object]:
    """
    Load ORACLE_* and other vars from <project>/.env.
    If .env is missing and .env.example exists, copy example → .env once.
    """
    created = False
    if create_from_example and not ENV_PATH.is_file() and ENV_EXAMPLE_PATH.is_file():
        shutil.copy2(ENV_EXAMPLE_PATH, ENV_PATH)
        created = True

    loaded = load_dotenv(ENV_PATH, encoding="utf-8", override=False)

    return {
        "project_root": str(PROJECT_ROOT),
        "env_path": str(ENV_PATH),
        "env_exists": ENV_PATH.is_file(),
        "env_loaded": bool(loaded),
        "created_from_example": created,
        "cwd": os.getcwd(),
    }


def env_diagnostics() -> dict[str, object]:
    """Non-secret summary for Oracle test / troubleshooting."""
    info = load_project_env(create_from_example=False)
    cfg = {
        "user_set": bool((os.environ.get("ORACLE_USER") or "").strip()),
        "password_set": bool((os.environ.get("ORACLE_PASSWORD") or "").strip()),
        "host_set": bool((os.environ.get("ORACLE_HOST") or "").strip()),
        "port_set": bool((os.environ.get("ORACLE_PORT") or "").strip()),
        "service_set": bool((os.environ.get("ORACLE_SERVICE_NAME") or "").strip()),
    }
    return {**info, **cfg}
