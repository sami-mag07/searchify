"""Host memory: which rung of the load ladder works for which website.

Every page climbs the same ladder: the static fetch, the shared browser, the browser
loading the page itself (browser-direct) and Camoufox (stealth). On many sites the same
rung fails every time: bot protection answers the static fetch with 403, a JavaScript
shell needs a browser. This file remembers, per host and rung, how often the rung
worked and failed and why, so the next call jumps over the rungs that never work there.

- Stored are counters per host and rung, the last reason code and timestamps, never a
  URL or page text. The list of hosts is still a browsing history, so the file is only
  readable by its owner and ``searchify hosts --clear`` deletes everything.
- A rung is skipped on a host after it failed there ``STREAK`` times in a row. Only
  failures that belong to the site count (`FAILURES`); timeouts, network errors and
  5xx answers change nothing, as they may be the caller's own network, and neither do
  missing pages, rate limits and wrong content types. One success resets the streak.
- Every ``REPROBE_EVERY``-th visit of a host, and the first visit ``REPROBE_DAYS`` after
  its last full climb, climbs the whole ladder again, so a site that drops its bot
  protection gets cheap again.
- It never lifts a limit. Only the rungs that the installation, the operator cap
  ``SEARCHIFY_MAX_STAGE`` and the request allow are considered. When every one of them
  is known to fail on a host, only the cheap static fetch runs until the next full climb.
- Hosts not seen for ``FORGET_DAYS`` days are deleted; at most ``MAX_HOSTS`` are kept.
- On by default. ``SEARCHIFY_HOST_MEMORY=0`` turns it off, ``SEARCHIFY_HOST_MEMORY_PATH``
  names the file. Any trouble with the file turns the memory off for that call: reading
  pages never fails because of it.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse

log = logging.getLogger("searchify")

RUNGS = ("static", "browser", "browser-direct", "stealth")  # ladder order
FAILURES = frozenset({"blocked", "challenge", "thin_content", "no_main_text", "failed"})
STREAK = 3  # failures in a row before a rung is skipped on that host
REPROBE_EVERY = 10  # every 10th visit climbs the whole ladder
REPROBE_DAYS = 7
FORGET_DAYS = 30
MAX_HOSTS = 5000
FILE_NAME = "hosts.sqlite3"
_DAY = 86400.0
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS host (host TEXT PRIMARY KEY, visits INTEGER NOT NULL DEFAULT 0,"
    " probed REAL, seen REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS rung (host TEXT NOT NULL, rung TEXT NOT NULL,"
    " ok INTEGER NOT NULL DEFAULT 0, fail INTEGER NOT NULL DEFAULT 0,"
    " streak INTEGER NOT NULL DEFAULT 0, reason TEXT, seen REAL NOT NULL,"
    " PRIMARY KEY (host, rung))",
)
_warned = False  # a broken file is reported once per process, not on every call


def enabled() -> bool:
    return os.environ.get("SEARCHIFY_HOST_MEMORY", "1").strip().lower() not in ("0", "false", "no", "off")


def cache_dir(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None,
              home: Optional[os.PathLike] = None) -> Path:
    """The user cache folder: macOS ``~/Library/Caches/searchify``; Linux
    ``$XDG_CACHE_HOME/searchify`` (an absolute path) or ``~/.cache/searchify``;
    Windows ``%LOCALAPPDATA%\\searchify\\Cache``."""
    env = os.environ if env is None else env
    platform = platform or sys.platform
    base = Path(home) if home else Path.home()
    if platform == "darwin":
        return base / "Library" / "Caches" / "searchify"
    if platform.startswith("win"):
        return Path(env.get("LOCALAPPDATA") or base / "AppData" / "Local") / "searchify" / "Cache"
    xdg = (env.get("XDG_CACHE_HOME") or "").strip()
    return (Path(xdg) if xdg and os.path.isabs(xdg) else base / ".cache") / "searchify"


def memory_path() -> Path:
    override = os.environ.get("SEARCHIFY_HOST_MEMORY_PATH", "").strip()
    return Path(override) if override else cache_dir() / FILE_NAME


def host_of(url: str) -> Optional[str]:
    """The host a URL belongs to, lower case and without ``www.``; None if it has none."""
    try:
        host = (urlparse(url).hostname or "").strip(".").lower()
    except ValueError:
        return None
    host = host.removeprefix("www.")
    return host if host and len(host) <= 253 else None


class Memory:
    """One memory file. Every method opens its own short connection, so threads and
    processes can share the file; a busy file waits up to two seconds."""

    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """One short transaction: committed on success, rolled back on error, closed."""
        con = sqlite3.connect(self.path, timeout=2.0)
        try:
            with con:
                yield con
        finally:
            con.close()

    @classmethod
    def open(cls, path: Optional[Path] = None) -> "Memory":
        """Create the file (owner only) and its tables if needed. Raises on trouble."""
        path = Path(path or memory_path())
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.exists():
            os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
        mem = cls(path)
        with mem._connect() as con:
            for statement in _SCHEMA:
                con.execute(statement)
        return mem

    # ---------------------------------------------------------------- deciding
    def plan(self, urls: Sequence[str], rungs: Sequence[str],
             now: Optional[float] = None) -> Dict[str, Set[str]]:
        """{url: rungs to skip} for one call, counting the visit of each URL's host.
        `rungs` are the rungs this call may use; only those can be skipped. The static
        rung always stays when every rung is known to fail. A reprobe visit (see the
        module text) skips nothing."""
        now = time.time() if now is None else now
        allowed = [r for r in RUNGS if r in rungs]
        out: Dict[str, Set[str]] = {}
        if len(allowed) < 2:
            return out
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")  # read and count the visit as one step
            for url in urls:
                host = host_of(url)
                if not host:
                    continue
                row = con.execute("SELECT visits, probed FROM host WHERE host = ?", (host,)).fetchone()
                if row is None:
                    continue  # nothing learned about this host yet: nothing to skip, nothing stored
                visits, probed = row[0] + 1, row[1]
                reprobe = probed is None or visits % REPROBE_EVERY == 0 or now - probed > REPROBE_DAYS * _DAY
                con.execute("UPDATE host SET visits = ?, probed = ?, seen = ? WHERE host = ?",
                            (visits, now if reprobe else probed, now, host))
                if reprobe:
                    continue
                bad = {r for r, in con.execute(
                    "SELECT rung FROM rung WHERE host = ? AND streak >= ?", (host, STREAK))}
                skip = bad & set(allowed)
                if skip == set(allowed):  # nothing works there: only the cheap fetch, no browser
                    skip.discard("static")
                if skip:
                    out[url] = skip
        return out

    # ---------------------------------------------------------------- learning
    def record(self, events: Iterable[Tuple[str, str, str]], now: Optional[float] = None) -> None:
        """Note what each tried rung did: (url, rung, "ok" or a reason code). Codes
        outside FAILURES (timeout, not_found, ...) say nothing about the rung."""
        now = time.time() if now is None else now
        rows = []
        for url, rung, outcome in events:
            host = host_of(url)
            if host and rung in RUNGS and (outcome == "ok" or outcome in FAILURES):
                rows.append((host, rung, outcome))
        if not rows:
            return
        with self._connect() as con:
            for host, rung, outcome in rows:
                ok = outcome == "ok"
                con.execute(
                    "INSERT INTO rung (host, rung, ok, fail, streak, reason, seen)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(host, rung) DO UPDATE SET"
                    " ok = ok + excluded.ok, fail = fail + excluded.fail,"
                    " streak = CASE WHEN excluded.ok = 1 THEN 0 ELSE streak + 1 END,"
                    " reason = excluded.reason, seen = excluded.seen",
                    (host, rung, int(ok), int(not ok), 0 if ok else 1, None if ok else outcome, now))
                con.execute(  # a host's first lessons come from a call that skipped nothing
                    "INSERT INTO host (host, visits, probed, seen) VALUES (?, 0, ?, ?)"
                    " ON CONFLICT(host) DO UPDATE SET seen = excluded.seen", (host, now, now))
            self._prune(con, now)

    @staticmethod
    def _prune(con: sqlite3.Connection, now: float) -> None:
        con.execute("DELETE FROM host WHERE seen < ?", (now - FORGET_DAYS * _DAY,))
        con.execute("DELETE FROM host WHERE host IN (SELECT host FROM host ORDER BY seen DESC"
                    " LIMIT -1 OFFSET ?)", (MAX_HOSTS,))
        con.execute("DELETE FROM rung WHERE host NOT IN (SELECT host FROM host)")

    # ---------------------------------------------------------------- showing
    def describe(self) -> List[Dict]:
        """Every remembered host, last seen first: visits, what each rung did and the
        rung a call starts at (the first one that is not known to fail)."""
        with self._connect() as con:
            hosts = con.execute("SELECT host, visits, seen FROM host ORDER BY seen DESC").fetchall()
            rungs = con.execute("SELECT host, rung, ok, fail, streak, reason FROM rung").fetchall()
        per: Dict[str, Dict[str, Dict]] = {}
        for host, rung, ok, fail, streak, reason in rungs:
            per.setdefault(host, {})[rung] = {"ok": ok, "fail": fail, "streak": streak, "reason": reason}
        out = []
        for host, visits, seen in hosts:
            known = per.get(host, {})
            if not known:
                continue
            order = [r for r in RUNGS if r in known]
            start = next((r for r in RUNGS if known.get(r, {}).get("streak", 0) < STREAK), RUNGS[0])
            out.append({"host": host, "visits": visits, "seen": seen, "starts_at": start,
                        "rungs": {r: known[r] for r in order}})
        return out

    def forget(self, host: Optional[str] = None) -> int:
        """Delete one host (or all with None); the number of hosts removed."""
        with self._connect() as con:
            if host is None:
                n = con.execute("SELECT COUNT(*) FROM host").fetchone()[0]
                con.execute("DELETE FROM host")
                con.execute("DELETE FROM rung")
                return n
            name = host_of(host if "//" in host else f"https://{host}") or host.lower()
            n = con.execute("DELETE FROM host WHERE host = ?", (name,)).rowcount
            con.execute("DELETE FROM rung WHERE host = ?", (name,))
            return n


def open_memory() -> Optional[Memory]:
    """The memory for one call, or None when it is off or its file is unusable."""
    global _warned
    if not enabled():
        return None
    try:
        return Memory.open()
    except (OSError, sqlite3.Error) as exc:
        if not _warned:
            _warned = True
            log.warning("host memory off: %s (%s)", memory_path(), type(exc).__name__)
        return None


def safely(memory: Optional[Memory], method: str, *args, default=None):
    """Call a Memory method; any trouble with the file gives `default` instead."""
    global _warned
    if memory is None:
        return default
    try:
        return getattr(memory, method)(*args)
    except (OSError, sqlite3.Error) as exc:
        if not _warned:
            _warned = True
            log.warning("host memory off: %s (%s)", memory.path, type(exc).__name__)
        return default


__all__ = ["FAILURES", "Memory", "RUNGS", "STREAK", "enabled", "host_of", "memory_path",
           "open_memory", "safely"]
# Stand: 10.11.2026
