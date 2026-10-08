"""Logging configuration."""

from __future__ import annotations

import logging
import sys

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"
_configured = False


def configure_logging(level: str = "INFO") -> None:
    """Install a single stderr handler on the root logger. Safe to call twice."""
    global _configured
    root = logging.getLogger()
    root.setLevel(level)

    if _configured:
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(handler)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
