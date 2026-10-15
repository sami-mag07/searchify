# Research Agent

Stand vom Mac am 15.10.2026: der Recherche-Agent, sein Skill und der eigene Suchdienst in einem Repo.

| Ordner | Was | Auf dem Mac liegt das Original unter |
|---|---|---|
| `skills/research-agent/` | Skill: `search.py` (Multi-Engine, `--fan`, Filter, Fundliste), `sources.py` (Beleg-Ledger und Verify-Gate), `fetch_hard.py`, `tools.md`, Such-Eval in `evals/` | `~/.claude/skills/research-agent` |
| `agents/researcher.md` | spawnbarer Recherche-Agent für Claude Code | `~/.claude/agents/researcher.md` |
| `skills/tool-clone/` | Skill für Clean-room-Nachbauten von Tools/APIs | `~/.claude/skills/tool-clone` |
| `search-service/` | eigener Suchdienst, kompatibel zu Tavily, Exa, Serper und Jina, mit kompletter Git-Historie | `~/Desktop/APP/tavily-clone` |

## Einstieg

- Suchdienst: `search-service/README.md` und `search-service/docs/handoff.md` (Stand, Fallen, Messwerkzeuge). Nächster Auftrag: `search-service/docs/auftrag-bibliothek.md`.
- Skill und Agent: Ordner nach `~/.claude/skills/` bzw. `~/.claude/agents/` kopieren, `.env.example` zu `.env` kopieren und eigene Keys eintragen.

## Nicht im Repo

- Secrets (`.env`). Keys nur lokal.
- Das Sortier-Modell. Der Dienst nutzt `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` von Hugging Face (`RERANKER_MODEL` in `search-service/app/config.py`) und lädt es beim ersten Start automatisch herunter (ca. 470 MB). Die Ordner unter `models/` auf dem Mac sind verworfene Feintuning-Versuche und werden nicht gebraucht.
- Messdaten des Suchdienstes (große Teile von `eval/`, ca. 2,4 GB); Austausch nur per USB/AirDrop.
- `agent-reach` (Fremd-Skill, eigenständig installieren).
