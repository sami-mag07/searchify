# Changelog

## Unreleased

- Limit the load ladder per request: `max_stage` (plain, tls, browser, stealth), `budget` (low, normal, high) and `cloud_fallback`, in `read_pages`, `read_pages_ex`, `extract_report`, the HTTP `/extract` body, the MCP `extract` tool and the CLI (`--max-stage`, `--budget`, `--no-cloud`). The effective stage is the lowest of the environment, the operator cap `SEARCHIFY_MAX_STAGE` and the request, so `READER_STEALTH=1` cannot override a request for `plain`. With nothing set the behaviour is unchanged.
- User profile for the command line and `extract_report` (never the HTTP service or MCP): a first-run interview at a terminal (stage, budget, search region from the system locale as a suggestion), `searchify config` and `searchify config --show`. JSON file in the user config folder, written atomically.
- Product pages carry a compact `product` (name, price, priceCurrency, availability, gtin list, sku, brand), read from the JSON-LD that is already parsed. Pages without a product get no `product`, and pages without any JSON-LD or namespaced meta no longer get an empty `structured`.
- Region: `--country` (CLI), `country` (HTTP `/search`, Python filter) next to `language`. With a language, DuckDuckGo gets the matching region (`nl` gives `nl-nl`, `nl` + `BE` gives `be-nl`; table of 64 regions in `search/regions.py`); without one it stays `wt-wt`. `language=any` means no language parameter. SearXNG, Serper and Tavily ignore the new region mapping.
- DuckDuckGo: a real zero-hit search is an empty list instead of an error; rate limit and timeout errors start with `rate_limited:` or `timeout:` (`searchify.search.error_code()` reads it from `search_report()`).
- PDFs are read as normal documents (extra `pdf`, `pip install "searchify[pdf]"`, pypdfium2): up to 20 MB, 50 pages and 60,000 characters, with `[Seite N]` markers and `page_count`, text extraction in its own process per PDF. Failures say `pdf_encrypted`, `pdf_no_text` or `pdf_too_large` and skip the browser stages. Without the extra nothing changes.
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
