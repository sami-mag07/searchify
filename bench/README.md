# Passages benchmark

Measures and tunes three things about `searchify.passages`:

1. **Passage settings**: chunking (lines, merged lines, word windows), pages read, minimum
   words, passage length, passages per page and per site.
2. **Reranker**: six cross-encoders with pinned revisions (`models.json`), alone or as a
   second stage on the top 10/20/40, plus speed per question on a 4-thread CPU.
3. **Full question or keywords**: for the search and for the ranking, as a 2x2.

The goal is the share of questions whose answer pattern appears in the first 4800 characters
the model reads (today: 8 passages of 600). 125 questions with answer patterns from the
existing eval: 50 dev to tune on, 75 test read once at the end.

| Stage | Script | Needs |
|---|---|---|
| 1 record | `record.py` | network; search hits and pages for both query forms, recorded once |
| 2 latency | `latency.py` | ms per pair on CPU and GPU |
| 3 scores | `scores.py` | GPU helps; every chunk scored by every model, cached in sqlite |
| 4 loop | `loop.py` | CPU; tries settings on dev until the deadline, writes `out/report.md` |

Every stage resumes where it stopped. `run_all.sh` runs them in order; `SMOKE=1` runs
2 questions through everything in about two minutes.

## On schinken

```bash
bench/schinken/run.sh check     # online? WSL? GPU?
bench/schinken/run.sh ship      # code + questions to WSL ~/searchify-bench
bench/schinken/run.sh setup     # venv, searchify[rerank], prints the GPU torch sees
bench/schinken/run.sh smoke     # 2 questions end to end, foreground
UNTIL=07:30 bench/schinken/run.sh launch
bench/schinken/run.sh status
bench/schinken/run.sh fetch     # report and logs to bench/out-schinken/
```

`data/` holds the questions and stays out of git, as does everything under `out*/`.
<!-- Stand: 02.11.2026 -->
