"""Shared pieces of the passages benchmark: questions, query forms, recordings, chunks.

Layout under OUT (default bench/out, override with BENCH_OUT):
  replay/search/<form>/<id>.json   recorded search hits per question and query form
  replay/pages/<sha1(url)>.json    recorded page text, shared by both forms
  scores/<model>.sqlite            cached reranker scores, key = sha1(query, text)
  latency.json, loop.jsonl, report.md
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Dict, Iterator, List

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = Path(os.environ.get("BENCH_OUT", ROOT / "out"))
REPLAY = OUT / "replay"
FORMS = ("question", "keywords")
MAX_HITS = 10          # recorded per search, the loop varies max_results up to this
MIN_CHUNK_WORDS = 3    # shorter lines are never scored, so min_words below this is moot
CHUNKINGS = ("para", "merge60", "merge120", "win60", "win120")

STOP = {
    "de": "der die das den dem des ein eine einer eines einem einen wer wie was wann wo "
          "welche welcher welches welchen welchem warum wieso weshalb wieviel viele hat "
          "haben hatte ist sind war waren wird werden wurde wurden im in am an auf aus bei "
          "mit nach von vom zu zum zur für über unter und oder es sich man gibt heißt "
          "hieß kann können",
    "en": "the a an who what when where which why how many much is are was were be been "
          "does do did has have had of in on at to for from by with and or it its there "
          "that this",
    "es": "el la los las un una unos unas quién quien qué que cuándo cuando dónde donde "
          "cuál cual cuáles cómo como cuántos cuántas es son fue fueron ha han de del en "
          "a al por para con y o se su",
    "fr": "le la les l un une des du de d qui que qu quoi quand où quel quelle quels "
          "quelles comment combien est sont était a ont été en à au aux par pour avec et "
          "ou se sa son",
}
_STOP = {lang: set(words.split()) for lang, words in STOP.items()}


def load_queries(split: str) -> List[Dict]:
    """Questions of the split: dev (tuning), test (control) or all."""
    names = ["dev", "test"] if split == "all" else [split]
    rows = []
    for name in names:
        for line in (DATA / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(dict(json.loads(line), split=name))
    return rows


def keywords(query: str, lang: str) -> str:
    """The question cut down to its content words, the way people type into a search box."""
    words = re.findall(r"[\w'’.,-]+", query.replace("’", "'"))
    words = [w.strip(".,") for w in words]
    stop = _STOP.get(lang, set())
    kept = [w for w in words if w and w.lower().split("'")[-1] not in stop
            and w.lower() not in stop]
    return " ".join(kept) or query


def query_text(q: Dict, form: str) -> str:
    return q["query"] if form == "question" else keywords(q["query"], q["lang"])


def answer_in(pattern: str, text: str) -> bool:
    """Same matching as eval/objective.py: case-insensitive, also with spaces removed."""
    rx = re.compile(pattern, re.IGNORECASE)
    tight = re.compile(re.sub(r"(\\s|\s)[?*+]?", "", pattern), re.IGNORECASE)
    return bool(rx.search(text) or tight.search(re.sub(r"\s+", "", text)))


def sha(*parts: str) -> str:
    return hashlib.sha1("\0".join(parts).encode("utf-8")).hexdigest()


def search_path(form: str, qid: str) -> Path:
    return REPLAY / "search" / form / f"{qid}.json"


def page_path(url: str) -> Path:
    return REPLAY / "pages" / f"{sha(url)}.json"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def recorded_hits(form: str, qid: str) -> List[Dict]:
    path = search_path(form, qid)
    return read_json(path).get("hits", []) if path.exists() else []


def recorded_page(url: str) -> Dict:
    path = page_path(url)
    return read_json(path) if path.exists() else {}


def _windows(words: List[str], size: int) -> Iterator[str]:
    for start in range(0, len(words), size):
        yield " ".join(words[start:start + size])


def chunk_page(content: str, chunking: str) -> List[str]:
    """Split one page's text into the units the reranker scores.

    para       one item per line, as searchify.passages does today
    mergeN     consecutive lines joined until they reach N words (keeps lists and
               tables together instead of dropping their short lines)
    winN       fixed windows of N words over the whole text, ignoring line breaks
    """
    lines = [ln.strip() for ln in (content or "").split("\n") if ln.strip()]
    if chunking == "para":
        return [ln for ln in lines if len(ln.split()) >= MIN_CHUNK_WORDS]
    size = int(re.sub(r"\D", "", chunking))
    if chunking.startswith("win"):
        return list(_windows(" ".join(lines).split(), size))
    merged, buf = [], []
    for ln in lines:
        buf.append(ln)
        if sum(len(b.split()) for b in buf) >= size:
            merged.append(" ".join(buf))
            buf = []
    if buf:
        merged.append(" ".join(buf))
    return [m for m in merged if len(m.split()) >= MIN_CHUNK_WORDS]


def candidates(q: Dict, search_form: str, chunking: str, max_results: int = MAX_HITS) -> List[Dict]:
    """Every scorable chunk of the recorded top pages, in page order, without repeats."""
    items, seen = [], set()
    for rank, hit in enumerate(recorded_hits(search_form, q["id"])[:max_results]):
        page = recorded_page(hit["url"])
        for text in chunk_page(page.get("content") or "", chunking):
            if text in seen:
                continue
            seen.add(text)
            items.append({"url": hit["url"], "rank": rank, "text": text})
    return items


def stop_at(until: str) -> float:
    """Timestamp of the next HH:MM, or infinity for an empty value."""
    import datetime as dt

    if not until:
        return float("inf")
    hh, mm = map(int, until.split(":"))
    now = dt.datetime.now()
    end = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if end <= now:
        end += dt.timedelta(days=1)
    return end.timestamp()


def load_models() -> List[Dict]:
    return json.loads((ROOT / "models.json").read_text(encoding="utf-8"))["models"]
# Stand: 02.11.2026
