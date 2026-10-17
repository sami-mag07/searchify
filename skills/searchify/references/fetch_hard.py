#!/usr/bin/env python3
"""Letzte Stufe zum Seitenlesen: Scrapling-Stealth-Fetch mit festen Parametern.

Nur nehmen, wenn donsetch.web_fetch, r.jina.ai und WebFetch an einer Seite
scheitern (Cloudflare, Bot-Schutz). Langsam (ca. 10 bis 30 s pro Seite).

  fetch_hard.py <url> [--selector "main"] [--max 20000] [--html]

Sicherheit (Auflagen aus dem Security-Vetting 30.09.2026): nur http/https,
keine privaten, Loopback- oder Link-Local-Ziele, und Scrapling bekommt NUR die
Parameter, die hier fest stehen. executable_path, cdp_url, proxy,
additional_args und real_chrome werden nie durchgereicht.
"""
import argparse
import ipaddress
import re
import os
import socket
import subprocess
import sys
import tempfile
import urllib.parse

SCRAPLING = os.path.expanduser("~/.local/bin/scrapling")


def check_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        sys.exit("abgelehnt: nur http/https mit Host")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or 443)
    except socket.gaierror:
        sys.exit("abgelehnt: Host nicht auflösbar")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            sys.exit(f"abgelehnt: {p.hostname} zeigt auf ein internes Netz")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--selector", help="CSS-Selektor, z.B. main oder article")
    ap.add_argument("--max", type=int, default=20000, help="max. Zeichen Ausgabe")
    ap.add_argument("--html", action="store_true", help="HTML statt Markdown")
    o = ap.parse_args()
    check_url(o.url)

    ext = ".html" if o.html else ".md"
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "seite" + ext)
        cmd = [SCRAPLING, "extract", "stealthy-fetch", o.url, out,
               "--solve-cloudflare", "--block-ads", "--wait", "3000",
               "--timeout", "60000"]
        if o.selector:
            cmd += ["--css-selector", o.selector]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=150)
        except subprocess.TimeoutExpired:
            sys.exit("Zeitüberschreitung")
        status = re.search(r"Fetched \((\d+)\)", p.stdout + p.stderr)
        if not os.path.exists(out):
            sys.exit("Scrapling-Fehler: " + (p.stderr or p.stdout).strip()[-300:])
        text = open(out, encoding="utf-8", errors="replace").read()
    code = status.group(1) if status else "?"
    print(f"# {code} {o.url}\n")
    print(text[:o.max])
    if code != "200":
        sys.exit(1)


if __name__ == "__main__":
    main()
