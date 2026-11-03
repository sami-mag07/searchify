"""The first-run interview of the command line (and ``searchify config``).

It runs only for a person at a terminal: stdin, stdout and stderr are all TTYs,
no CI variable is set and ``--no-input`` was not given. The prompts go to stderr,
never to stdout, so JSON output stays clean, and ``searchify search q > out.json``
never stops to wait for an answer. At most three questions: how far the load
ladder may go, the budget and, when the system locale suggests one, the search
region. Enter keeps today's behaviour. Answering ``later`` (or closing the input)
writes nothing, so the question comes back next time.

Agents, scripts, pipes, the HTTP service and the MCP server never get here.
"""
from __future__ import annotations

import locale
import os
import re
import sys
from typing import Callable, Dict, Mapping, Optional, TextIO, Tuple

from . import profile
from .limits import BUDGETS, STAGES
from .search.regions import DDG_REGIONS, ddg_region

CI_VARS = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "JENKINS_URL", "TF_BUILD")
LATER = {"later", "spaeter", "später"}
RESET = {"default", "none"}
TRIES = 3
_asked = False  # first_run asks at most once per process

_STAGE_HELP = (
    "  plain    fast fetch only\n"
    "  tls      plus a Chrome-like fetch for sites that turn bots away\n"
    "  browser  plus a headless browser for pages that need JavaScript\n"
    "  stealth  plus a stealth browser (slowest, most load)\n"
)


def interactive(no_input: bool = False, env: Optional[Mapping[str, str]] = None) -> bool:
    """A person at a terminal: stdin and stderr are TTYs, no CI variable, not --no-input."""
    env = os.environ if env is None else env
    if no_input or any(env.get(v) for v in CI_VARS):
        return False
    try:
        return bool(sys.stdin.isatty() and sys.stderr.isatty())
    except (AttributeError, ValueError):
        return False


def _stdout_is_tty() -> bool:
    """False when the output goes to a file or a pipe: then nobody may be watching."""
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


_LOCALE_RE = re.compile(r"^([a-z]{2,3})(?:[_-]([A-Za-z]{2}))?(?:[.@].*)?$")


def detect_region(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The DuckDuckGo region the system locale points to (``de_DE.UTF-8`` gives
    ``de-de``), or None if the locale says nothing useful. Only ever a suggestion."""
    env = os.environ if env is None else env
    raw = ""
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        raw = (env.get(name) or "").strip()
        if raw:
            break
    if not raw and env is os.environ:
        try:
            raw = locale.getlocale()[0] or ""
        except (ValueError, TypeError):
            raw = ""
    match = _LOCALE_RE.match(raw)
    if not match or raw.upper() in ("C", "POSIX"):
        return None
    code = ddg_region(match.group(1), match.group(2))
    return code if code != "wt-wt" else None


def _read(stdin: TextIO) -> Optional[str]:
    """One line without the newline; None when the input is closed."""
    line = stdin.readline()
    return None if line == "" else line.strip()


class _Later(Exception):
    """The person chose to be asked next time."""


def _ask(prompt: str, parse: Callable[[str], object], stdin: TextIO, err: TextIO) -> object:
    """Ask until `parse` accepts (at most TRIES times, then Enter's meaning). `parse`
    gets the stripped answer and returns the value or raises ValueError."""
    for _ in range(TRIES):
        err.write(prompt)
        err.flush()
        answer = _read(stdin)
        if answer is None or answer.lower() in LATER:
            raise _Later
        try:
            return parse(answer)
        except ValueError as exc:
            err.write(f"  {exc}\n")
    return parse("")


_KEEP = object()  # "leave this value as it is"


def _choice(allowed: Tuple[str, ...], current: Optional[str]) -> Callable[[str], object]:
    def parse(answer: str) -> object:
        word = answer.lower()
        if word == "":
            return _KEEP if current else None
        if word in RESET:
            return None
        if word.isdigit() and 1 <= int(word) <= len(allowed):
            return allowed[int(word) - 1]
        if word in allowed:
            return word
        raise ValueError(f"please type one of: {', '.join(allowed)} (or a number)")
    return parse


def _yes_no(code: str, current: Optional[str]) -> Callable[[str], object]:
    def parse(answer: str) -> object:
        word = answer.lower()
        if word == "":
            return _KEEP if current else None
        if word in ("y", "yes", "j", "ja"):
            return code
        if word in ("n", "no", "nein") or word in RESET:
            return None
        raise ValueError("please answer y or n")
    return parse


def _enter(current: Optional[str], fallback: str) -> str:
    return f"Enter = keep {current}" if current else f"Enter = {fallback}"


def run(
    current: Optional[Mapping[str, object]] = None,
    stdin: Optional[TextIO] = None, stderr: Optional[TextIO] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Optional[Dict]:
    """Ask the questions. Returns the new profile values (only what was chosen), or
    None if the person answered ``later``."""
    stdin = stdin or sys.stdin
    err = stderr or sys.stderr
    have = dict(current or {})
    answers: Dict[str, object] = dict(have)
    err.write("searchify: three quick questions, you can change them later with `searchify config`.\n"
              "Type 'later' at any question to be asked again next time.\n\n")
    try:
        err.write("1/3 How far may searchify go to load a page?\n" + _STAGE_HELP)
        stage = _ask(f"    {_enter(have.get('max_stage'), 'no limit')} > ",  # type: ignore[arg-type]
                     _choice(STAGES, have.get("max_stage")), stdin, err)  # type: ignore[arg-type]
        err.write("\n2/3 Budget: low (short waits, short pages), normal, high (long waits).\n")
        budget = _ask(f"    {_enter(have.get('budget'), 'normal')} > ",  # type: ignore[arg-type]
                      _choice(BUDGETS, have.get("budget")), stdin, err)  # type: ignore[arg-type]
        region = None
        guess = detect_region(env)
        if have.get("region") or guess:
            code = str(have.get("region") or guess)
            name = DDG_REGIONS.get(code, code)
            err.write(f"\n3/3 Your system suggests the search region {name} ({code}).\n")
            region = _ask(f"    Use it for searches? [y/N]{'' if not have.get('region') else ', Enter = keep'} > ",
                          _yes_no(code, have.get("region")), stdin, err)  # type: ignore[arg-type]
    except _Later:
        err.write("\nOk, nothing saved. You will be asked again next time.\n")
        return None
    for key, value in (("max_stage", stage), ("budget", budget), ("region", region)):
        if value is _KEEP:
            continue
        if value is None:
            answers.pop(key, None)
        else:
            answers[key] = value
    return answers


def first_run(no_input: bool = False) -> None:
    """Called by the CLI commands: interview once, when there is no profile yet and a
    person is at the terminal. A saved profile or ``later`` is never asked about twice
    in one process."""
    global _asked
    if _asked or profile.exists() or not interactive(no_input) or not _stdout_is_tty():
        return
    _asked = True
    answers = run()
    if answers is None:
        return
    try:
        path = profile.save(answers)
    except OSError as exc:
        sys.stderr.write(f"searchify: could not save the profile ({type(exc).__name__}); continuing.\n")
        return
    sys.stderr.write(f"Saved to {path}. Change it with `searchify config`.\n\n")

