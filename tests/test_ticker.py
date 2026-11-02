"""Die Fortschrittszeile druckt nach dem Aufraeumen nichts mehr und geht nie rueckwaerts."""
import io

from searchify import cli


def _ticker(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stderr", out)
    return cli._Ticker(), out


def test_nothing_is_printed_after_clear(monkeypatch):
    t, out = _ticker(monkeypatch)
    t.update(1, 3, 1, {})
    t.clear()
    written = out.getvalue()
    t.update(2, 3, 2, {})  # a late callback of a page past the deadline
    t.clear()
    assert out.getvalue() == written
    assert out.getvalue().endswith("\r\x1b[K")


def test_a_smaller_count_is_dropped(monkeypatch):
    t, out = _ticker(monkeypatch)
    t.update(3, 5, 3, {})
    shown = out.getvalue()
    t.update(2, 5, 2, {})  # snapshots can arrive out of order
    assert out.getvalue() == shown
    t.update(4, 5, 3, {"blocked": 1})
    assert "reading 4/5" in out.getvalue()
