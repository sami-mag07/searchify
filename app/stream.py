"""Engine-getrennte Suche mit sofortigem Laden ("Bing zuerst").

SearXNG wartet bei einer gemeinsamen Anfrage auf die langsamste Engine (Yahoo
~0.6 s, Bing ~0.16 s). Hier wird jede Engine einzeln gefragt. Sobald eine
Engine antwortet, gehen ihre Snippets in den Reranker und ihre Seiten in den
Download. Die langsame Engine mischt ihre Treffer nach, waehrend die ersten
Seiten schon geladen und gerankt werden.

Aktiv nur mit SEARCH_BACKEND=searxng und gesetztem SEARXNG_SPLIT_ENGINES
(z. B. "bing,yahoo"). Liefert keine Engine etwas, gibt `gather` None zurueck
und der Aufrufer nimmt den normalen Weg (inkl. Fallback-Backend).
"""

from __future__ import annotations

import math
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Dict, List, Optional, Tuple

from . import extract as extract_mod
from . import rerank as rerank_mod
from . import search as search_mod
from .config import get_settings

# Eigener Pool fuer Engine-Anfragen. Je Anfrage bis zu 12 Futures (3 Engines x
# 2 Seiten x Zweitanfrage), bei 40 Request-Threads also 480. Threads entstehen
# erst bei Bedarf. Geteilt mit dem Serper-Hedging wuerden sich haengende
# Engine-Anfragen und Serper gegenseitig blockieren.
_ENGINE_POOL = ThreadPoolExecutor(max_workers=480, thread_name_prefix="engine")


# Nur diese Engines koennen ueber SearXNG blaettern. Bing nicht: SearXNG kennt
# dort kein Paging, und Bing ignoriert "first=11" ohne JavaScript (liefert wieder
# Seite 1, gemessen 11.10.2026).
PAGING_ENGINES = {"yahoo", "yandex"}


_LAST = threading.local()


def last_complete() -> bool:
    """Ob im letzten gather dieses Threads alle Engine-Slots geantwortet haben."""
    return getattr(_LAST, "complete", True)


def engines_for(language: Optional[str]) -> List[str]:
    """Engines je Sprache: SEARXNG_SPLIT_ENGINES_<SPRACHE> (z. B. _ES), sonst
    SEARXNG_SPLIT_ENGINES. Bing liefert ueber SearXNG bei es/fr 0 Treffer, darum
    gelten fuer es/fr eigene Defaults (Yandex + Yahoo)."""
    settings = get_settings()
    default = {"es": "yandex,yahoo", "fr": "yandex,yahoo"}.get(language or "", settings.split_engines)
    raw = os.environ.get(f"SEARXNG_SPLIT_ENGINES_{(language or '').upper()}", default) if language else default
    return [e.strip() for e in raw.split(",") if e.strip()]


def _search_kwargs(req) -> Dict:
    return {
        "search_depth": req.search_depth,
        "topic": req.topic,
        "language": req.language,
        "time_range": req.time_range,
        "include_domains": req.include_domains,
        "exclude_domains": req.exclude_domains,
    }


def gather(
    req, budget_s: float, candidate_pool: int = 10
) -> Optional[Tuple[List[Dict], List[Dict], int, float]]:
    """Suche + Laden + Ranken ueberlappend.

    Rueckgabe: (candidates, ranked_chunks, seiten_geladen, sekunden_bis_suche_fertig)
    oder None, wenn keine Engine Treffer lieferte.
    """
    settings = get_settings()
    engines = engines_for(req.language)
    started = time.monotonic()
    search_deadline = started + settings.split_search_timeout_ms / 1000
    kwargs = _search_kwargs(req)
    # Je Engine genug, damit zusammen mindestens candidate_pool Quellen moeglich sind.
    per_engine = max(settings.split_per_engine, math.ceil(candidate_pool / max(1, len(engines))))
    # Eine Ergebnisseite bringt ~7-10 Treffer je Engine. Fuer mehr als 10 Quellen
    # kommen weitere Seiten der blaetternden Engines dazu (SPLIT_PAGES_WIDE, Default 3),
    # parallel abgefragt. Fehleranalyse 12.10.2026: bei Recherche-Fragen lag die
    # Gold-Quelle in 10 von 27 dev-Fragen nicht unter den Kandidaten, Bing traf fast
    # nie, Yahoo-Seite 3 schon. Replay dev 0.9281 -> 0.9355, test 0.8578 -> 0.8651.
    page_count = max(1, settings.split_pages_wide) if candidate_pool > 10 else 1
    slots = [
        f"{name}#{page}"
        for name in engines
        for page in range(1, (page_count if name in PAGING_ENGINES else 1) + 1)
    ]

    futures: Dict = {}

    def submit(slot: str):
        name, page = slot.split("#")
        fut = _ENGINE_POOL.submit(
            search_mod.searxng_search, req.query, per_engine, engines=name,
            page=int(page), **kwargs,
        )
        futures[fut] = slot
        return fut

    fetcher = extract_mod.PageFetcher()
    candidates: List[Dict] = []
    seen: Dict[str, int] = {}
    ranked: List[Dict] = []
    extracted = 0
    first_hit: Optional[float] = None

    def take_pages(timeout: float) -> None:
        nonlocal extracted
        batch = fetcher.poll(timeout)
        if not batch:
            return
        picked: List[Dict] = []
        for idx, text in batch:
            body = extract_mod.page_chunks(candidates[idx], text, with_snippet=False)
            picked += rerank_mod.best_chunks(req.query, body, settings.rerank_chunks_per_page)
        extracted += len(batch)
        ranked.extend(rerank_mod.rerank(req.query, rerank_mod.dedup_chunks(picked)))

    def harvest(done, pending) -> None:
        nonlocal first_hit
        new: List[Dict] = []
        for fut in done:
            name = futures[fut]  # Slot "engine#seite"
            if name in answered:
                continue  # die andere Anfrage dieses Slots war schneller
            try:
                hits = fut.result()
            except search_mod.SearchBackendError:
                hits = []
            if not hits and any(futures[p] == name for p in pending):
                continue  # leer (Timeout in SearXNG), die Zweitanfrage laeuft noch
            answered.add(name)
            if not hits:
                empty.add(name)
            engine = name.split("#")[0]
            for hit in hits:
                key = rerank_mod.canonical_url(hit.get("url", ""))
                if not key:
                    continue
                rank = hit.get("engine_rank")
                if key in seen:
                    # Schon von einer anderen Engine/Seite da: nur ihren Rang merken
                    # (Engine-Uebereinstimmung fuer die Rang-Fusion).
                    if rank:
                        ranks = candidates[seen[key]].setdefault("engine_ranks", {})
                        ranks[engine] = min(rank, ranks.get(engine, rank))
                    continue
                if rank:
                    hit = dict(hit, engine_ranks={engine: rank})
                seen[key] = len(candidates)
                fetcher.add(len(candidates), hit["url"])
                candidates.append(hit)
                new.append(hit)
        if new:
            first_hit = first_hit or time.monotonic()
            snippets = [ch for cand in new for ch in extract_mod.page_chunks(cand, None)]
            ranked.extend(rerank_mod.rerank(req.query, snippets))

    # Versetzte Zweitanfrage: Yahoo braucht meist 0.5-0.95 s, haengt aber in ~3 %
    # der Faelle ueber 1.3 s (zufaellig). Ist eine Engine nach SPLIT_HEDGE_MS
    # nicht da, geht dieselbe Anfrage noch einmal raus; die erste Antwort zaehlt.
    hedge_after = settings.split_hedge_ms / 1000
    pending = {submit(slot) for slot in slots}
    answered: set = set()
    empty: set = set()  # Slots, die leer geantwortet haben (Engine-Timeout, Sperre)
    hedged: set = set()
    while time.monotonic() < search_deadline and len(answered) < len(slots):
        if hedge_after > 0 and time.monotonic() - started >= hedge_after:
            for slot in slots:
                if slot not in answered and slot not in hedged:
                    hedged.add(slot)
                    pending.add(submit(slot))
        if not pending:
            break
        done, pending = wait(pending, timeout=0.02, return_when=FIRST_COMPLETED)
        harvest(done, pending)
        take_pages(0)
    # Schon fertige Antworten nach Ablauf der Frist trotzdem einsammeln. Haengt der
    # Reranker (Modell laedt, CPU voll), liefen sonst laengst angekommene Engine-Seiten
    # ins Leere: Fehleranalyse 12.10.2026, erste Anfrage nach dem Start hatte nur
    # Yahoo-Seite 3 (7 statt 30 Kandidaten), Gold auf Platz 11 statt 1.
    late = {fut for fut in pending if fut.done()}
    if late:
        harvest(late, pending - late)
        take_pages(0)
    t_search = time.monotonic()
    _LAST.complete = len(answered) >= len(slots) and not empty

    if not candidates:
        return None

    # Seiten: mindestens `budget_s` ab dem ersten Treffer, nach der letzten Engine
    # aber hoechstens noch einen kurzen Rest (SPLIT_TAIL_MS) warten.
    tail = min(budget_s, settings.split_tail_ms / 1000)
    page_deadline = max(t_search + tail, (first_hit or t_search) + budget_s)
    while fetcher.has_pending():
        remaining = page_deadline - time.monotonic()
        if remaining <= 0:
            break
        take_pages(remaining)

    ranked.sort(key=rerank_mod.rank_order, reverse=True)
    return candidates, ranked, extracted, t_search - started
