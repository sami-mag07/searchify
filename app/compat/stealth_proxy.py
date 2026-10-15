"""Lokaler HTTP-Proxy mit SSRF-Pruefung fuer die Camoufox-Stufe (app/compat/stealth.py).

Warum ein Proxy und nicht context.route wie in render.py: render.py holt jede
Anfrage ueber httpx, die Zielseite sieht dann den TLS-Abdruck von httpx statt den
von Firefox, und genau der Abdruck ist der Grund, warum Camoufox Bot-Schutz
passiert (Messung 15.10.2026: 20 von 33 blockierten Seiten, Chromium 9). Der Proxy
tunnelt die TLS-Verbindung des Browsers unveraendert (CONNECT), entscheidet aber
selbst, wohin verbunden wird:

- Nur Port 80 und 443.
- Hostname wird hier aufgeloest (extract._public_ips: alle IPs muessen is_global
  sein), verbunden wird mit genau dieser IP. Der Browser loest bei einem
  HTTP-Proxy nichts selbst auf, DNS-Rebinding zwischen Pruefung und Verbindung
  ist damit ausgeschlossen.
- Jede Verbindung des Browsers laeuft hier durch (Dokument, Unterressourcen,
  Redirect-Spruenge, XHR, WebSocket), also wird jede einzeln geprueft.
- Klartext-HTTP nur mit absoluter URI und genau einer Anfrage je Verbindung: danach
  geht nur noch die Antwort zum Browser, weitere Anfragen auf derselben Verbindung
  erreichen den Server nie (sonst liefen sie ungeprueft an die erste IP).
"""

from __future__ import annotations

import ipaddress
import select
import socket
import socketserver
import threading
from typing import List, Optional, Tuple
from urllib.parse import urlsplit

from .. import extract as extract_mod

ALLOWED_PORTS = (80, 443)
_HEAD_MAX = 32 * 1024
_CONNECT_TIMEOUT_S = 8.0
_IDLE_TIMEOUT_S = 15.0


def resolve_public(host: str, port: int) -> Optional[str]:
    """Gepruefte Ziel-IP oder None (interner Host, falscher Port, nicht aufloesbar)."""
    if port not in ALLOWED_PORTS or not host:
        return None
    host = host.strip("[]")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ips = extract_mod._public_ips(host)
        return ips[0] if ips else None
    return str(ip) if extract_mod.ip_is_public(str(ip)) else None


def _split_hostport(value: str, default_port: int) -> Tuple[str, int]:
    if value.startswith("["):
        host, _, rest = value[1:].partition("]")
        port = rest.lstrip(":")
    else:
        host, _, port = value.rpartition(":") if value.count(":") == 1 else (value, "", "")
    try:
        return host, int(port) if port else default_port
    except ValueError:
        return host, -1


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    """Nur eine Richtung (Antwort zum Browser), bis src schliesst oder ruht."""
    while True:
        ready, _, _ = select.select([src], [], [], _IDLE_TIMEOUT_S)
        if not ready:
            return
        data = src.recv(65536)
        if not data:
            return
        dst.sendall(data)


def _relay(a: socket.socket, b: socket.socket) -> None:
    socks = [a, b]
    while True:
        ready, _, _ = select.select(socks, [], [], _IDLE_TIMEOUT_S)
        if not ready:
            return
        for s in ready:
            try:
                data = s.recv(65536)
            except OSError:
                return
            if not data:
                return
            (b if s is a else a).sendall(data)


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        try:
            self._handle()
        except (OSError, ValueError):  # Timeout, Abbruch durch den Browser, Proxy-Ende
            pass

    def _handle(self) -> None:
        client: socket.socket = self.request
        client.settimeout(_CONNECT_TIMEOUT_S)
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = client.recv(4096)
            if not chunk or len(head) > _HEAD_MAX:
                return
            head += chunk
        header_block, _, rest = head.partition(b"\r\n\r\n")
        lines = header_block.decode("latin-1").split("\r\n")
        try:
            method, target, version = lines[0].split(" ", 2)
        except ValueError:
            return self._deny(client)

        if method.upper() == "CONNECT":
            host, port = _split_hostport(target, 443)
            ip = resolve_public(host, port)
            if ip is None:
                return self._deny(client, target)
            upstream = self._open(ip, port)
            if upstream is None:
                return self._deny(client, target, 502)
            client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            if rest:
                upstream.sendall(rest)
        else:
            parts = urlsplit(target)
            if parts.scheme != "http" or not parts.hostname:
                return self._deny(client, target)
            port = parts.port or 80
            ip = resolve_public(parts.hostname, port)
            if ip is None:
                return self._deny(client, target)
            upstream = self._open(ip, port)
            if upstream is None:
                return self._deny(client, target, 502)
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            kept = [ln for ln in lines[1:] if ln.split(":", 1)[0].strip().lower()
                    not in ("proxy-connection", "proxy-authorization", "connection", "keep-alive")]
            out = f"{method} {path} {version}\r\n" + "\r\n".join(kept + ["Connection: close"]) + "\r\n\r\n"
            body_len = _content_length(lines[1:])
            if body_len is None:  # chunked oder kaputte Laenge: nicht weiterleiten
                upstream.close()
                return self._deny(client, target, 400)
            upstream.sendall(out.encode("latin-1") + rest[:body_len])
            left = body_len - len(rest[:body_len])
            while left > 0:
                chunk = client.recv(min(65536, left))
                if not chunk:
                    break
                upstream.sendall(chunk)
                left -= len(chunk)
            # Genau eine Anfrage je Verbindung: weitere Anfragen des Browsers auf
            # dieser Verbindung gingen sonst ungeprueft an dieselbe IP (Review 15.10.).
            try:
                client.settimeout(None)
                _pipe(upstream, client)
            finally:
                upstream.close()
            return
        try:
            client.settimeout(None)
            _relay(client, upstream)
        finally:
            upstream.close()

    def _open(self, ip: str, port: int) -> Optional[socket.socket]:
        try:
            sock = socket.create_connection((ip, port), timeout=_CONNECT_TIMEOUT_S)
        except OSError:
            return None
        sock.settimeout(None)
        self.server.track(sock)  # type: ignore[attr-defined]
        return sock

    def _deny(self, client: socket.socket, target: str = "", code: int = 403) -> None:
        if code == 403:
            self.server.blocked.append(target)  # type: ignore[attr-defined]
        reason = {403: "Forbidden", 400: "Bad Request"}.get(code, "Bad Gateway")
        try:
            client.sendall(f"HTTP/1.1 {code} {reason}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
        except OSError:
            pass


def _content_length(lines: List[str]) -> Optional[int]:
    """Laenge des Anfrage-Bodys, 0 ohne Body, None bei chunked oder ungueltig."""
    length = 0
    for ln in lines:
        name, _, value = ln.partition(":")
        name = name.strip().lower()
        if name == "transfer-encoding":
            return None
        if name == "content-length":
            try:
                length = int(value.strip())
            except ValueError:
                return None
            if length < 0:
                return None
    return length


class SsrfProxy(socketserver.ThreadingTCPServer):
    """Proxy auf 127.0.0.1 mit zufaelligem Port. Als Kontextmanager: start/stop,
    beim Stop werden alle offenen Verbindungen geschlossen."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.blocked: List[str] = []
        self._socks: List[socket.socket] = []
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        return self.server_address[1]

    def track(self, sock: socket.socket) -> None:
        with self._lock:
            self._socks.append(sock)

    def process_request(self, request, client_address):
        self.track(request)
        super().process_request(request, client_address)

    def __enter__(self) -> "SsrfProxy":
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown()
        self.server_close()
        with self._lock:
            for s in self._socks:
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    s.close()
                except OSError:
                    pass
            self._socks.clear()
