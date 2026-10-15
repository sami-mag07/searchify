"""Ausgangs-Proxy fuer die Render-Stufe 3 (app/compat/render.py).

Der Browser laedt in Stufe 3 selbst (echter Chrome-TLS-Fingerabdruck gegen Bot-Schutz),
aber nur ueber diesen Proxy auf 127.0.0.1. Er ist die SSRF-Grenze:

- CONNECT host:port (https, wss): Host aufloesen, nur wenn ALLE IPs oeffentlich sind
  (app/extract._public_ips, IP-Literale direkt geprueft), und genau zu dieser
  gepruefte IP verbinden (kein DNS-Rebinding). Danach werden die Bytes unveraendert
  durchgereicht, TLS macht der Browser selbst.
- Klartext-http (absolute URL): gleiche Pruefung, eine Anfrage je Verbindung
  ("Connection: close"), damit keine Folgeanfrage an einen anderen Host durchrutscht.
- Nur Web-Ports (PORTS). Alles andere -> 403.
"""

from __future__ import annotations

import asyncio
import ipaddress
from typing import List, Optional, Tuple
from urllib.parse import urlsplit

from .. import extract as extract_mod

PORTS = {80, 443, 8080, 8443}
CONNECT_TIMEOUT_S = 8.0
HEAD_TIMEOUT_S = 10.0
_DROP = ("proxy-", "connection:", "keep-alive:")


def _resolve(host: str) -> List[str]:
    """Gepruefte IPs zum Host, [] wenn eine davon nicht oeffentlich ist."""
    host = host.strip().strip("[]").rstrip(".")
    if not host:
        return []
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return extract_mod._public_ips(host)
    return [str(ip)] if ip.is_global else []


async def _open(host: str, port: int) -> Optional[Tuple[asyncio.StreamReader, asyncio.StreamWriter]]:
    if port not in PORTS:
        return None
    for ip in await asyncio.to_thread(_resolve, host):
        try:
            return await asyncio.wait_for(asyncio.open_connection(ip, port), CONNECT_TIMEOUT_S)
        except (OSError, asyncio.TimeoutError):
            continue
    return None


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:  # noqa: BLE001 - Gegenseite zu
        pass
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


def _deny(writer: asyncio.StreamWriter, code: str = "403 Forbidden") -> None:
    try:
        writer.write(f"HTTP/1.1 {code}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
        writer.close()
    except Exception:  # noqa: BLE001
        pass


async def _handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
    try:
        head = await asyncio.wait_for(client_r.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT_S)
        lines = head.decode("latin-1").split("\r\n")
        method, target, version = lines[0].split(" ", 2)
    except Exception:  # noqa: BLE001 - kaputte oder zu lange Anfrage
        _deny(client_w, "400 Bad Request")
        return
    if method == "CONNECT":
        host, _, port = target.rpartition(":")
        conn = await _open(host, int(port)) if port.isdigit() else None
        if conn is None:
            _deny(client_w)
            return
        up_r, up_w = conn
        client_w.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await client_w.drain()
        await asyncio.gather(_pipe(client_r, up_w), _pipe(up_r, client_w))
        return
    parts = urlsplit(target)
    try:
        port = parts.port or 80
    except ValueError:
        port = 0
    if parts.scheme != "http" or not parts.hostname:
        _deny(client_w, "400 Bad Request")
        return
    headers = [h for h in lines[1:] if h and not h.lower().startswith(_DROP)]
    if any(h.lower().startswith("transfer-encoding:") for h in headers):
        _deny(client_w, "501 Not Implemented")
        return
    length = next((int(h.split(":", 1)[1]) for h in headers
                   if h.lower().startswith("content-length:") and h.split(":", 1)[1].strip().isdigit()), 0)
    conn = await _open(parts.hostname, port)
    if conn is None:
        _deny(client_w)
        return
    up_r, up_w = conn
    try:
        body = await client_r.readexactly(length) if length else b""
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        up_w.write(f"{method} {path} {version}\r\n".encode("latin-1")
                   + "".join(f"{h}\r\n" for h in headers).encode("latin-1")
                   + b"Connection: close\r\n\r\n" + body)
        await up_w.drain()
    except Exception:  # noqa: BLE001
        up_w.close()
        _deny(client_w, "502 Bad Gateway")
        return
    await _pipe(up_r, client_w)  # Antwort bis der Server schliesst, dann Verbindung zu
    up_w.close()


async def start() -> Tuple[asyncio.AbstractServer, int]:
    """Proxy auf 127.0.0.1 (freier Port) in der laufenden Schleife starten."""
    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]
