# Changelog

## Unreleased

- `passages(query)`: search, read the top pages and return the paragraphs that answer the query, best first, with their source URLs. Also as `searchify passages` on the command line and `POST /passages` in the HTTP service.
- MCP server `searchify-mcp` with the tools `passages`, `search` and `extract` (extra `mcp`).
- Splitting pages into ranked passages and asking for full questions instead of keywords follow [langchain-searchify](https://github.com/felixvew/langchain-searchify) by Félix Fautsch.

## 0.2.0

- Command line interface: `searchify search` and `searchify extract`, JSON by default, `--no-json` for readable text. Thanks to @c2p-cmd (#2).
- Tavily as the last fallback for search and reading, only if `TAVILY_API_KEY` is set.
- The research skill now lives in the repo under `skill/`.

## 0.1.0

First release.

- Local-first web search: DuckDuckGo (keyless, default) and SearXNG (self-hosted).
- Optional cloud fallback: Google via Serper, off unless a key is set.
- Clean content extraction with a static-then-browser load ladder and an SSRF-safe proxy.
- Fast cross-encoder reranking with a swappable model.
- Small Python API and an optional HTTP service.
<!-- Stand: 02.11.2026 -->
