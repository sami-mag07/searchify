"""Limits for reading untrusted servers over httpx (Security-Review 03.11.2026).

* Bounded body decoding (iter_decoded): at most one content coding, decoded in
  pieces of at most _DECODE_STEP bytes, so a compression bomb never lands in memory
  in one go. Accept-Encoding names only what can be decoded that way.
* _RedirectBodyGuard: the body of a followed redirect is never decoded.
* overall_deadline / _DeadlineStream: one deadline per thread that caps every connect,
  TLS handshake, read and write of the pinned client in htmltext.
"""

from __future__ import annotations

import threading
import time
import zlib
from typing import Iterator, List, Optional

import httpcore
import httpx

_TLS = threading.local()  # .deadline: the overall deadline of the fetch in this thread
_REDIRECTS = {301, 302, 303, 307, 308}


# ---------------------------------------------------------------- bounded body decoding
# httpx decodes every network read (up to 64 KB) in one call without an output cap,
# stacked encodings ("gzip, gzip") included: 610 bytes on the wire became one 256 MB
# piece before any size check (Security-Review 03.11.2026). The page and PDF reads
# therefore take the raw bytes and decode them here, at most _DECODE_STEP per piece
# and at most one encoding layer. Brotli only with a module that has an output cap.
_DECODE_STEP = 256 * 1024


def _bounded_brotli():
    for name in ("brotli", "brotlicffi"):
        try:
            mod = __import__(name)
            probe = mod.Decompressor()
        except Exception:  # noqa: BLE001 - missing or broken: br is simply not offered
            continue
        if hasattr(probe, "can_accept_more_data"):  # Brotli >= 1.2 has output_buffer_limit
            return mod
    return None


_BROTLI = _bounded_brotli()
ACCEPT_ENCODING = "gzip, deflate, br" if _BROTLI is not None else "gzip, deflate"


class _Inflate:
    """gzip or deflate (zlib wrapper, raw deflate as fallback like httpx)."""

    def __init__(self, kind: str) -> None:
        self._raw_fallback = kind == "deflate"
        self._obj = zlib.decompressobj(zlib.MAX_WBITS | 16 if kind == "gzip" else zlib.MAX_WBITS)

    def feed(self, data: bytes) -> Iterator[bytes]:
        while not self._obj.eof:
            try:
                out = self._obj.decompress(data, _DECODE_STEP)
            except zlib.error:
                if not self._raw_fallback:
                    raise
                self._raw_fallback = False
                self._obj = zlib.decompressobj(-zlib.MAX_WBITS)
                continue
            self._raw_fallback = False
            data = self._obj.unconsumed_tail
            if out:
                yield out
            if not data and len(out) < _DECODE_STEP:
                return  # input used up and nothing left inside the decoder

    def finish(self) -> Iterator[bytes]:
        """Whatever the decoder still holds after the last network read."""
        return self.feed(b"")


class _Brotli:
    def __init__(self, mod) -> None:
        self._obj = mod.Decompressor()
        self._run = getattr(self._obj, "process", None) or self._obj.decompress

    def feed(self, data: bytes) -> Iterator[bytes]:
        out = self._run(data, output_buffer_limit=_DECODE_STEP)
        # Drain until a call gives nothing: in Brotli 1.2 can_accept_more_data() only
        # reports pending input, not output still inside the decoder, and the rest of a
        # page behind the last network read was silently lost (887 KB -> 622 KB).
        while out:
            if len(out) <= _DECODE_STEP:
                yield out
            else:  # the buffer may overshoot the limit by one growth step (480 KB for 256 KB)
                for start in range(0, len(out), _DECODE_STEP):
                    yield out[start:start + _DECODE_STEP]
            out = self._run(b"", output_buffer_limit=_DECODE_STEP)

    def finish(self) -> Iterator[bytes]:
        """Whatever the decoder still holds after the last network read."""
        return self.feed(b"")


# Real content codings (RFC 9110, with the old x- aliases). Anything else in the header
# ("UTF-8", "none", "binary" on an uncompressed body) is skipped, as httpx does.
_CODINGS = {"gzip": "gzip", "x-gzip": "gzip", "deflate": "deflate", "br": "br",
            "zstd": "zstd", "compress": "compress", "x-compress": "compress"}


def content_encodings(resp: httpx.Response) -> List[str]:
    """The known content codings of `resp`, in header order (aliases normalised)."""
    values = resp.headers.get_list("content-encoding", split_commas=True)
    return [_CODINGS[v] for v in (x.strip().lower() for x in values) if v in _CODINGS]


class _EncodingRefused(httpx.DecodingError):
    """More than one coding layer, or one we cannot decode with an output cap."""


def iter_decoded(resp: httpx.Response) -> Iterator[bytes]:
    """The body of a streamed `resp`, decoded in pieces of at most about _DECODE_STEP
    bytes, so a compression bomb never lands in memory in one go. Raises
    _EncodingRefused for stacked codings or one without an output cap (zstd, compress,
    br without a capping Brotli module), httpx.DecodingError for a broken stream."""
    layers = content_encodings(resp)
    if len(layers) > 1 or (layers and layers[0] not in ("gzip", "deflate", "br")) \
            or (layers == ["br"] and _BROTLI is None):
        raise _EncodingRefused("unsupported content encoding", request=resp.request)
    if not layers:
        yield from resp.iter_raw()
        return
    decoder = _Brotli(_BROTLI) if layers[0] == "br" else _Inflate(layers[0])
    errors = (zlib.error,) + ((_BROTLI.error,) if _BROTLI is not None else ())
    try:
        for raw in resp.iter_raw():
            yield from decoder.feed(raw)
        yield from decoder.finish()
    except errors as exc:
        raise httpx.DecodingError(str(exc), request=resp.request) from exc


class _RedirectBodyGuard(httpx.BaseTransport):
    """httpx reads and decodes the whole body of every redirect it follows, without
    a cap. Here that body is read raw up to 64 KB (so the connection stays reusable)
    and replaced by an empty one; nobody needs it, the browser follows Location."""

    _MAX_RAW = 64 * 1024

    def __init__(self, inner: httpx.BaseTransport) -> None:
        self._inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        resp = self._inner.handle_request(request)
        if resp.status_code not in _REDIRECTS:
            return resp
        try:
            seen = 0
            for raw in resp.stream:  # raw bytes, httpx has not decoded anything yet
                seen += len(raw)
                if seen > self._MAX_RAW:
                    break
        finally:
            resp.stream.close()
        return httpx.Response(resp.status_code, headers=resp.headers, stream=httpx.ByteStream(b""),
                              extensions=resp.extensions, request=request)

    def close(self) -> None:
        self._inner.close()


# The overall budget of one fetch (Security-Review 03.11.2026): httpx only knows a
# timeout per read, and the body loop only checked the budget once the headers were
# in. A server dripping 64 header bytes every 0.1 s held a 2 s budget for 6.4 s.
# The caller sets a deadline for its thread (overall_deadline); every connect,
# TLS handshake, read and write of the pinned client then gets at most the time left.
class overall_deadline:  # noqa: N801 - used like a function: with overall_deadline(5): ...
    """All network I/O of the pinned client in this thread ends `seconds` from now
    (or at an earlier deadline already set) with a timeout error."""

    __slots__ = ("_seconds", "_outer")

    def __init__(self, seconds: float) -> None:
        self._seconds = seconds

    def __enter__(self) -> float:
        self._outer = getattr(_TLS, "deadline", None)
        deadline = time.monotonic() + self._seconds
        _TLS.deadline = deadline if self._outer is None else min(self._outer, deadline)
        return _TLS.deadline

    def __exit__(self, *exc) -> None:
        _TLS.deadline = self._outer


def _time_left(timeout: Optional[float], error: type) -> Optional[float]:
    deadline = getattr(_TLS, "deadline", None)
    if deadline is None:
        return timeout
    left = deadline - time.monotonic()
    if left <= 0:
        raise error("overall time budget used up")
    return left if timeout is None else min(timeout, left)


class _DeadlineStream(httpcore.NetworkStream):
    """A connection whose every step is capped by the deadline of the thread using it."""

    def __init__(self, inner: httpcore.NetworkStream) -> None:
        self._inner = inner

    def read(self, max_bytes: int, timeout: Optional[float] = None) -> bytes:
        return self._inner.read(max_bytes, _time_left(timeout, httpcore.ReadTimeout))

    def write(self, buffer: bytes, timeout: Optional[float] = None) -> None:
        self._inner.write(buffer, _time_left(timeout, httpcore.WriteTimeout))

    def close(self) -> None:
        self._inner.close()

    def start_tls(self, ssl_context, server_hostname=None, timeout=None) -> "_DeadlineStream":
        # Python bounds the whole handshake by the socket timeout, not each step
        inner = self._inner.start_tls(ssl_context, server_hostname,
                                      _time_left(timeout, httpcore.ConnectTimeout))
        return _DeadlineStream(inner)

    def get_extra_info(self, info: str):
        return self._inner.get_extra_info(info)
