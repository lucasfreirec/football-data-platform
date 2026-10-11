"""Multiprocess-mode setup that must run before ``prometheus_client`` is imported.

prometheus_client decides between in-process and file-backed (multiprocess) values when
it is first imported, by reading ``PROMETHEUS_MULTIPROC_DIR``. Keep this module free of
that import so the worker launcher can call it first.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

MULTIPROC_ENV = "PROMETHEUS_MULTIPROC_DIR"


def multiprocess_enabled() -> bool:
    return bool(os.environ.get(MULTIPROC_ENV))


def enable_multiprocess() -> Path:
    """Point prometheus_client at an empty directory shared by a worker's pool processes."""
    configured = os.environ.get(MULTIPROC_ENV)
    directory = Path(configured) if configured else Path(tempfile.mkdtemp(prefix="football-prom-"))
    directory.mkdir(parents=True, exist_ok=True)
    # Files left by a previous run in the same container would be double counted.
    for entry in directory.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
    os.environ[MULTIPROC_ENV] = str(directory)
    return directory
