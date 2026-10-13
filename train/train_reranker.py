#!/usr/bin/env python3
"""Reranker (Cross-Encoder) auf den Daten aus prepare_data.py nachtrainieren.

Ausgangsmodell ist dasselbe wie im Dienst (app/config.py, RERANKER_MODEL), damit das
Ergebnis ohne Codeaenderung geladen werden kann: RERANKER_MODEL=models/<name>-<datum>.

Verlust
  distill.jsonl  MSELoss auf Lehrer-Scores. Die Scores werden vorher per Mittelwert und
                 Streuung auf die Skala des Ausgangsmodells gebracht (moment matching),
                 damit feste Schwellen im Dienst (Sigmoid, MIN_RESULT_SCORE) gueltig bleiben.
  labeled.jsonl  BinaryCrossEntropyLoss auf 1/0-Labels (harte Negative, GermanDPR, MIRACL).
  Beide Mengen im selben Lauf, Batches anteilig gemischt.

Pruefung waehrend des Trainings
  dev.jsonl (nur oeffentliche Daten, nie die eigenen Pruefungen): NDCG@10 je Sprache,
  Mittelwert ist das Auswahlmass. Vor dem Training wird das Ausgangsmodell gemessen.
  Bestes Modell liegt jederzeit unter train/checkpoints/<name>/best/.
  Frueher Abbruch, wenn --patience Messungen hintereinander nicht besser sind.

Zwischenstaende
  train/checkpoints/<name>/checkpoint-*  (Modell + Optimierer, mindestens stuendlich)
  --resume setzt nach Absturz oder Neustart an der letzten Stelle fort.

Ergebnis
  models/<name>-<YYYY-MM-DD>/  Modell im transformers-Format + model_card.json
  train/logs/train-*.log, train/logs/metrics.jsonl

  python train/train_reranker.py                # volle Nacht, CUDA, fp16
  python train/train_reranker.py --resume       # nach Abbruch weiter
  python train/train_reranker.py --status       # Stand anzeigen
  python train/train_reranker.py --quick        # Rauchtest auf CPU/MPS, wenige Schritte
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import os
import platform
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import datautil as du  # noqa: E402

DATA = HERE / "data"
LOGS = HERE / "logs"
CKPT = HERE / "checkpoints"
MODELS = ROOT / "models"
# Muss zum Dienst passen: app/config.py (RERANKER_MODEL, RERANK_MAX_LENGTH).
BASE_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
MAX_LENGTH = 256

log = logging.getLogger("train")


def setup_logging(quick: bool) -> Path:
    LOGS.mkdir(parents=True, exist_ok=True)
    path = LOGS / f"train-{dt.datetime.now():%Y%m%d-%H%M}{'-quick' if quick else ''}.log"
    fmt = logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(path, encoding="utf-8")):
        h.setFormatter(fmt)
        root.addHandler(h)
    return path


def read_jsonl(path: Path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def show_status(name: str) -> None:
    run_dir = CKPT / name
    metrics = LOGS / "metrics.jsonl"
    done = LOGS / f"DONE-{name}.json"
    if done.exists():
        info = json.loads(done.read_text(encoding="utf-8"))
        print(f"FERTIG: {info['model_dir']}")
        print(f"dev NDCG@10 vorher {info.get('baseline')}, bestes {info.get('best')}, besser: {info.get('better')}")
        return
    if not metrics.exists():
        print("Noch keine Messwerte (Training nicht gestartet oder noch in der Datenvorbereitung).")
        print("Datenvorbereitung: train/logs/prepare.log")
        return
    rows = [json.loads(x) for x in open(metrics, encoding="utf-8") if x.strip()]
    last_train = next((r for r in reversed(rows) if "loss" in r), None)
    evals = [r for r in rows if "dev_ndcg10" in r]
    if last_train:
        pct = 100.0 * last_train["step"] / max(1, last_train.get("max_steps", 1))
        print(f"Schritt {last_train['step']}/{last_train.get('max_steps')} ({pct:.1f} %), "
              f"loss {last_train['loss']:.4f}, Stand {last_train['time']}")
        if last_train.get("eta_min") is not None:
            print(f"Restzeit ca. {last_train['eta_min']:.0f} min")
    for e in evals[-5:]:
        print(f"dev NDCG@10 bei Schritt {e['step']}: {e['dev_ndcg10']:.4f}  {e.get('per_lang', {})}")
    best = run_dir / "best.json"
    if best.exists():
        b = json.loads(best.read_text(encoding="utf-8"))
        print(f"bestes bisher: {b.get('best')} bei Schritt {b.get('best_step')} (vorher {b.get('baseline')})")


def pick_device() -> str:
    import torch  # noqa: PLC0415

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def dev_score(metrics: dict) -> tuple[float, dict]:
    """Mittel der NDCG@10-Werte aller dev-Sprachen aus einem Evaluator-Ergebnis."""
    per = {}
    for k, v in metrics.items():
        if k.endswith("_ndcg@10"):
            lang = k.split("_ndcg@10")[0].replace("eval_", "").replace("dev-", "")
            per[lang] = round(float(v), 4)
    if not per:
        return float("nan"), per
    return round(sum(per.values()) / len(per), 4), per


def build_callbacks(args, run_dir: Path, max_steps_hint: int):
    from transformers import TrainerCallback  # noqa: PLC0415

    metrics_path = LOGS / "metrics.jsonl"

    def write_metric(row: dict) -> None:
        row = {"time": f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}", **row}
        with open(metrics_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    class TimedSave(TrainerCallback):
        """Mindestens alle --save-minutes einen Zwischenstand (mit Messung) schreiben."""

        def __init__(self):
            self.last = time.time()

        def on_step_end(self, a, state, control, **kw):
            if time.time() - self.last >= args.save_minutes * 60:
                control.should_save = True
                control.should_evaluate = True
            return control

        def on_save(self, a, state, control, **kw):
            self.last = time.time()
            log.info(f"Zwischenstand gespeichert bei Schritt {state.global_step}")

    class Progress(TrainerCallback):
        def __init__(self):
            self.t0 = time.time()
            self.step0 = None

        def on_log(self, a, state, control, logs=None, **kw):
            if not logs or "loss" not in logs:
                return
            if self.step0 is None:
                self.step0 = state.global_step
            done = state.global_step - self.step0
            rate = done / max(1e-9, time.time() - self.t0)
            eta = (state.max_steps - state.global_step) / rate / 60 if rate > 0 else None
            write_metric({"step": state.global_step, "max_steps": state.max_steps, "loss": float(logs["loss"]),
                          "lr": float(logs.get("learning_rate", 0)), "epoch": float(logs.get("epoch", 0)),
                          "eta_min": round(eta, 1) if eta is not None else None})

    class BestAndStop(TrainerCallback):
        """Bestes Modell nach dev sichern, frueh abbrechen ohne Fortschritt."""

        def __init__(self):
            self.path = run_dir / "best.json"
            self.state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

        def save_state(self):
            self.path.write_text(json.dumps(self.state, indent=1), encoding="utf-8")

        def on_evaluate(self, a, state, control, metrics=None, model=None, **kw):
            score, per = dev_score(metrics or {})
            if math.isnan(score):
                return control
            write_metric({"step": state.global_step, "dev_ndcg10": score, "per_lang": per})
            best = self.state.get("best")
            if best is None or score > best + args.min_delta:
                self.state.update({"best": score, "best_step": state.global_step, "best_per_lang": per, "bad_evals": 0})
                tmp = run_dir / "best.tmp"
                if tmp.exists():
                    shutil.rmtree(tmp)
                model.save(str(tmp))
                if (run_dir / "best").exists():
                    shutil.rmtree(run_dir / "best")
                os.replace(tmp, run_dir / "best")
                log.info(f"dev NDCG@10 {score:.4f} (neu bestes) {per}")
            else:
                self.state["bad_evals"] = self.state.get("bad_evals", 0) + 1
                log.info(f"dev NDCG@10 {score:.4f}, bestes {best:.4f}, ohne Fortschritt {self.state['bad_evals']}/{args.patience}")
                if self.state["bad_evals"] >= args.patience:
                    log.info("Frueher Abbruch: dev steigt nicht mehr.")
                    control.should_training_stop = True
            self.state["last"] = score
            self.save_state()
            return control

    return [TimedSave(), Progress(), BestAndStop()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="reranker-mminilm")
    ap.add_argument("--base", default=BASE_MODEL)
    ap.add_argument("--max-length", type=int, default=MAX_LENGTH)
    ap.add_argument("--batch", type=int, default=64, help="Paare je Schritt auf der GPU (bei OOM 32 oder 16)")
    ap.add_argument("--grad-accum", type=int, default=1, help="Schritte sammeln (Batch 32 + 2 = wie 64)")
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--warmup", type=float, default=0.05)
    ap.add_argument("--evals", type=int, default=12, help="Messungen auf dev ueber den ganzen Lauf")
    ap.add_argument("--save-minutes", type=float, default=60)
    ap.add_argument("--patience", type=int, default=3)
    ap.add_argument("--min-delta", type=float, default=0.001)
    ap.add_argument("--calib-samples", type=int, default=2000)
    ap.add_argument("--max-rows", type=int, default=0, help="Obergrenze je Datei (0 = alles)")
    ap.add_argument("--workers", type=int, default=-1, help="DataLoader-Prozesse (-1: Linux 2, sonst 0)")
    ap.add_argument("--resume", action="store_true", help="am letzten Zwischenstand fortsetzen")
    ap.add_argument("--fresh", action="store_true", help="alte Zwischenstaende dieses Namens loeschen")
    ap.add_argument("--quick", action="store_true", help="Rauchtest: wenige Zeilen und Schritte, CPU/MPS geht")
    ap.add_argument("--status", action="store_true", help="nur Stand anzeigen")
    ap.add_argument("--out-root", default=str(MODELS))
    args = ap.parse_args()

    if args.quick and "--name" not in sys.argv:
        args.name = "reranker-mminilm-quick"
    if args.status:
        show_status(args.name)
        return

    log_path = setup_logging(args.quick)
    run_dir = CKPT / args.name
    if args.fresh and run_dir.exists():
        shutil.rmtree(run_dir)
    checkpoints = sorted(run_dir.glob("checkpoint-*"), key=lambda p: int(p.name.split("-")[-1])) if run_dir.exists() else []
    if checkpoints and not args.resume:
        sys.exit(f"In {run_dir} liegen Zwischenstaende. --resume zum Fortsetzen oder --fresh zum Neubeginn.")
    run_dir.mkdir(parents=True, exist_ok=True)

    for f in ("distill.jsonl", "labeled.jsonl", "dev.jsonl"):
        if not (DATA / f).exists():
            sys.exit(f"{DATA / f} fehlt. Erst python train/prepare_data.py laufen lassen.")

    import torch  # noqa: PLC0415
    from datasets import load_dataset  # noqa: PLC0415
    from sentence_transformers import __version__ as st_version  # noqa: PLC0415
    from sentence_transformers.cross_encoder import (  # noqa: PLC0415
        CrossEncoder,
        CrossEncoderTrainer,
        CrossEncoderTrainingArguments,
    )
    from sentence_transformers.cross_encoder.evaluation import CrossEncoderRerankingEvaluator  # noqa: PLC0415
    from sentence_transformers.cross_encoder.losses import BinaryCrossEntropyLoss, MSELoss  # noqa: PLC0415
    from sentence_transformers.evaluation import SequentialEvaluator  # noqa: PLC0415
    import transformers  # noqa: PLC0415

    device = pick_device()
    fp16 = device == "cuda"
    gpu = torch.cuda.get_device_name(0) if device == "cuda" else device
    log.info(f"Start {args.name}: Geraet {gpu}, fp16={fp16}, Basis {args.base}, max_length {args.max_length}")
    log.info(f"Log: {log_path}")
    if args.quick:
        args.batch, args.grad_accum = min(args.batch, 8), 1
        args.calib_samples = min(args.calib_samples, 128)
        args.max_rows = args.max_rows or 400
        args.save_minutes = min(args.save_minutes, 60)

    cache_dir = str(DATA / ".hf_cache")

    def load(name: str):
        ds = load_dataset("json", data_files=str(DATA / name), split="train", cache_dir=cache_dir)
        if len(ds) and args.max_rows and len(ds) > args.max_rows:
            ds = ds.shuffle(seed=42).select(range(args.max_rows))
        return ds

    distill, labeled = load("distill.jsonl"), load("labeled.jsonl")
    log.info(f"Daten: distill {len(distill)}, labeled {len(labeled)}")

    model = CrossEncoder(args.base, num_labels=1, max_length=args.max_length, device=device)

    # Lehrer-Scores auf die Skala des Ausgangsmodells bringen (gleiches Ergebnis bei --resume).
    calib = None
    calib_path = run_dir / "calibration.json"
    if len(distill):
        if calib_path.exists():
            calib = json.loads(calib_path.read_text(encoding="utf-8"))
        else:
            sample = distill.shuffle(seed=7).select(range(min(args.calib_samples, len(distill))))
            pairs = list(zip(sample["query"], sample["passage"]))
            student = model.predict(pairs, batch_size=64, show_progress_bar=False)
            a, b = du.moment_match([float(x) for x in sample["score"]], [float(x) for x in student])
            calib = {"a": a, "b": b, "n": len(pairs)}
            calib_path.write_text(json.dumps(calib), encoding="utf-8")
        a, b = calib["a"], calib["b"]
        log.info(f"Lehrer-Kalibrierung: score_neu = {a:.4f} * score + {b:.4f} (aus {calib['n']} Paaren)")
        distill = distill.map(lambda r: {"score": a * r["score"] + b}, desc="Kalibrierung")

    train_sets, losses = {}, {}
    if len(distill):
        train_sets["distill"] = distill.select_columns(["query", "passage", "score"])
        losses["distill"] = MSELoss(model)
    if len(labeled):
        train_sets["labeled"] = labeled.select_columns(["query", "passage", "label"]).cast_column(
            "label", __import__("datasets").Value("float32"))
        losses["labeled"] = BinaryCrossEntropyLoss(model)
    if not train_sets:
        sys.exit("Keine Trainingsdaten.")

    # dev: je Sprache ein Evaluator, Auswahlmass ist der Mittelwert.
    groups = list(read_jsonl(DATA / "dev.jsonl"))
    if args.quick:
        groups = groups[:24]
    by_lang = {}
    for g in groups:
        by_lang.setdefault(g.get("lang") or "xx", []).append(
            {"query": g["query"], "positive": g["positive"], "negative": g["negative"]})
    if not by_lang:
        sys.exit("dev.jsonl ist leer, ohne Pruefmenge kein Training (frueher Abbruch braucht sie).")
    evaluators = [CrossEncoderRerankingEvaluator(samples=s, at_k=10, name=f"dev-{lang}", batch_size=64,
                                                 write_csv=False)
                  for lang, s in sorted(by_lang.items())]
    evaluator = SequentialEvaluator(evaluators)
    log.info(f"dev-Gruppen je Sprache: { {k: len(v) for k, v in by_lang.items()} }")

    base_path = run_dir / "baseline.json"
    if base_path.exists():
        baseline = json.loads(base_path.read_text(encoding="utf-8"))
    else:
        score, per = dev_score(evaluator(model))
        baseline = {"dev_ndcg10": score, "per_lang": per}
        base_path.write_text(json.dumps(baseline), encoding="utf-8")
    log.info(f"Ausgangsmodell dev NDCG@10 {baseline['dev_ndcg10']} {baseline['per_lang']}")
    best_state = run_dir / "best.json"
    if not best_state.exists():
        best_state.write_text(json.dumps({"baseline": baseline["dev_ndcg10"]}), encoding="utf-8")

    rows = sum(len(d) for d in train_sets.values())
    steps = max(1, math.ceil(rows / (args.batch * args.grad_accum) * args.epochs))
    max_steps = 30 if args.quick else -1
    eval_steps = max(5, (30 if args.quick else steps) // max(1, args.evals))
    # Nur Linux startet Worker per fork. Windows/macOS muessten den Collator samt Modell
    # pickeln, das scheitert (gemessen auf macOS mit MPS).
    workers = args.workers if args.workers >= 0 else (2 if platform.system() == "Linux" else 0)
    log.info(f"{rows} Paare, ca. {steps} Schritte, Messung alle {eval_steps} Schritte und mindestens alle "
             f"{args.save_minutes:.0f} min")

    targs = CrossEncoderTrainingArguments(
        output_dir=str(run_dir),
        num_train_epochs=args.epochs,
        max_steps=max_steps,
        per_device_train_batch_size=args.batch,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        warmup_steps=args.warmup,
        fp16=fp16,
        eval_strategy="steps",
        eval_steps=eval_steps,
        save_strategy="steps",
        save_steps=eval_steps,
        save_total_limit=2,
        logging_steps=max(1, eval_steps // 10),
        logging_first_step=True,
        report_to="none",
        seed=42,
        dataloader_num_workers=workers,
        multi_dataset_batch_sampler="proportional",
        use_cpu=device == "cpu",
    )
    trainer = CrossEncoderTrainer(
        model=model,
        args=targs,
        train_dataset=train_sets if len(train_sets) > 1 else next(iter(train_sets.values())),
        loss=losses if len(losses) > 1 else next(iter(losses.values())),
        evaluator=evaluator,
        callbacks=build_callbacks(args, run_dir, steps),
    )
    t0 = time.time()
    resume = str(checkpoints[-1]) if (args.resume and checkpoints) else None
    if resume:
        log.info(f"Fortsetzen ab {resume}")
    trainer.train(resume_from_checkpoint=resume)
    hours = (time.time() - t0) / 3600
    log.info(f"Training beendet nach {hours:.2f} h, Schritt {trainer.state.global_step}")

    # Abschlussmessung des letzten Stands, dann das beste Modell exportieren.
    final_score, final_per = dev_score(evaluator(model))
    state = json.loads(best_state.read_text(encoding="utf-8"))
    best_dir = run_dir / "best"
    if state.get("best") is None or final_score > state["best"] + args.min_delta or not best_dir.exists():
        model.save(str(best_dir))
        state.update({"best": final_score, "best_step": trainer.state.global_step, "best_per_lang": final_per})
        best_state.write_text(json.dumps(state, indent=1), encoding="utf-8")

    date = dt.date.today().isoformat()
    out = Path(args.out_root) / f"{args.name}-{date}"
    n = 2
    while out.exists():
        out = Path(args.out_root) / f"{args.name}-{date}-{n}"
        n += 1
    shutil.copytree(best_dir, out)
    better = state["best"] > baseline["dev_ndcg10"] + args.min_delta
    manifest_path = DATA / "manifest.json"
    card = {
        "name": out.name,
        "date": dt.datetime.now().isoformat(timespec="seconds"),
        "base_model": args.base,
        "max_length": args.max_length,
        "use_in_service": f"RERANKER_MODEL={out.relative_to(ROOT).as_posix() if out.is_relative_to(ROOT) else out}",
        "quick_smoke_test": args.quick,
        "data": json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None,
        "train_rows": {k: len(v) for k, v in train_sets.items()},
        "losses": {"distill": "MSELoss (Lehrer-Score, kalibriert)", "labeled": "BinaryCrossEntropyLoss"},
        "teacher_calibration": calib,
        "steps": trainer.state.global_step,
        "epochs": args.epochs,
        "batch": args.batch,
        "grad_accum": args.grad_accum,
        "lr": args.lr,
        "fp16": fp16,
        "device": gpu,
        "train_hours": round(hours, 2),
        "dev_groups": {k: len(v) for k, v in by_lang.items()},
        "dev_ndcg10_baseline": baseline,
        "dev_ndcg10_best": {"score": state["best"], "step": state.get("best_step"), "per_lang": state.get("best_per_lang")},
        "dev_ndcg10_final": {"score": final_score, "per_lang": final_per},
        "better_than_base_on_public_dev": better,
        "note": "dev ist oeffentlich (MS MARCO/mMARCO, GermanDPR, MIRACL). Entscheidend ist nur die "
                "Messung auf dem Mac: eval/objective.py --split both plus Phase 2b (docs/finetuning-plan.md).",
        "versions": {"torch": torch.__version__, "transformers": transformers.__version__,
                     "sentence_transformers": st_version, "python": platform.python_version()},
    }
    (out / "model_card.json").write_text(json.dumps(card, indent=1, ensure_ascii=False), encoding="utf-8")

    # Ladeprobe wie im Dienst (app/rerank.py): transformers direkt, ohne sentence-transformers.
    from transformers import AutoModelForSequenceClassification, AutoTokenizer  # noqa: PLC0415

    tok = AutoTokenizer.from_pretrained(out)
    m = AutoModelForSequenceClassification.from_pretrained(out).eval()
    with torch.inference_mode():
        enc = tok(["wie hoch ist die zugspitze"], ["Die Zugspitze ist mit 2962 m der hoechste Berg Deutschlands."],
                  truncation="only_second", max_length=args.max_length, return_tensors="pt")
        probe = float(m(**enc).logits.view(-1)[0])
    log.info(f"Ladeprobe wie im Dienst ok, Score {probe:.3f}")

    LOGS.mkdir(parents=True, exist_ok=True)
    (LOGS / f"DONE-{args.name}.json").write_text(json.dumps({
        "model_dir": str(out), "baseline": baseline["dev_ndcg10"], "best": state["best"], "better": better,
    }), encoding="utf-8")
    log.info(f"FERTIG: {out}")
    log.info(f"dev NDCG@10 vorher {baseline['dev_ndcg10']}, bestes {state['best']} -> "
             f"{'besser' if better else 'NICHT besser, trotzdem auf dem Mac messen oder verwerfen'}")
    log.info("Diesen Ordner auf den Mac kopieren (models/), dort messen: train/LAPTOP.md Abschnitt 'Zurueck auf den Mac'.")


if __name__ == "__main__":
    main()
