"""Letzte Lade-Stufe fuer den Reader: Camoufox (quelloffener getarnter Firefox).

Springt nur an, wenn normales Laden, Chrome-TLS (FETCH_FALLBACK) und das
Headless-Chromium (render.py) nichts Lesbares geliefert haben. Messung 15.10.2026
an den 33 Seiten, die am 14.10. auf allen Stufen blockiert blieben: Camoufox 20,
Chromium 9, davon 11 nur mit Camoufox (eval/private/camoufox-2026-10-15).

Standard aus (Mac-Lauf 16.10.: im Mix kaum mehr Seiten, p95 +3 s, RAM +0,9 GB). An mit
READER_STEALTH=1 und CAMOUFOX_PYTHON auf dem Python des eigenen Camoufox-venv
(Einrichtung und gepinnte Versionen: docs/camoufox.md).
Der Browser laeuft als eigener Prozess (camoufox_worker.py) mit hartem Zeitlimit,
alles Netz geht ueber den SSRF-Proxy (stealth_proxy.py). Hoechstens ein Aufruf
gleichzeitig.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import render as render_mod
from .stealth_proxy import SsrfProxy

_SEM = threading.Semaphore(1)
_WORKER = Path(__file__).with_name("camoufox_worker.py")
SETTLE_S = 3.0  # Bot-Pruefseiten loesen sich meist in 2 bis 3 s selbst
SEM_WAIT_S = 1.0
MAX_CHARS = 4_000_000  # wie READER_MAX_BYTES


def _python() -> str:
    return os.environ.get("CAMOUFOX_PYTHON", "").strip()


def enabled() -> bool:
    if os.environ.get("READER_STEALTH", "0").strip() in ("0", "false", "no", ""):
        return False
    py = _python()
    return bool(py) and os.path.isfile(py) and os.access(py, os.X_OK)


def try_stealth(url: str, timeout: float) -> Tuple[str, Optional[str]]:
    """(status, html) wie render.try_render: "off", "blocked", "busy" oder "tried"."""
    if timeout <= 0 or not enabled():
        return "off", None
    if not render_mod.url_allowed(url):
        return "blocked", None
    if not _SEM.acquire(timeout=min(SEM_WAIT_S, timeout)):
        return "busy", None
    try:
        return "tried", _run(url, timeout)
    finally:
        _SEM.release()


def _run(url: str, timeout: float) -> Optional[str]:
    with SsrfProxy() as proxy:
        job = json.dumps({"url": url, "timeout": max(1.0, timeout - 1.0), "settle": SETTLE_S,
                          "proxy_port": proxy.port, "max_chars": MAX_CHARS})
        try:
            proc = subprocess.Popen(
                [_python(), "-I", str(_WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, start_new_session=True, text=True,
            )
        except OSError:
            return None
        try:
            raw, _ = proc.communicate(job, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(proc)
            return None
        finally:
            _kill(proc)
    try:
        res = json.loads(raw or "{}")
    except ValueError:
        return None
    status = int(res.get("status") or 0)
    if res.get("error") or not res.get("html") or status >= 400:
        return None
    if not render_mod.url_allowed(res.get("url") or ""):  # Endstand nach Weiterleitungen
        return None
    return res["html"]


def _descendants(pid: int) -> List[int]:
    """Alle Nachfahren eines Prozesses (Firefox legt eine eigene Prozessgruppe an,
    killpg der Worker-Gruppe erreicht ihn darum nicht; Security-Review 15.10.)."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    children: Dict[int, List[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, todo = [], [pid]
    while todo:
        for child in children.get(todo.pop(), []):
            found.append(child)
            todo.append(child)
    return found


def _kill(proc: subprocess.Popen) -> None:
    """Worker samt Firefox-Prozessbaum beenden, auch wenn er noch laeuft oder haengt."""
    for pid in _descendants(proc.pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait(timeout=5)
