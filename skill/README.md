# searchify (skill)

A drop-in research skill: give your agent multi-engine web search, clean page
extraction and a fast reranker, and a toolbox of search technique.

## Install

Copy this folder into your agent's skills directory. For Claude Code:

```
cp -r skill ~/.claude/skills/searchify
```

Then set the search provider keys you have (any subset):

```
cp references/.env.example references/.env    # then fill in the keys you have
```

`references/search.py` is standalone (standard library only). For page
extraction and reranking, install the engine package:

```
pip install "searchify[rerank] @ git+https://github.com/sami-mag07/searchify"
```

## Use

```
python3 references/search.py "who makes solid-state batteries" --fan
python3 references/search.py "grant deadline 2026" --site ec.europa.eu --since 30d
```

See `SKILL.md` for how the agent should use it, and `references/tools.md` for
search technique and source APIs.
<!-- Stand: 24.10.2026 -->
