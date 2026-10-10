"""Resolution of a client-supplied source reference against the configured root.

A job request may name a sub-directory of the source root. It must stay inside
that root, otherwise an API client could point a worker at arbitrary files.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath


def validate_source_reference(reference: str) -> str:
    """Reject anything that is not a plain relative path; return it normalised."""
    if "\x00" in reference or "\\" in reference:
        raise ValueError("source_path contains invalid characters")
    path = PurePosixPath(reference)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("source_path must be a relative path inside the source directory")
    normalised = str(path)
    return "" if normalised == "." else normalised


def resolve_source_dir(root: Path, reference: str | None) -> Path:
    """Return the directory a job should read, guaranteed to lie inside ``root``."""
    base = root.resolve()
    if not reference:
        return base
    target = (base / validate_source_reference(reference)).resolve()
    if target != base and base not in target.parents:
        raise ValueError("source_path resolves outside the source directory")
    return target
