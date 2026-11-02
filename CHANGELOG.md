# Changelog

## Unreleased

- Every extracted page carries `structured` (its JSON-LD blocks and namespaced meta tags, untouched) and `stage` (static, browser, browser-direct, stealth or tavily).
- Failures say why: `fetch_failure()` records the reason of the last fetch (`HTTP 403, also after retry`, `not HTML: application/pdf`, `timeout`, ...), the HTTP service returns a `failed` list next to `results`, and `search_report()` tells a backend error (rate limit, unreachable instance) apart from a query with no hits. `/search` and the CLI report that error.
- Fixed reason codes next to the failure text: each failed page keeps `error` and gets `reason` (blocked, rate_limited, unavailable, timeout, not_html, too_large, address_blocked, no_main_text, extract_failed). A page that only delivers thin static text after every browser stage failed counts as read but thin (thin_content or challenge); its document stays unchanged.
- `/extract` adds `status: {read, failed, thin, reasons}` only when a page failed or came back thin; `/search` adds `status: {reason, engine}` only when the list is empty because a backend failed. Header `X-Searchify-Status` carries fixed codes only. `extract_report(urls)` returns `{results, failed, status}`; `extract()` is unchanged. `searchify extract` shows a one-line progress ticker on stderr at an interactive terminal only (`--quiet` turns it off).
- A crashed extraction worker no longer leaves page reading in threads for good: the pool is recreated.
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
