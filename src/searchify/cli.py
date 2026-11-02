"""Command line interface: ``searchify search "..."``, ``searchify extract URL`` and
``searchify passages "..."``.

Output is JSON by default, so agents and scripts can parse it. Pass ``--no-json``
for a human-readable view.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from typing import Dict, List, Optional

import typer

app = typer.Typer(
    help="Local-first web search and clean page extraction.",
    no_args_is_help=True,
    add_completion=False,
)

JSON_OPT = typer.Option(True, "--json/--no-json", help="JSON output (default) or readable text.")


def _emit(data: dict) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


def _fail(message: str, as_json: bool, **extra) -> None:
    if as_json:
        _emit({"error": message, **extra})
    else:
        typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


from .interview import CI_VARS as _CI_VARS  # noqa: E402

NO_INPUT_OPT = typer.Option(False, "--no-input", help="Never ask questions (the first-run interview).")
_SHORT = {"rate_limited": "rate limited", "address_blocked": "address blocked",
          "no_main_text": "no text", "extract_failed": "unreadable", "not_html": "not HTML",
          "too_large": "too large"}


def _ticker_allowed(quiet: bool) -> bool:
    """A person at a terminal only: stdin and stderr are TTYs, no CI variable, not --quiet.
    Agents and pipes never see the ticker, because their stderr goes to the model or a file."""
    if quiet or any(os.environ.get(v) for v in _CI_VARS):
        return False
    try:
        return sys.stdin.isatty() and sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


class _Ticker:
    """One line on stderr that overwrites itself: ``reading 7/10 · 5 read · 2 blocked``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._shown = False
        self._closed = False
        self._last = 0

    def update(self, done: int, total: int, read: int, reasons: Dict[str, int]) -> None:
        parts = [f"reading {done}/{total}", f"{read} read"]
        parts += [f"{n} {_SHORT.get(code, code)}" for code, n in sorted(reasons.items())]
        with self._lock:
            if self._closed or done < self._last:  # after clear(), or an older snapshot
                return
            self._last = done
            sys.stderr.write("\r\x1b[K" + " \u00b7 ".join(parts))
            sys.stderr.flush()
            self._shown = True

    def clear(self) -> None:
        with self._lock:
            self._closed = True
            if self._shown:
                sys.stderr.write("\r\x1b[K")
                sys.stderr.flush()
                self._shown = False


@app.command()
def search(
    query: str = typer.Argument(..., help="What to search for."),
    max_results: int = typer.Option(10, "--max-results", "-n", min=1, max=50),
    backend: Optional[str] = typer.Option(None, help="duckduckgo, searxng, serper or tavily."),
    language: Optional[str] = typer.Option(None, help="Result language, e.g. en, or any for none."),
    country: Optional[str] = typer.Option(None, "--country", help="Country code, e.g. BE (with --language nl: Belgian Dutch)."),
    time_range: Optional[str] = typer.Option(None, "--time-range", help="e.g. day, week, month, year."),
    include_domain: Optional[List[str]] = typer.Option(None, "--include-domain", help="Repeatable."),
    exclude_domain: Optional[List[str]] = typer.Option(None, "--exclude-domain", help="Repeatable."),
    no_input: bool = NO_INPUT_OPT,
    as_json: bool = JSON_OPT,
) -> None:
    """Search the web and print the hits."""
    from .interview import first_run
    from .search import BACKENDS, search_report

    first_run(no_input)
    if backend is not None and backend not in BACKENDS:
        _fail(f"unknown backend {backend!r}, use {', '.join(BACKENDS)}", as_json, results=[])
    filters = {
        "language": language,
        "country": country,
        "time_range": time_range,
        "include_domains": include_domain or None,
        "exclude_domains": exclude_domain or None,
    }
    filters = {k: v for k, v in filters.items() if v is not None}
    report = search_report(query, max_results, backend=backend, **filters)
    hits = report["results"]
    if not hits and report["error"]:
        _fail(str(report["error"]), as_json, results=[])
    if as_json:
        _emit({"results": hits})
        return
    if not hits:
        typer.echo("No results.")
        return
    for i, hit in enumerate(hits, 1):
        typer.secho(f"{i}. {hit.get('title', '')}", bold=True)
        typer.echo(f"   {hit.get('url', '')}")
        if hit.get("content"):
            typer.echo(f"   {hit['content']}")


@app.command()
def extract(
    urls: List[str] = typer.Argument(..., help="One or more page addresses."),
    fmt: str = typer.Option("text", "--format", "-f", help="text or markdown."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="No progress line on stderr."),
    max_stage: Optional[str] = typer.Option(
        None, "--max-stage", help="Highest way to load a page: plain, tls, browser or stealth."),
    budget: Optional[str] = typer.Option(
        None, "--budget", help="low (short waits and pages), normal or high (long waits)."),
    no_cloud: bool = typer.Option(False, "--no-cloud", help="Never ask Tavily (it costs credits)."),
    no_input: bool = NO_INPUT_OPT,
    structured: bool = typer.Option(False, "--structured", help="Add the raw JSON-LD and meta tags."),
    as_json: bool = JSON_OPT,
) -> None:
    """Fetch pages and print their clean article text.

    Each failed page comes with "error" (text) and "reason" (a fixed code such as
    blocked, rate_limited, timeout). At an interactive terminal a progress line
    shows on stderr; pipes, agents and CI never get one. --max-stage, --budget and
    --no-cloud limit the load of this call; what you leave out comes from your
    profile (`searchify config`), and the operator cap SEARCHIFY_MAX_STAGE wins.
    """
    if fmt not in ("text", "markdown"):
        _fail(f"unknown format {fmt!r}, use text or markdown", as_json)
    from . import limits as limits_mod
    from . import profile as profile_mod
    from .extract import read_pages_ex
    from .interview import first_run
    from .reasons import code_for

    first_run(no_input)
    try:
        wanted = limits_mod.explicit(max_stage, budget, False if no_cloud else None, profile_mod.load())
    except ValueError as exc:
        _fail(str(exc), as_json)

    ticker = _Ticker() if _ticker_allowed(quiet) else None
    try:
        docs, failed, _thin = read_pages_ex(urls, fmt, progress=ticker.update if ticker else None,
                                            structured=structured, **wanted)
    finally:
        if ticker:
            ticker.clear()
    failed = [{"url": f.get("url"), "error": f.get("error"),
               "reason": f.get("reason") or code_for(f.get("error"))} for f in failed]
    if as_json:
        _emit({"results": docs, "failed": failed})
    else:
        for doc in docs:
            typer.secho(doc.get("title") or doc.get("url", ""), bold=True)
            typer.echo(doc.get("url", ""))
            typer.echo(f"\n{doc.get('content', '')}\n")
        for f in failed:
            typer.secho(f"failed: {f['url']} ({f['error']})", fg=typer.colors.RED, err=True)
    if failed and not docs:
        raise typer.Exit(1)


@app.command()
def passages(
    query: str = typer.Argument(..., help="A full question works better than keywords."),
    max_results: int = typer.Option(5, "--max-results", "-n", min=1, max=20,
                                    help="Result pages to read."),
    top_k: int = typer.Option(8, "--top-k", "-k", min=1, max=50, help="Passages to return."),
    per_site: Optional[int] = typer.Option(None, "--per-site", min=1,
                                           help="At most this many passages from one site."),
    max_chars: int = typer.Option(600, "--max-chars", min=50, help="Trim each passage."),
    rerank: Optional[bool] = typer.Option(
        None, "--rerank/--no-rerank",
        help="Rank by relevance. Default: on if the [rerank] extra is installed."),
    backend: Optional[str] = typer.Option(None, help="duckduckgo, searxng, serper or tavily."),
    language: Optional[str] = typer.Option(None, help="Result language, e.g. en."),
    time_range: Optional[str] = typer.Option(None, "--time-range", help="e.g. day, week, month, year."),
    include_domain: Optional[List[str]] = typer.Option(None, "--include-domain", help="Repeatable."),
    exclude_domain: Optional[List[str]] = typer.Option(None, "--exclude-domain", help="Repeatable."),
    no_input: bool = NO_INPUT_OPT,
    as_json: bool = JSON_OPT,
) -> None:
    """Search, read the top pages and print their best paragraphs."""
    from .interview import first_run
    from .passages import passages as best_passages
    from .search import BACKENDS, SearchBackendError

    first_run(no_input)
    if backend is not None and backend not in BACKENDS:
        _fail(f"unknown backend {backend!r}, use {', '.join(BACKENDS)}", as_json, results=[])
    filters = {
        "language": language,
        "time_range": time_range,
        "include_domains": include_domain or None,
        "exclude_domains": exclude_domain or None,
    }
    filters = {k: v for k, v in filters.items() if v is not None}
    try:
        found = best_passages(query, max_results, top_k=top_k, per_site=per_site,
                              max_chars=max_chars, rerank=rerank, backend=backend, **filters)
    except (SearchBackendError, ImportError) as exc:
        _fail(str(exc), as_json, results=[])
    if as_json:
        _emit({"results": found})
        return
    if not found:
        typer.echo("No results.")
        return
    for i, item in enumerate(found, 1):
        typer.secho(f"{i}. {item['content']}", bold=True)
        typer.echo(f"   {item['url']}")


@app.command()
def config(
    show: bool = typer.Option(False, "--show", help="Print every setting and where it comes from."),
    max_stage: Optional[str] = typer.Option(
        None, "--max-stage", help="Save: plain, tls, browser or stealth ('default' clears it)."),
    budget: Optional[str] = typer.Option(
        None, "--budget", help="Save: low, normal or high ('default' clears it)."),
    region: Optional[str] = typer.Option(
        None, "--region", help="Save a DuckDuckGo search region such as de-de ('default' clears it)."),
    no_input: bool = NO_INPUT_OPT,
    as_json: bool = JSON_OPT,
) -> None:
    """Show or change your profile (how far searchify may go, budget, search region).

    Without options, at a terminal, the three questions are asked again with your
    current answers. With options, the values are saved directly. The profile is a
    JSON file in your user config folder; the HTTP service and the MCP server never read it.
    """
    from . import interview
    from . import limits as limits_mod
    from . import profile as profile_mod
    from .search.regions import is_region

    changes = {"max_stage": max_stage, "budget": budget, "region": region}
    asked = {k: v for k, v in changes.items() if v is not None}
    if asked:
        new = profile_mod.load(force=True)
        try:
            for key, value in asked.items():
                word = value.strip().lower()
                if word in interview.RESET:
                    new.pop(key, None)
                    continue
                if key == "max_stage":
                    limits_mod.resolve(max_stage=word)
                elif key == "budget":
                    limits_mod.resolve(budget=word)
                elif not is_region(word):
                    raise ValueError(f"unknown region {value!r}, use a DuckDuckGo code such as de-de")
                new[key] = word
        except ValueError as exc:
            _fail(str(exc), as_json)
        _save_profile(new, as_json)
    elif not show:
        if not interview.interactive(no_input):
            _fail("`searchify config` asks questions and needs a terminal; use --show, or set "
                  "values with --max-stage, --budget or --region", as_json)
        answers = interview.run(current=profile_mod.load(force=True))
        if answers is not None:
            _save_profile(answers, as_json)
    _print_config(as_json)


def _save_profile(values: dict, as_json: bool) -> None:
    from . import profile as profile_mod

    try:
        profile_mod.save(values)
    except OSError as exc:
        _fail(f"could not save the profile ({type(exc).__name__})", as_json)


def _print_config(as_json: bool) -> None:
    from . import limits as limits_mod
    from . import profile as profile_mod

    prof = profile_mod.load(force=True)
    got = limits_mod.detail(profile=prof)
    values = {name: {"value": value, "source": source} for name, (value, source) in got.items()}
    values["region"] = ({"value": prof["region"], "source": "profile"} if "region" in prof
                        else {"value": None, "source": "default"})
    data = {"path": str(profile_mod.profile_path()), "exists": profile_mod.exists(),
            "operator_cap": limits_mod.operator_cap(), "values": values}
    if as_json:
        _emit(data)
        return
    typer.echo(f"profile: {data['path']}" + ("" if data["exists"] else " (not created yet)"))
    for name, item in values.items():
        typer.echo(f"{name}: {item['value']} ({item['source']})")
    if data["operator_cap"]:
        typer.echo(f"operator cap: {data['operator_cap']} (SEARCHIFY_MAX_STAGE)")


def main() -> None:  # extraction uses worker processes, so entry points need a real main
    app()


def mcp_main() -> None:
    """Entry point of searchify-mcp: start the MCP server, or say which extra is missing."""
    try:
        from .mcp_server import main as serve
    except ImportError as exc:
        if not (exc.name or "").split(".")[0] == "mcp":
            raise
        typer.secho('error: the MCP server needs the extra: pip install "searchify[mcp]" '
                    "(Python 3.10+)", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    serve()


if __name__ == "__main__":
    main()
# Stand: 02.11.2026
