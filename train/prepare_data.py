#!/usr/bin/env python3
"""Oeffentliche Trainingsdaten fuer den Reranker laden und als Paare ablegen.

Laeuft auf dem Trainings-Laptop, nicht auf dem Mac (der Mac-Speicher ist knapp).
Alles wird gestreamt: Grosse Dateien landen nie ganz auf der Platte, im Speicher
bleiben nur die Texte, die wirklich gebraucht werden (16 GB RAM reichen).

Quellen (alle oeffentlich, ohne Login):
  msmarco      sentence-transformers/msmarco, Konfig bert-ensemble-mse
               (Anfrage-ID, Passagen-ID, Lehrer-Score eines BERT-Ensembles)  -> distill
  msmarco-hard sentence-transformers/msmarco-co-condenser-margin-mse-sym-mnrl-mean-v1,
               Konfig triplet-hard-ids (harte Negative, per Cross-Encoder gefiltert) -> labeled + dev
  mmarco       unicamp-dl/mmarco (Google-Uebersetzung von MS MARCO, gleiche IDs):
               Texte fuer en/de/es/fr. Lehrer-Score der englischen Fassung wird uebernommen.
  germandpr    lopozz/GermanDPRRetrieval (GermanDPR im BEIR-Format, Train-Teil) -> labeled + dev
  germanrag    DiscoResearch/germanrag (aus GermanDPR, mit Kontext-Listen)      -> labeled + dev
  miracl       nthakur/miracl-monolingual-sft (MIRACL-Train mit Passagen, en/es/fr) -> labeled + dev

Jede MS-MARCO-Anfrage bekommt genau eine Sprache (deterministisch), dev-Anfragen
(0,4 % per Hash) landen nie im Training. Anfragen aus eval/ werden herausgefiltert.

Ausgabe in train/data/ (gitignored):
  distill.jsonl   query, passage, score (roher Lehrer-Score), lang, src
  labeled.jsonl   query, passage, label (1/0), lang, src
  dev.jsonl       query, positive[], negative[], lang, src (nur oeffentliche Daten)
  manifest.json   Mengen je Quelle/Sprache, Einstellungen, fehlgeschlagene Quellen
  parts/          Zwischenstaende je Quelle (Neustart ueberspringt fertige Quellen)

  python train/prepare_data.py                    # Standard: ca. 1,5 Mio. Paare
  python train/prepare_data.py --max-pairs 2000000
  python train/prepare_data.py --quick            # Rauchtest, ca. 55 MB Netz, kaum Platte
  python train/prepare_data.py --sources miracl --quick   # nur eine Quelle pruefen
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.client
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import datautil as du  # noqa: E402

DATA = HERE / "data"
PARTS = DATA / "parts"
LOGS = HERE / "logs"
HF = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
MMARCO = f"{HF}/datasets/unicamp-dl/mmarco/resolve/main/data/google"
LANG_NAME = {"en": "english", "de": "german", "es": "spanish", "fr": "french"}

DS_MSMARCO = "sentence-transformers/msmarco"
DS_HARD = "sentence-transformers/msmarco-co-condenser-margin-mse-sym-mnrl-mean-v1"
DS_GERMANDPR = "lopozz/GermanDPRRetrieval"
DS_GERMANRAG = "DiscoResearch/germanrag"
DS_MIRACL = "nthakur/miracl-monolingual-sft"

ALL_SOURCES = ["msmarco", "germandpr", "germanrag", "miracl"]
SHARES = {"distill": 0.75, "hard": 0.20}  # Rest kommt aus den kleinen Quellen (alles, was sie haben)
LANG_WEIGHTS = {"en": 0.4, "de": 0.2, "es": 0.2, "fr": 0.2}

LICENSES = {
    DS_MSMARCO: "MS MARCO: nur nicht-kommerzielle Forschung",
    DS_HARD: "MS MARCO: nur nicht-kommerzielle Forschung",
    "unicamp-dl/mmarco": "MS MARCO-Bedingungen (nicht-kommerziell), Uebersetzung Apache 2.0",
    DS_GERMANDPR: "GermanDPR: CC BY 4.0",
    DS_GERMANRAG: "CC BY 4.0",
    DS_MIRACL: "MIRACL: Apache 2.0",
}

_LOG_FILE = None


def log(msg: str) -> None:
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    if _LOG_FILE:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def write_jsonl_atomic(path: Path, rows) -> int:
    tmp = path.with_suffix(path.suffix + ".tmp")
    n = 0
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    os.replace(tmp, path)
    return n


def read_jsonl(path: Path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def stream(name: str, config=None, split="train"):
    from datasets import load_dataset  # noqa: PLC0415 (erst hier, --help geht ohne)

    return load_dataset(name, config, split=split, streaming=True)


def http_lines(url: str, max_bytes: int = 0, retries: int = 10):
    """Zeilen einer grossen Datei per HTTP, ohne sie zu speichern. Setzt nach
    Verbindungsabbruch per Range-Anfrage an derselben Stelle fort."""
    offset, rest, attempt, next_report = 0, b"", 0, 512 << 20
    while True:
        headers = {"User-Agent": "tavily-clone-train/1.0"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as resp:
                if offset and resp.status != 206:
                    raise RuntimeError("Server unterstuetzt keine Range-Anfragen, Abbruch")
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        if rest.strip():
                            yield rest
                        return
                    offset += len(chunk)
                    attempt = 0
                    lines, rest = du.split_lines(rest + chunk)
                    yield from lines
                    if offset >= next_report:
                        log(f"  ... {du.human_bytes(offset)} gelesen")
                        next_report += 512 << 20
                    if max_bytes and offset >= max_bytes:
                        return
        except (OSError, http.client.HTTPException) as exc:
            attempt += 1
            if attempt > retries:
                raise
            log(f"  Verbindung abgebrochen ({exc.__class__.__name__}: {exc}), Versuch {attempt}/{retries} ab {du.human_bytes(offset)}")
            time.sleep(min(120, 10 * attempt))


# --------------------------------------------------------------------------- MS MARCO


def stage_msmarco_select(args, q) -> None:
    """IDs auswaehlen: harte Negative (labeled + dev) und Lehrer-Scores (distill)."""
    out = PARTS / "msmarco_select.jsonl"
    if out.exists():
        log(f"msmarco: Auswahl schon vorhanden ({out.name}), uebersprungen")
        return
    lw = args.lang_weights
    pid_max = args.quick_pid_max  # nur --quick: Passagen aus dem kurzen Sammlungs-Anfang

    def ok(pid: int) -> bool:
        return not pid_max or pid <= pid_max

    def rows():
        need_hard = {lang: q[("hard", lang)] for lang in lw}
        need_dev = {lang: args.dev_per_lang for lang in lw}
        log(f"msmarco-hard: {DS_HARD} [triplet-hard-ids], Ziel {sum(need_hard.values())} Paare")
        cur, negs, seen = None, [], 0

        def flush(qid, pos, negs):
            lang = du.assign_lang(str(qid), lw)
            if du.is_dev(f"msmarco:{qid}"):
                if need_dev[lang] > 0:
                    need_dev[lang] -= 1
                    yield {"kind": "dev", "lang": lang, "qid": qid, "pid": pos, "label": 1}
                    for n in negs[:30]:
                        yield {"kind": "dev", "lang": lang, "qid": qid, "pid": n, "label": 0}
                return
            if need_hard[lang] <= 0:
                return
            chosen = du.pick_negatives([n for n in negs if ok(n)], args.hard_negs, f"{qid}:{pos}")
            need_hard[lang] -= int(ok(pos)) + len(chosen)
            if ok(pos):
                yield {"kind": "hard", "lang": lang, "qid": qid, "pid": pos, "label": 1}
            for n in chosen:
                yield {"kind": "hard", "lang": lang, "qid": qid, "pid": n, "label": 0}

        for row in stream(DS_HARD, "triplet-hard-ids"):
            seen += 1
            if args.hard_rows_limit and seen > args.hard_rows_limit:
                break
            key = (int(row["query"]), int(row["positive"]))
            if key != cur:
                if cur:
                    yield from flush(cur[0], cur[1], negs)
                cur, negs = key, []
                if all(v <= 0 for v in need_hard.values()) and all(v <= 0 for v in need_dev.values()):
                    break
            negs.append(int(row["negative"]))
        if cur:
            yield from flush(cur[0], cur[1], negs)
        log(f"msmarco-hard: {seen} Zeilen gelesen")

        need = {lang: q[("distill", lang)] for lang in lw}
        log(f"msmarco: {DS_MSMARCO} [bert-ensemble-mse], Ziel {sum(need.values())} Paare")
        seen = 0
        for row in stream(DS_MSMARCO, "bert-ensemble-mse"):
            seen += 1
            if args.distill_rows_limit and seen > args.distill_rows_limit:
                break
            qid = int(row["query_id"])
            if du.is_dev(f"msmarco:{qid}"):
                continue
            lang = du.assign_lang(str(qid), lw)
            if need[lang] <= 0:
                if all(v <= 0 for v in need.values()):
                    break
                continue
            pid = int(row["passage_id"])
            if not ok(pid):
                continue
            need[lang] -= 1
            yield {"kind": "distill", "lang": lang, "qid": qid, "pid": pid,
                   "score": float(row["score"])}
            if seen % 1_000_000 == 0:
                log(f"  ... {seen} Zeilen, offen: {need}")
        log(f"msmarco: {seen} Zeilen gelesen")

    n = write_jsonl_atomic(out, rows())
    log(f"msmarco: {n} IDs ausgewaehlt -> {out.name}")


def stage_msmarco_join(args, lang: str) -> None:
    """Texte fuer eine Sprache aus mMARCO holen (Anfragen + Passagen) und Paare schreiben."""
    out = PARTS / f"msmarco_{lang}.jsonl"
    if out.exists():
        log(f"mmarco-{lang}: schon vorhanden, uebersprungen")
        return
    sel = [r for r in read_jsonl(PARTS / "msmarco_select.jsonl") if r["lang"] == lang]
    if not sel:
        log(f"mmarco-{lang}: nichts ausgewaehlt")
        write_jsonl_atomic(out, [])
        return
    qids = {r["qid"] for r in sel}
    pids = {r["pid"] for r in sel}
    name = LANG_NAME[lang]

    log(f"mmarco-{lang}: {len(qids)} Anfragen laden")
    queries = {}
    for line in http_lines(f"{MMARCO}/queries/train/{name}_queries.train.tsv"):
        parsed = du.parse_tsv_id_text(line)
        if parsed and parsed[0] in qids:
            queries[parsed[0]] = parsed[1]

    log(f"mmarco-{lang}: {len(pids)} Passagen aus der Sammlung (ca. 3,5 GB, gestreamt) suchen")
    passages = {}
    for line in http_lines(f"{MMARCO}/collections/{name}_collection.tsv", max_bytes=args.collection_max_bytes):
        parsed = du.parse_tsv_id_text(line)
        if parsed and parsed[0] in pids:
            passages[parsed[0]] = parsed[1]
            if len(passages) == len(pids):
                break

    src = f"msmarco-{lang}"

    def rows():
        for r in sel:
            qt, pt = queries.get(r["qid"]), passages.get(r["pid"])
            if not qt or not pt:
                continue
            base = {"lang": lang, "src": src, "qkey": f"msmarco:{r['qid']}", "query": qt, "passage": pt}
            if r["kind"] == "distill":
                yield {**base, "split": "train", "kind": "distill", "score": r["score"]}
            elif r["kind"] == "hard":
                yield {**base, "split": "train", "kind": "labeled", "label": r["label"]}
            else:
                yield {**base, "split": "dev", "kind": "labeled", "label": r["label"]}

    n = write_jsonl_atomic(out, rows())
    log(f"mmarco-{lang}: {n} von {len(sel)} Zeilen mit Text (Anfragen {len(queries)}/{len(qids)}, "
        f"Passagen {len(passages)}/{len(pids)})")


# --------------------------------------------------------------------------- kleine Quellen


def labeled_row(src, lang, query, passage, label):
    qkey = "text:" + du.normalize_query(query)
    split = "dev" if du.is_dev(qkey) else "train"
    return {"split": split, "kind": "labeled", "lang": lang, "src": src, "qkey": qkey,
            "query": query, "passage": passage, "label": label}


def stage_germandpr(args) -> None:
    out = PARTS / "germandpr.jsonl"
    if out.exists():
        log("germandpr: schon vorhanden, uebersprungen")
        return
    log(f"germandpr: {DS_GERMANDPR} (de-corpus, de-queries, de-qrels, Train-Teil)")
    corpus = {}
    for r in stream(DS_GERMANDPR, "de-corpus"):
        corpus[r["_id"]] = r["text"]
    queries = {r["_id"]: r["text"] for r in stream(DS_GERMANDPR, "de-queries")}
    qrels = {}
    for r in stream(DS_GERMANDPR, "de-qrels"):
        if float(r["score"]) > 0:
            qrels.setdefault(r["query-id"], []).append(r["corpus-id"])
    # Passagen "c12-0", "c12-1" gehoeren zur Frage von "c12" (die harten Negative aus GermanDPR).
    family = {}
    for cid in corpus:
        if "-" in cid:
            family.setdefault(cid.rsplit("-", 1)[0], []).append(cid)
    ids = sorted(corpus)

    def rows():
        for qid, pos_ids in qrels.items():
            qt = queries.get(qid)
            if not qt:
                continue
            negs = [c for p in pos_ids for c in family.get(p, []) if c not in pos_ids]
            rnd = ids[int(du.stable_unit("rnd:" + qid) * len(ids))]
            if rnd not in pos_ids:
                negs.append(rnd)
            for p in pos_ids:
                if p in corpus:
                    yield labeled_row("germandpr", "de", qt, corpus[p], 1)
            for n in du.pick_negatives(negs, 4, qid):
                yield labeled_row("germandpr", "de", qt, corpus[n], 0)

    n = write_jsonl_atomic(out, rows())
    log(f"germandpr: {n} Paare ({len(qrels)} Fragen, {len(corpus)} Passagen)")


def stage_germanrag(args) -> None:
    out = PARTS / "germanrag.jsonl"
    if out.exists():
        log("germanrag: schon vorhanden, uebersprungen")
        return
    log(f"germanrag: {DS_GERMANRAG}")

    def rows():
        for i, r in enumerate(stream(DS_GERMANRAG)):
            if args.small_rows_limit and i >= args.small_rows_limit:
                break
            pos = r.get("positive_ctx_idx", -1)
            for j, ctx in enumerate(r.get("contexts") or []):
                if ctx and r.get("question"):
                    yield labeled_row("germanrag", "de", r["question"], ctx, 1 if j == pos else 0)

    n = write_jsonl_atomic(out, rows())
    log(f"germanrag: {n} Paare")


def stage_miracl(args) -> None:
    out = PARTS / "miracl.jsonl"
    if out.exists():
        log("miracl: schon vorhanden, uebersprungen")
        return
    langs = set(args.langs)
    log(f"miracl: {DS_MIRACL} (Sprachen {sorted(langs)}; Deutsch hat in MIRACL keinen Train-Teil)")
    stats = {"rows": 0, "kept": 0, "bad": 0}

    def rows():
        for r in stream(DS_MIRACL):
            stats["rows"] += 1
            if args.small_rows_limit and stats["rows"] > args.small_rows_limit:
                break
            if r.get("language") not in langs:
                continue
            msgs = r.get("messages") or []
            question, passages = du.parse_miracl_prompt(msgs[0]["content"] if msgs else "")
            if not question or not passages:
                stats["bad"] += 1
                continue
            stats["kept"] += 1
            pos, neg = set(r.get("positive_ids") or []), set(r.get("negative_ids") or [])
            for pid, text in passages.items():
                if pid in pos:
                    yield labeled_row("miracl", r["language"], question, text, 1)
                elif pid in neg:
                    yield labeled_row("miracl", r["language"], question, text, 0)

    n = write_jsonl_atomic(out, rows())
    log(f"miracl: {n} Paare aus {stats['kept']} Fragen ({stats['rows']} Zeilen gelesen, {stats['bad']} unlesbar)")


# --------------------------------------------------------------------------- Zusammenfuehren


def eval_queries() -> set:
    """Alle Anfragen aus den eigenen Pruefungen: duerfen nie ins Training."""
    out = set()
    for p in (ROOT / "eval").glob("queries*.jsonl"):
        for r in read_jsonl(p):
            if r.get("query"):
                out.add(du.normalize_query(r["query"]))
    tasks = Path.home() / ".claude/skills/research-agent/evals/tasks.json"
    if tasks.exists():
        for t in json.loads(tasks.read_text(encoding="utf-8")).get("tasks", []):
            for field in ("queries", "queries_niche"):
                for qq in t.get(field, []):
                    out.add(du.normalize_query(qq))
    return out


def merge(args, failed) -> dict:
    blocked = eval_queries()
    counts, dev_rows, dropped = {}, [], 0
    files = sorted(p for p in PARTS.glob("*.jsonl") if p.name != "msmarco_select.jsonl")
    tmp_d, tmp_l = DATA / "distill.jsonl.tmp", DATA / "labeled.jsonl.tmp"
    with open(tmp_d, "w", encoding="utf-8") as fd, open(tmp_l, "w", encoding="utf-8") as fl:
        for p in files:
            for r in read_jsonl(p):
                if du.normalize_query(r["query"]) in blocked:
                    dropped += 1
                    continue
                if r["split"] == "dev":
                    dev_rows.append(r)
                    continue
                key = f"{r['src']}/{r['kind']}"
                counts[key] = counts.get(key, 0) + 1
                if r["kind"] == "distill":
                    fd.write(json.dumps({"query": r["query"], "passage": r["passage"], "score": r["score"],
                                         "lang": r["lang"], "src": r["src"]}, ensure_ascii=False) + "\n")
                else:
                    fl.write(json.dumps({"query": r["query"], "passage": r["passage"], "label": r["label"],
                                         "lang": r["lang"], "src": r["src"]}, ensure_ascii=False) + "\n")
    os.replace(tmp_d, DATA / "distill.jsonl")
    os.replace(tmp_l, DATA / "labeled.jsonl")

    per_src = {}
    groups = []
    for g in du.group_dev(dev_rows):
        if per_src.get(g["src"], 0) >= args.dev_max_per_src:
            continue
        per_src[g["src"]] = per_src.get(g["src"], 0) + 1
        groups.append(g)
    write_jsonl_atomic(DATA / "dev.jsonl", groups)

    manifest = {
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "quick": args.quick,
        "max_pairs": args.max_pairs,
        "langs": args.langs,
        "train_pairs": counts,
        "train_total": sum(counts.values()),
        "dev_groups": per_src,
        "dropped_eval_overlap": dropped,
        "failed_sources": failed,
        "datasets": LICENSES,
        "dev_fraction": du.DEV_FRACTION,
    }
    (DATA / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    return manifest


def main() -> None:
    global _LOG_FILE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-pairs", type=int, default=1_500_000, help="Ziel fuer MS MARCO/mMARCO (kleine Quellen kommen dazu)")
    ap.add_argument("--quick", action="store_true", help="Rauchtest: nur Englisch + germanrag, kleine Ausschnitte")
    ap.add_argument("--sources", default=",".join(ALL_SOURCES), help=f"Auswahl aus {ALL_SOURCES}")
    ap.add_argument("--langs", default="en,de,es,fr")
    ap.add_argument("--hard-negs", type=int, default=3, help="harte Negative je Anfrage")
    ap.add_argument("--dev-per-lang", type=int, default=250, help="dev-Anfragen je Sprache aus MS MARCO")
    ap.add_argument("--dev-max-per-src", type=int, default=300)
    ap.add_argument("--fresh", action="store_true", help="Zwischenstaende in train/data/parts loeschen und neu laden")
    args = ap.parse_args()

    args.sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = set(args.sources) - set(ALL_SOURCES)
    if unknown:
        ap.error(f"unbekannte Quellen: {sorted(unknown)}")
    args.langs = [x.strip() for x in args.langs.split(",") if x.strip()]
    args.hard_rows_limit = args.distill_rows_limit = args.small_rows_limit = args.collection_max_bytes = 0
    args.quick_pid_max = 0
    if args.quick:
        if "--max-pairs" not in sys.argv:
            args.max_pairs = 10_000
        if "--langs" not in sys.argv:
            args.langs = ["en"]
        if "--sources" not in sys.argv:
            args.sources = ["msmarco", "germanrag"]
        args.dev_per_lang = min(args.dev_per_lang, 20)
        args.hard_rows_limit = 30_000          # ids, ca. 0,3 MB
        args.distill_rows_limit = 300_000      # ids + Score, wenige MB
        args.collection_max_bytes = 8 << 20    # nur die ersten 8 MB je Sammlung
        args.small_rows_limit = 400
        du.DEV_FRACTION = 0.05                 # sonst gaebe es im Rauchtest keine dev-Gruppen
    args.lang_weights = {k: v for k, v in LANG_WEIGHTS.items() if k in args.langs}

    DATA.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    _LOG_FILE = LOGS / "prepare.log"
    if args.fresh and PARTS.exists():
        for p in PARTS.glob("*"):
            p.unlink()
    PARTS.mkdir(parents=True, exist_ok=True)
    marker = PARTS / "settings.json"
    settings = {"quick": args.quick, "max_pairs": args.max_pairs, "langs": args.langs, "sources": args.sources}
    if marker.exists() and json.loads(marker.read_text(encoding="utf-8")) != settings:
        log("Hinweis: Zwischenstaende stammen aus einem Lauf mit anderen Einstellungen. "
            "Fuer einen sauberen Neuanfang --fresh angeben.")
    marker.write_text(json.dumps(settings), encoding="utf-8")

    q = du.quotas(args.max_pairs, SHARES, args.lang_weights)
    log(f"Start: Quellen {args.sources}, Sprachen {args.langs}, max_pairs {args.max_pairs}, quick={args.quick}")
    t0 = time.time()
    failed = {}

    def run(name, fn, *a):
        try:
            fn(args, *a)
        except Exception as exc:  # noqa: BLE001 - eine kaputte Quelle darf die Nacht nicht beenden
            failed[name] = f"{exc.__class__.__name__}: {exc}"
            log(f"FEHLER in {name}: {failed[name]} -> Quelle uebersprungen")

    if "msmarco" in args.sources and args.quick and not (PARTS / "msmarco_select.jsonl").exists():
        # Rauchtest: nur Passagen aus den ersten MB der Sammlung koennen gefunden werden,
        # also vorher die hoechste ID darin bestimmen und nur solche Paare auswaehlen.
        last = 0
        for line in http_lines(f"{MMARCO}/collections/{LANG_NAME[args.langs[0]]}_collection.tsv",
                               max_bytes=args.collection_max_bytes):
            parsed = du.parse_tsv_id_text(line)
            last = parsed[0] if parsed else last
        args.quick_pid_max = last
        log(f"quick: nur Passagen-IDs bis {last}")
    if "msmarco" in args.sources:
        run("msmarco-select", lambda a: stage_msmarco_select(a, q))
        if (PARTS / "msmarco_select.jsonl").exists():
            for lang in args.langs:
                run(f"mmarco-{lang}", stage_msmarco_join, lang)
    if "germandpr" in args.sources and "de" in args.langs:
        run("germandpr", stage_germandpr)
    if "germanrag" in args.sources:
        run("germanrag", stage_germanrag)
    if "miracl" in args.sources:
        run("miracl", stage_miracl)

    m = merge(args, failed)
    log(f"Fertig in {(time.time() - t0) / 60:.1f} min: {m['train_total']} Trainingspaare, "
        f"dev-Gruppen {m['dev_groups']}, ausgelassen wegen eval-Ueberschneidung {m['dropped_eval_overlap']}")
    log(f"Mengen: {m['train_pairs']}")
    if failed:
        log(f"Fehlgeschlagen: {failed}")
    if m["train_total"] == 0:
        sys.exit("Keine Trainingsdaten erzeugt, siehe train/logs/prepare.log")


if __name__ == "__main__":
    main()
