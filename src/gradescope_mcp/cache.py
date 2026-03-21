"""Centralized runtime cache paths for ephemeral MCP and skill artifacts."""

from __future__ import annotations

import os
import pathlib

_CACHE_ROOT_ENV = "GRADESCOPE_MCP_CACHE_DIR"
DEFAULT_CACHE_ROOT = pathlib.Path("/tmp/gradescope-mcp")


def get_cache_root() -> pathlib.Path:
    """Return the runtime cache root and ensure it exists."""
    configured = os.environ.get(_CACHE_ROOT_ENV, "").strip()
    root = pathlib.Path(configured) if configured else DEFAULT_CACHE_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_artifact_path(name: str) -> pathlib.Path:
    """Build a file path under the shared runtime cache root."""
    return get_cache_root() / name


def get_artifact_dir(name: str) -> pathlib.Path:
    """Build a directory path under the shared runtime cache root."""
    path = get_cache_root() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_process_cache_env() -> pathlib.Path:
    """Pin common temp/cache env vars to the runtime cache root."""
    root = get_cache_root()
    for key in ("TMPDIR", "TEMP", "TMP"):
        os.environ[key] = str(root)

    xdg_cache_home = root / "xdg-cache"
    xdg_cache_home.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_CACHE_HOME"] = str(xdg_cache_home)
    return root
