# 04 Reranker 2025/2026 (Stand 11.10.2026)

Methode: HF-Karten und Herstellerseiten direkt gelesen (r.jina.ai), Lizenzen per HF-API gegengeprüft. Keine eigenen Latenzmessungen auf M3. Hinweis: Blogs mit "MIT" für bge-reranker-v2-m3 sind falsch, HF-API sagt Apache-2.0.

## Qualität (nDCG@10, Zahlen der Hersteller, nicht unabhängig)
| Modell | Params | Lizenz | BEIR | MIRACL | MKQA | Quelle |
|---|---|---|---|---|---|---|
| jina-reranker-v3 | 0.6B | CC-BY-NC-4.0 | 61.94 | 66.83 | 67.92 | https://huggingface.co/jinaai/jina-reranker-v3 |
| bge-reranker-v2-m3 | 0.57B | Apache-2.0 | 56.51 | 69.32 | 67.88 | gleiche Tabelle; https://huggingface.co/BAAI/bge-reranker-v2-m3 |
| Qwen3-Reranker-0.6B | 0.6B | Apache-2.0 | 56.28 | 57.70 | 65.34 | gleiche Tabelle; https://huggingface.co/Qwen/Qwen3-Reranker-0.6B |
| mxbai-rerank-base-v2 | 0.5B | Apache-2.0 | 55.57 | (Multiling. 28.56, anderer Test) | - | https://huggingface.co/mixedbread-ai/mxbai-rerank-base-v2 |
| jina-reranker-v2-base-multilingual | 0.28B | CC-BY-NC-4.0 | 54.35 (laut mxbai) | - | - | https://huggingface.co/jinaai/jina-reranker-v2-base-multilingual |
| gte-multilingual-reranker-base | 0.31B | Apache-2.0 | - | MLDR 66.33, MMTEB-R 59.44, MTEB-R 59.51 | - | https://huggingface.co/Alibaba-NLP/gte-multilingual-reranker-base |

Qwen-Tabelle (MTEB-R/MMTEB-R): gte-multilingual-reranker-base 59.51/59.44, Jina v2 58.22/63.73, bge-v2-m3 57.03/58.36 (https://huggingface.co/Qwen/Qwen3-Reranker-0.6B). Qwen3-0.6B selbst liegt dort laut Qwen höher, Zeile nicht extrahiert.
mxbai-Blog: BEIR mxbai-large-v2 57.49, base-v2 55.57, Cohere 3.5 55.39, jina-v2 54.35, bge-v2-m3 53.94, mxbai-large-v1 49.32 (https://www.mixedbread.com/blog/mxbai-rerank-v2).
Widerspruch: bge-v2-m3 BEIR 53.94 (mxbai) vs 56.51 (Jina). Jeder Hersteller misst anders. Nur eigener Test mit DE/EN-Webdaten zählt.

## Latenz (A100, GPU, nicht Mac)
mxbai-Blog, Latenz pro Query NFCorpus: xsmall-v1 0.32 s, base-v2 0.67 s, large-v2 0.89 s, bge-v2-m3 3.05 s, bge-gemma 7.20 s. Heißt: mxbai-base-v2 ca. 4.5x schneller als bge-v2-m3, bei höherem BEIR. (Herstellerangabe.)
Agentset (Jina v2 vs bge-v2-m3 DBPedia): 671 ms vs 2087 ms Mittel, API-Messung: https://agentset.ai/rerankers/compare/baaibge-reranker-v2-m3-vs-jina-reranker-v2-base-multilingual
Eigene Basis: mmarco-mMiniLMv2-L12 ~5 ms/Paar, bge-reranker-base ~12 ms/Paar (MPS fp16).

## Kleine/schnelle Optionen
- mmarco-mMiniLMv2-L12-H384 (aktuell): Apache-2.0, ONNX da. Trainiert auf mMARCO (maschinenübersetztes MS MARCO), bekannt schwach out-of-domain. https://huggingface.co/cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
- jina-reranker-v1-turbo-en: 37.8M, 6 Layer, Apache-2.0, nur Englisch. https://huggingface.co/jinaai/jina-reranker-v1-turbo-en
- FlashRank (ONNX, CPU): TinyBERT-L2 ~4 MB, MiniLM-L12 ~34 MB (englisch), ms-marco-MultiBERT-L-12 ~150 MB mehrsprachig (Herstellerhinweis: nicht für Englisch). https://github.com/PrithivirajDamodaran/FlashRank
- answerai-colbert-small-v1: 33M, Apache-2.0, BEIR 53.79 (so gut wie bge-base-Embedding 109M 53.25); englisch-lastig, nicht für DE gedacht. https://huggingface.co/answerdotai/answerai-colbert-small-v1 und https://www.answer.ai/posts/2024-08-13-small-but-mighty-colbert.html
- Model2Vec: potion-multilingual-128M (MIT, aus bge-m3 destilliert), bis 500x schneller als Transformer, aber Bi-Encoder-Qualität, kein Ersatz für Reranker; nur als Vorfilter. https://github.com/MinishLab/model2vec
- multilingual-e5-small (MIT) / bge-m3 (MIT) als Cosinus: schnell, aber schwächer als Cross-Encoder auf Top-20.

## Apple Silicon
- mlx-rerank: Qwen3-Reranker (0.6B/4B, mxfp8) auf MLX, yes/no-Logits, FastAPI. Keine Latenzzahlen im README. https://github.com/aperepel/mlx-rerank
- ONNX Runtime CoreML-EP: bei Transformern oft kein Gewinn, bei dynamischen Shapes langsamer als CPU-EP; feste Shapes nötig. https://www.xybrid.ai/blog/onnx-runtime-6x-faster-apple-silicon-coreml und https://macgpu.com/en/blog/2026-0420-mac-onnx-runtime-coreml-ep-vs-cpu-dynamic-shapes-remote.html . Auf MPS bleiben heißt oft: das Richtige.
- torch.compile für Jina v2: https://github.com/shreyansh26/Accelerating-Cross-Encoder-Inference

## Weitere Lesestoffe (nicht vertieft)
https://localaimaster.com/blog/reranking-cross-encoders-guide , https://www.recal.so/blog/best-local-reranker-models-2026 , https://aimultiple.com/rerankers , https://mixpeek.com/curated-lists/best-rerankers

## Lücken
Keine unabhängige DE-spezifische Benchmark gefunden (MIRACL hat kein Deutsch). mxbai-v2 und Qwen3 ohne verifizierte Mac-Latenz. Reddit nicht ausgewertet (search.py lieferte keine Treffer).
