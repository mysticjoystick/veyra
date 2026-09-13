"""Operational resilience helpers (Phase 10).

* ``backup_database`` — a WAL-safe copy of the SQLite file using
  ``sqlite3.Connection.backup`` so an in-use database is copied consistently
  even while the dashboard/stream is writing.
* ``configure_file_logging`` — route the ``veyra.*`` loggers to a rotating
  file under ``<data_dir>/logs`` so operators keep a durable audit trail that
  outlives a single shell session or uvicorn reload.

Nothing here touches a real exchange or network; it only manages local state.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from ..config import Settings


def backup_database(settings: Settings, dest: Optional[Path] = None) -> Path:
    """Copy the configured SQLite database to ``dest`` (or a timestamped file).

    Uses the sqlite3 ``backup`` API so the destination is a consistent snapshot
    even if a connection has an open write transaction (WAL mode). Creates the
    destination directory. Returns the destination path.
    """
    src = settings.absolute_database_path
    if dest is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        dest = settings.absolute_data_dir / "backup" / f"veyra-{stamp}.db"
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        raise FileNotFoundError(f"database not found at {src}")
    src_con = sqlite3.connect(str(src))
    dest_con = sqlite3.connect(str(dest))
    try:
        with dest_con:
            src_con.backup(dest_con)
    finally:
        dest_con.close()
        src_con.close()
    return dest


def configure_file_logging(settings: Settings, *, level: int = logging.INFO) -> Path:
    """Send the ``veyra`` logger tree to a rotating file; returns the log path.

    Keeps console streaming (a base Handler already attached by CLI t the
    root logger) and adds a file handler, so output is both human-visible and
    durable. Secrets are never logged by the modules that know them; this just
    persists whatever the application already emits.
    """
    logs_dir = settings.absolute_data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "veyra.log"
    handler = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=5,
                                  encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger("veyra")
    root.addHandler(handler)
    root.setLevel(min(root.getEffectiveLevel(), level))
    return path