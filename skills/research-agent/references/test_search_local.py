#!/usr/bin/env python3
"""Tests ohne Netz für die Kette in search.py (eigener Dienst zuerst).

  python3 -m unittest test_search_local      (aus references/)
  python3 test_search_local.py
"""
import io
import json
import os
import socket
import sys
import time
import types
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import search  # noqa: E402

PAID_KEYS = {"TAVILY_API_KEY": "x", "SERPER_API_KEY": "x",
             "EXA_API_KEY": "x", "JINA_API_KEY": "x"}


def opts(**kw):
    base = dict(n=5, site=[], exclude=[], lang=None, news=False, since=None,
                since_d=None, engine=None, fan=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Net:
    """Ersetzt urlopen und den TCP-Probe; merkt sich jede angefragte URL."""

    def __init__(self, own_up=True, own_results=None):
        self.own_up = own_up
        self.own_results = own_results if own_results is not None else [
            {"title": "Eigener Treffer", "url": "https://example.org/a", "content": "x"}]
        self.calls = []
        self.bodies = {}

    def urlopen(self, req, timeout=None):
        url = req.full_url
        self.calls.append(url)
        if req.data:
            self.bodies[url] = json.loads(req.data)
        if url.startswith(search.OWN_URL):
            if not self.own_up:
                raise urllib.error.URLError(ConnectionRefusedError())
            if url.endswith("/exa/search"):
                res = [{"title": r["title"], "url": r["url"], "highlights": ["h"]}
                       for r in self.own_results]
            else:
                res = self.own_results
            return FakeResp(json.dumps({"results": res}).encode())
        raise AssertionError(f"unerwarteter Netzaufruf: {url}")

    def connect(self, addr, timeout=None):
        if not self.own_up:
            raise ConnectionRefusedError()
        return mock.Mock()


class ChainTest(unittest.TestCase):
    def setUp(self):
        search._own_state.clear()
        self.mcp = mock.patch.object(search, "_mcporter",
                                     side_effect=search.EngineDown("mcporter aus"))
        self.sub = mock.patch.object(search.subprocess, "run",
                                     side_effect=OSError("kein mcporter im Test"))
        self.mcp.start()
        self.sub.start()

    def tearDown(self):
        mock.patch.stopall()

    def patch_net(self, net):
        mock.patch.object(search.urllib.request, "urlopen", net.urlopen).start()
        mock.patch.object(search.socket, "create_connection", net.connect).start()

    def test_own_first_and_stops_chain(self):
        net = Net()
        self.patch_net(net)
        log = []
        hits = search.run_query("hackathon", opts(), PAID_KEYS, log)
        self.assertEqual([h["engine"] for h in hits], ["own"])
        self.assertEqual(net.calls, [search.OWN_URL + "/search"])
        self.assertEqual(log, [])

    def test_own_body_maps_filters(self):
        net = Net()
        self.patch_net(net)
        o = opts(n=30, lang="es", news=True, site=["a.es"], exclude=["b.es"],
                 since_d=search.since_date("20d"))
        search.run_query("q", o, {}, [])
        b = net.bodies[search.OWN_URL + "/search"]
        self.assertEqual(b["max_results"], 20)
        self.assertFalse(b["include_answer"])
        self.assertEqual((b["language"], b["topic"], b["time_range"]), ("es", "news", "month"))
        self.assertEqual((b["include_domains"], b["exclude_domains"]), (["a.es"], ["b.es"]))

    def test_connection_error_skips_fast(self):
        net = Net(own_up=False)
        self.patch_net(net)
        log = []
        t0 = time.time()
        hits = search.run_query("q", opts(), {}, log)
        self.assertLess(time.time() - t0, 1.0)
        self.assertEqual(hits, [])
        self.assertTrue(any("own: lokaler Dienst" in x and "nicht erreichbar" in x for x in log))
        self.assertTrue(any(x.startswith("own-exa: lokaler Dienst") for x in log))
        self.assertEqual(net.calls, [])  # kein HTTP-Versuch nach fehlgeschlagener Probe

    def test_real_probe_timeout_is_short(self):
        mock.patch.object(search, "OWN_URL", "http://127.0.0.1:1").start()
        t0 = time.time()
        self.assertIsNotNone(search.own_probe())
        self.assertLess(time.time() - t0, 1.5)

    def test_fallback_to_keyless_when_own_down(self):
        net = Net(own_up=False)
        self.patch_net(net)
        donse = lambda q, o, k: [search.hit("D", "https://d.example", "", "", "donsetch")]
        with mock.patch.dict(search.ENGINES, {"donsetch": donse}):
            hits = search.run_query("q", opts(), PAID_KEYS, [])
        self.assertEqual([h["engine"] for h in hits], ["donsetch"])
        self.assertFalse(any("tavily" in u or "serper" in u for u in net.calls))

    def test_paid_without_key_never_called(self):
        net = Net(own_up=False)
        self.patch_net(net)
        log = []
        search.run_query("q", opts(), {}, log)
        for name, key in search.PAID.items():
            self.assertIn(f"{name}: kein {key}", log)
        self.assertEqual(net.calls, [])

    def test_paid_runs_only_with_key_and_when_free_empty(self):
        net = Net(own_up=False)
        self.patch_net(net)
        seen = []
        fake = lambda name: (lambda q, o, k: seen.append(name) or [
            search.hit("P", "https://p.example", "", "", name)])
        with mock.patch.dict(search.ENGINES, {"tavily": fake("tavily"),
                                              "serper": fake("serper")}):
            hits = search.run_query("q", opts(), {"SERPER_API_KEY": "x"}, [])
        self.assertEqual(seen, ["serper"])  # tavily ohne Key übersprungen
        self.assertEqual(hits[0]["engine"], "serper")

    def test_fan_skips_paid_when_free_found(self):
        net = Net()
        self.patch_net(net)
        seen = []
        fake = lambda q, o, k: seen.append("paid") or []
        with mock.patch.dict(search.ENGINES, {n: fake for n in search.PAID}):
            hits = search.run_query("q", opts(fan=True), PAID_KEYS, [])
        self.assertEqual(seen, [])
        engines = {h["engine"] for h in hits}
        self.assertEqual(engines, {"own", "own-exa"})
        self.assertEqual(search.merge(hits)[0]["engine"], "own+own-exa")


if __name__ == "__main__":
    unittest.main(verbosity=2)
