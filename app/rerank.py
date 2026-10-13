"""Re-Ranking + Dedup.

- BM25-Vorauswahl je Quelle, dann Cross-Encoder-Reranking Query<->Chunk (Default mMiniLM, bge waehlbar)
  ueber transformers, auf CUDA/MPS wenn vorhanden.
- Dedup/Kanonisierung als reine Python-Heuristik (keine Schwer-Abhaengigkeit).

Das Reranker-Modell wird LAZY geladen und als Singleton gecacht, damit dieses
Modul ohne installiertes transformers/torch importierbar bleibt.
score = sigmoid-normalisierter Reranker-Score auf 0..1 (eigene, offengelegte Skala).
"""

from __future__ import annotations

import contextlib
import contextvars
import math
import re
import threading
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

from . import lang as lang_mod
from . import quality as quality_mod
from .config import get_settings

_RERANKER = None  # (tokenizer, model, device) - lazy Singleton
# Laden und Inferenz serialisieren: FastAPI ruft synchrone Endpunkte aus vielen
# Threads, und parallele Forward-Aufrufe auf MPS sind in PyTorch nicht sicher.
# Schneller wuerde es ohnehin nicht, die GPU arbeitet nacheinander.
_LOAD_LOCK = threading.Lock()
_INFER_LOCK = threading.Lock()


def _pick_device():
    import torch  # noqa: PLC0415 (bewusst lazy)

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _get_reranker():
    """Laedt den Cross-Encoder (RERANKER_MODEL) direkt ueber transformers auf GPU/MPS, sonst CPU."""
    global _RERANKER
    if _RERANKER is not None:
        return _RERANKER
    with _LOAD_LOCK:
        if _RERANKER is None:
            _RERANKER = _load_reranker()
    return _RERANKER


def _load_reranker():
    try:
        import torch  # noqa: PLC0415
        from transformers import AutoModelForSequenceClassification, AutoTokenizer  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - nur ohne Installation
        raise ImportError(
            "transformers/torch sind nicht installiert (stehen in requirements.txt)."
        ) from exc

    settings = get_settings()
    device = _pick_device()
    dtype = torch.float16 if settings.reranker_use_fp16 and device != "cpu" else torch.float32
    tokenizer = AutoTokenizer.from_pretrained(settings.reranker_model)
    model = AutoModelForSequenceClassification.from_pretrained(
        settings.reranker_model, torch_dtype=dtype
    ).to(device).eval()
    return (tokenizer, model, device)


def _cross_scores(query: str, texts: List[str], max_length: int, batch: int = 32) -> List[float]:
    import torch  # noqa: PLC0415

    tokenizer, model, device = _get_reranker()
    # truncation="only_second" kuerzt nur den Text; ist die Query selbst laenger als
    # die Grenze (findSimilar: Titel + Seitenbeschreibung), wirft der Tokenizer
    # ("Sequence to truncate too short"). Die Query daher auf die halbe Grenze kappen.
    # Auch dieser Tokenizer-Aufruf nur unter der Sperre: der schnelle Tokenizer ist
    # nicht threadsicher ("RuntimeError: Already borrowed"). Ausserhalb lief er
    # parallel zum Warmup bzw. einer zweiten Anfrage -> 500 (Bench 13.10.2026, erste
    # Anfrage nach dem Start).
    scores: List[float] = []
    with _INFER_LOCK, torch.inference_mode():
        q_ids = tokenizer(query, add_special_tokens=False)["input_ids"]
        if len(q_ids) > max_length // 2:
            query = tokenizer.decode(q_ids[: max_length // 2])
        for i in range(0, len(texts), batch):
            enc = tokenizer(
                [query] * len(texts[i : i + batch]),
                texts[i : i + batch],
                padding=True,
                truncation="only_second",
                max_length=max_length,
                return_tensors="pt",
            ).to(device)
            scores.extend(model(**enc).logits.view(-1).float().cpu().tolist())
    return scores


def warm_up() -> None:
    """Modell vorab laden und einmal rechnen lassen (kurze und lange Eingabe).
    Ohne das traegt die erste Anfrage nach dem Start Laden + MPS-Kernel-Aufbau
    (gemessen 16.5 s statt ~2 s). Fehler hier duerfen den Start nicht kippen."""
    try:
        _cross_scores("warm up", ["warm up", "warm up " * 200], get_settings().rerank_max_length)
    except Exception:  # noqa: BLE001 - ohne Modell laeuft der Dienst trotzdem an
        pass


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> List[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 1]


def lexical_scores(query: str, texts: List[str], k1: float = 1.2, b: float = 0.75) -> List[float]:
    """BM25 der Query gegen jeden Text, IDF aus genau dieser Chunk-Menge.

    Billiger Vorfilter vor dem Cross-Encoder (reine Python-Funktion, testbar).
    """
    docs = [_tokens(t) for t in texts]
    if not docs:
        return []
    avg_len = sum(len(d) for d in docs) / len(docs) or 1.0
    q_terms = set(_tokens(query))
    df = {t: sum(1 for d in docs if t in d) for t in q_terms}
    n = len(docs)
    out: List[float] = []
    for d in docs:
        tf: Dict[str, int] = {}
        for t in d:
            if t in q_terms:
                tf[t] = tf.get(t, 0) + 1
        score = 0.0
        for t, f in tf.items():
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(d) / avg_len))
        out.append(score)
    return out


def best_chunks(query: str, chunks: List[Dict], limit: int) -> List[Dict]:
    """Die `limit` lexikalisch besten Chunks (BM25), Reihenfolge bleibt erhalten."""
    if len(chunks) <= limit:
        return chunks
    scores = lexical_scores(query, [c.get("content", "") for c in chunks])
    keep = set(sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)[:limit])
    return [c for i, c in enumerate(chunks) if i in keep]


def _sigmoid(x: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:  # pragma: no cover
        return 0.0 if x < 0 else 1.0


def canonical_url(url: str) -> str:
    """Kanonisiert eine URL fuer Dedup (Schema/Host klein, ohne Query/Fragment/Trailing-Slash)."""
    try:
        p = urlparse(url)
    except ValueError:
        return url
    scheme = (p.scheme or "http").lower()
    netloc = p.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = p.path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", "", ""))


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _shingles(text: str, k: int = 5) -> set:
    words = _norm_text(text).split()
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def dedup_chunks(chunks: List[Dict], threshold: float = 0.8) -> List[Dict]:
    """Entfernt Near-Duplicate-Chunks (Jaccard ueber 5-Wort-Shingles).

    Reine Python-Heuristik, keine Modelle noetig - gut testbar.
    Reihenfolge bleibt erhalten (erster Treffer gewinnt).
    """
    kept: List[Dict] = []
    kept_shingles: List[set] = []
    for ch in chunks:
        sh = _shingles(ch.get("content", ""))
        if any(_jaccard(sh, prev) >= threshold for prev in kept_shingles):
            continue
        kept.append(ch)
        kept_shingles.append(sh)
    return kept


# Schaltet QUALITY_PRIOR fuer den aktuellen Aufruf ab (Exa-Kompat, siehe exa._run_search).
_QUALITY_OFF: contextvars.ContextVar[bool] = contextvars.ContextVar("quality_off", default=False)


@contextlib.contextmanager
def quality_disabled():
    token = _QUALITY_OFF.set(True)
    try:
        yield
    finally:
        _QUALITY_OFF.reset(token)


def quality_active() -> bool:
    settings = get_settings()
    return settings.quality_prior and settings.quality_prior_weight > 0 and not _QUALITY_OFF.get()


def rerank(query: str, chunks: List[Dict], top_pool: Optional[int] = None) -> List[Dict]:
    """Rerankt Chunks gegen die Query. Setzt 'score' (0..1) je Chunk.

    Cross-Encoder ueber alle uebergebenen Chunks (Vorauswahl macht der Aufrufer
    mit best_chunks). Rueckgabe absteigend nach score sortiert.
    """
    if not chunks:
        return []
    settings = get_settings()
    # Seitentitel vor den Chunk: gibt dem Cross-Encoder den Kontext, worum es auf
    # der Seite geht (Contextual-Retrieval-Idee ohne LLM). Nur fuers Scoring.
    raw_scores = _cross_scores(
        query,
        [f"{ch.get('title', '')}. {ch.get('content', '')}".strip(". ") for ch in chunks],
        settings.rerank_max_length,
    )

    # Sortiert wird nach dem Roh-Logit: Relevante Chunks liegen alle bei ~10-11,
    # sigmoid(raw) waere dann ueberall 1.0000 und die Reihenfolge Zufall.
    # Der ausgegebene score ist gestreckt (Temperatur), damit er unterscheidet.
    temp = max(0.1, settings.score_temperature)
    prior_on = settings.source_prior
    language = lang_mod.detect(query) if prior_on else None
    quality_on = quality_active()
    scored: List[Dict] = []
    for ch, raw in zip(chunks, raw_scores):
        item = dict(ch)
        raw = float(raw)
        prior = source_prior(ch.get("url", ""), language) if prior_on else 0.0
        if prior_on:
            raw += prior
            raw += language_prior(ch.get("content", ""), language, settings.language_mismatch_penalty)
        if quality_on and prior == 0.0 and ch.get("url"):
            # SEO-/Antwortfarm-Muster (app/quality.py), nur fuer Quellen ohne festen Prior
            raw += quality_mod.seo_penalty(
                ch.get("url", ""), ch.get("title", ""), query, settings.quality_prior_weight
            )
        item["raw_score"] = raw
        item["score"] = round(_sigmoid(float(raw) / temp), 4)
        scored.append(item)

    scored.sort(key=rank_order, reverse=True)
    if top_pool is not None:
        scored = scored[:top_pool]
    return scored


# Quellen-Prior auf den Logit (additiv). Antwort-Foren, Social Media und Video-
# Seiten ohne Text landeten bei Faktenfragen oft vor Wikipedia; nichts wird
# entfernt, nur umsortiert. SOURCE_PRIOR=0 schaltet es ab.
_LOW_QUALITY = (
    "brainly.", "quizlet.com", "chegg.com", "coursehero.com", "answers.com", "tusclases.",
    "tiktok.com", "pinterest.", "facebook.com", "instagram.com", "x.com", "twitter.com",
    "linkedin.com/pulse", "youtube.com", "youtu.be", "reddit.com/r/", "quora.com",
    "gutefrage.net", "yahoo.com/question", "superprof.",
)
_HIGH_QUALITY_HOSTS = ("wikipedia.org", "britannica.com", "europa.eu")
_HIGH_PRIOR = 0.5
_HIGH_QUALITY_SUFFIX = (".gov", ".gov.uk", ".gouv.fr", ".gob.es", ".bund.de", ".edu", ".ac.uk")


def source_prior(url: str, language: Optional[str] = None) -> float:
    """-2.5 fuer schwache Quellen, +0.5 fuer Nachschlagewerke/Behoerden/Unis, sonst 0.
    +0.5 statt +1.0: Sweep 12.10.2026 (Replay dev 0.837 -> 0.861 mit RESULTS_PER_DOMAIN=1,
    test 0.822 -> 0.824); +1.0 schob Wikipedia/.gov vor die eigentliche Antwort- bzw.
    Programmseite. Bleibt als Gleichstand-Brecher und fuer die Wikipedia-Sprache.
    Wikipedia zaehlt nur in der Sprache der Frage (de.wikipedia bei deutscher
    Frage), damit nicht die englische Ausgabe vor der passenden landet."""
    low = url.lower()
    host = (urlparse(low).hostname or "").removeprefix("www.")
    if any(p in low for p in _LOW_QUALITY):
        return -2.5
    if host.endswith("wikipedia.org") and language:
        sub = host.split(".")[0]
        return _HIGH_PRIOR if sub in (language, "m") else -0.5
    if host.endswith(_HIGH_QUALITY_HOSTS) or host.endswith(_HIGH_QUALITY_SUFFIX):
        return _HIGH_PRIOR
    return 0.0


def language_prior(text: str, language: Optional[str], penalty: float) -> float:
    """Abzug fuer Abschnitte in einer anderen Sprache als die Frage.

    Der Cross-Encoder (mMARCO, ueberwiegend englisch trainiert) gibt englischen
    Texten deutlich hoehere Werte: Bei "Wie hoch ist die Zugspitze?" lag der
    englische Wikipedia-Abschnitt bei 9.06, der deutsche bei 6.43, obwohl beide die
    Antwort enthalten. Unklare Sprache -> kein Abzug."""
    if not language or penalty <= 0 or not text:
        return 0.0
    found = lang_mod.detect(text[:400])
    return -penalty if found and found != language else 0.0


def _rank_key(chunk: Dict) -> float:
    """Roh-Logit, falls vorhanden (feiner als der gerundete score)."""
    return chunk.get("raw_score", chunk.get("score", 0.0))


def rank_order(chunk: Dict) -> Tuple[float, str, str]:
    """Sortierschluessel mit festem Gleichstands-Bruch (absteigend sortieren).

    Der Cross-Encoder rechnet in fp16: Logits um 10 liegen auf einem Raster von
    1/128, gleiche Werte sind haeufig. Mit dem Logit allein entschied bei
    Gleichstand die Ankunftsreihenfolge (welche Engine, welche Seite zuerst da
    war), und dieselbe Anfrage lieferte bei Wiederholung eine andere Reihenfolge.
    Gleichstaende bricht hier die URL, dann der Text: unabhaengig vom Timing."""
    return (_rank_key(chunk), canonical_url(chunk.get("url", "")), chunk.get("content", ""))


def merge_by_source(
    ranked_chunks: List[Dict],
    chunks_per_source: int,
    max_results: int,
    candidates: Optional[List[Dict]] = None,
) -> List[Dict]:
    """Fasst rerankte Chunks pro Quelle zusammen -> eine result-Zeile je URL.

    - score der Quelle = hoechster Chunk-Score dieser Quelle.
    - content = die besten chunks_per_source Chunks, zusammengefuehrt.
    - mit `candidates` (Treffer mit Engine-Raengen): Reihenfolge per RRF aus
      Cross-Encoder-Rang und Engine-Rang (fuse_engine_rank).
    Rueckgabe: bis zu max_results Quellen, absteigend nach score bzw. Fusion.
    """
    by_url: Dict[str, Dict] = {}
    order: List[str] = []
    for ch in ranked_chunks:  # bereits nach score sortiert
        key = canonical_url(ch.get("url", ""))
        if key not in by_url:
            by_url[key] = {
                "url": ch.get("url", ""),
                "title": ch.get("title", ""),
                "chunks": [],
                "score": ch.get("score", 0.0),
                "rank": _rank_key(ch),
                "full_text": ch.get("full_text", ""),
            }
            order.append(key)
        bucket = by_url[key]
        if len(bucket["chunks"]) < chunks_per_source:
            bucket["chunks"].append(ch.get("content", ""))
        if not bucket["full_text"] or len(ch.get("full_text", "")) > len(bucket["full_text"]):
            bucket["full_text"] = ch.get("full_text", "")
        bucket["score"] = max(bucket["score"], ch.get("score", 0.0))
        bucket["rank"] = max(bucket["rank"], _rank_key(ch))

    results: List[Dict] = []
    for key in order:
        b = by_url[key]
        results.append(
            {
                "url": b["url"],
                "title": b["title"],
                "content": _fill_content(b["chunks"], b["full_text"]),
                "score": round(b["score"], 4),
                "raw_content": b["full_text"] or None,  # None = Seite nicht geladen
                "_rank": b["rank"],
            }
        )
    results.sort(key=lambda r: (r["_rank"], canonical_url(r["url"])), reverse=True)
    ce = [r.pop("_rank") for r in results]
    settings = get_settings()
    if candidates:
        results = fuse_engine_rank(
            results, candidates, settings.fusion_weight, settings.fusion_k,
            ce_scores=ce, trust_min=settings.fusion_trust_min,
        )
    return diversify(results, settings.results_per_domain)[:max_results]


CONTENT_MIN_CHARS = 1000
CONTENT_FILL_CHARS = 1300


def _fill_content(chunks: List[str], full_text: str) -> str:
    """Abschnitte (bester zuerst) zusammenfuehren. Kleine Abschnitte ranken genauer,
    ergeben aber knappen content: Ist er kuerzer als CONTENT_MIN_CHARS, wird der
    Text direkt nach dem besten Abschnitt angehaengt (bis ~CONTENT_FILL_CHARS),
    damit der Treffer lesbaren Zusammenhang hat. Der Anfang bleibt unveraendert."""
    content = " [...] ".join(chunks).strip()
    if not chunks or not full_text or len(content) >= CONTENT_MIN_CHARS:
        return content
    flat = " ".join(full_text.split())
    pos = flat.find(chunks[0])
    if pos < 0:
        return content
    tail = flat[pos + len(chunks[0]) : pos + len(chunks[0]) + CONTENT_FILL_CHARS - len(content)].strip()
    if not tail or tail in content:
        return content
    cut = tail.rfind(" ")
    tail = tail[:cut] if cut > 0 and len(tail) > 40 else tail
    if len(chunks) == 1:
        return f"{content} {tail}".strip()
    return f"{content} [...] {tail}".strip()


def engine_ranks(cand: Dict) -> Dict[str, int]:
    """Platz je Engine eines Kandidaten ({"bing": 2, "yahoo": 5}); Treffer aus dem
    einfachen Weg tragen nur engine + engine_rank."""
    ranks = cand.get("engine_ranks")
    if ranks:
        return ranks
    rank = cand.get("engine_rank")
    return {cand.get("engine") or "?": rank} if rank else {}


def engine_trust(
    results: List[Dict], ranks: List[Dict[str, int]], ce_scores: List[float], trust_min: float
) -> Dict[str, float]:
    """Vertrauen je Engine fuer diese Anfrage: Anteil ihrer Treffer, die der
    Cross-Encoder fuer relevant haelt (Logit >= trust_min). Versteht eine Engine
    die Anfrage falsch (Bing liefert bei "international youth summit 2027 all
    expenses paid" Woerterbuch- und Nachrichtenseiten zu "international"), zieht
    die Fusion ihre Plaetze sonst ueber passende Treffer der anderen Engine."""
    stats: Dict[str, List[int]] = {}
    for engines, score in zip(ranks, ce_scores):
        for engine in engines:
            good_total = stats.setdefault(engine, [0, 0])
            good_total[0] += score >= trust_min
            good_total[1] += 1
    return {e: good / total for e, (good, total) in stats.items()}


def fuse_engine_rank(
    results: List[Dict],
    candidates: List[Dict],
    weight: float,
    k: int,
    ce_scores: Optional[List[float]] = None,
    trust_min: Optional[float] = None,
) -> List[Dict]:
    """RRF aus Cross-Encoder-Rang (Reihenfolge von `results`) und Engine-Rang.

    Engine-Rang = Reihenfolge nach Summe 1/Platz ueber Bing/Yahoo/Yandex (Seite 2
    zaehlt ab Platz 11). Der Cross-Encoder allein holt themennahe, aber schwache
    Seiten nach vorn; die Engines kennen Linkautoritaet. Idee aus dem Serper-Nachbau
    (mrr 0.16 -> 0.29). Replay dev offline: hist_mrr 0.24 -> 0.31, mrr 0.947 -> 0.96;
    Plateau fuer k 20-30 und Gewicht 0.35-0.7, Engine-Uebereinstimmung als Bonus
    schadet (0.31 -> 0.29). weight <= 0 schaltet ab.
    Mit ce_scores (Cross-Encoder-Logit je Ergebnis) und trust_min zaehlt jede Engine
    nur mit ihrem Vertrauen fuer diese Anfrage (engine_trust). Fehleranalyse
    12.10.2026: Bing-Fehltreffer (IMDb "Youth", dwds "international") standen vor
    Gold-Quellen; Replay dev hist_mrr 0.276 -> 0.306."""
    if weight <= 0 or len(results) < 2:
        return results
    by_key = {canonical_url(c.get("url", "")): engine_ranks(c) for c in candidates}
    ranks = [by_key.get(canonical_url(x["url"]), {}) for x in results]
    trust: Dict[str, float] = {}
    if ce_scores is not None and trust_min is not None and len(ce_scores) == len(results):
        trust = engine_trust(results, ranks, ce_scores, trust_min)
    eng = [sum(trust.get(e, 1.0) / max(r, 1) for e, r in rk.items()) for rk in ranks]
    if not any(eng):
        return results
    eng_order = sorted(range(len(results)), key=lambda i: -eng[i])  # stabil: CE-Reihenfolge bei Gleichstand
    eng_rank = {i: pos for pos, i in enumerate(eng_order)}
    fused = sorted(range(len(results)), key=lambda i: -(1 / (k + i) + weight / (k + eng_rank[i])))
    return [results[i] for i in fused]


def _site(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def diversify(results: List[Dict], per_domain: int) -> List[Dict]:
    """Je Domain die ersten `per_domain` Treffer an ihrem Platz, weitere rutschen
    in ihrer Reihenfolge ans Ende; nichts wird verworfen. Mehr verschiedene
    Quellen weiter oben (beim Exa-Nachbau gemessen: mrr 0.23 -> 0.27). 0 = aus."""
    if per_domain <= 0:
        return results
    seen: Dict[str, int] = {}
    front: List[Dict] = []
    back: List[Dict] = []
    for r in results:
        site = _site(r.get("url", ""))
        seen[site] = seen.get(site, 0) + 1
        (front if seen[site] <= per_domain else back).append(r)
    return front + back
