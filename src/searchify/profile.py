"""The user profile: a small JSON file in the user's config directory.

It holds the answers of the first-run interview (``max_stage``, ``budget``) and
an optional ``region`` (set with ``searchify config --region`` or by an older
version, not read by any search yet). It is read by the command line and by the Python API
(`searchify.extract_report`). The HTTP service and the MCP server never read it:
they take their limits from the environment and the request only.

The file is written atomically (temporary file, then rename) and read once per
process. Anything in it that is not a known key with a valid value is ignored.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, Mapping, Optional

from .limits import BUDGETS, STAGES

FILE_NAME = "profile.json"
VERSION = 1
MAX_BYTES = 64_000

_cache: Optional[Dict] = None


def config_dir(
    platform: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    home: Optional[os.PathLike] = None,
) -> Path:
    """The user config directory for searchify.

    macOS ``~/Library/Application Support/searchify``; Linux ``$XDG_CONFIG_HOME/searchify``
    (an absolute path) or ``~/.config/searchify``; Windows ``%APPDATA%\\searchify``.
    ``SEARCHIFY_CONFIG_DIR`` overrides all of it (tests, containers)."""
    env = os.environ if env is None else env
    override = (env.get("SEARCHIFY_CONFIG_DIR") or "").strip()
    if override:
        return Path(override)
    platform = platform or sys.platform
    base = Path(home) if home else Path.home()
    if platform == "darwin":
        return base / "Library" / "Application Support" / "searchify"
    if platform.startswith("win"):
        return Path(env.get("APPDATA") or base / "AppData" / "Roaming") / "searchify"
    xdg = (env.get("XDG_CONFIG_HOME") or "").strip()
    return (Path(xdg) if xdg and os.path.isabs(xdg) else base / ".config") / "searchify"


def profile_path() -> Path:
    return config_dir() / FILE_NAME


def clean(data: object) -> Dict:
    """Only the known keys with valid values survive."""
    if not isinstance(data, dict):
        return {}
    out: Dict = {}
    if data.get("max_stage") in STAGES:
        out["max_stage"] = data["max_stage"]
    if data.get("budget") in BUDGETS:
        out["budget"] = data["budget"]
    region = data.get("region")
    if isinstance(region, str):
        from .search.regions import is_region  # noqa: PLC0415

        if is_region(region):
            out["region"] = region
    return out


def exists() -> bool:
    try:
        return profile_path().is_file()
    except OSError:
        return False


def load(force: bool = False) -> Dict:
    """The profile as a dict ({} if there is none or it is unreadable), read once per process."""
    global _cache
    if _cache is not None and not force:
        return dict(_cache)
    data: Dict = {}
    try:
        path = profile_path()
        if path.is_file() and path.stat().st_size <= MAX_BYTES:
            data = clean(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        data = {}
    _cache = data
    return dict(data)


def reset_cache() -> None:
    global _cache
    _cache = None


def save(values: Mapping[str, object]) -> Path:
    """Write the profile atomically: temp file in the same folder, fsync, rename.
    A crash leaves the old file or the new one, never half a file."""
    data = clean(values)
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"version": VERSION, **data}, indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".profile-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    global _cache
    _cache = data
    return path
