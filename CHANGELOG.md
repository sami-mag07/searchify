# Changelog

## 0.3.0

- Limit the load ladder per request: `max_stage` (plain, tls, browser, stealth), `budget` (low, normal, high) and `cloud_fallback`, in `read_pages`, `read_pages_ex`, `extract_report`, the HTTP `/extract` body and the CLI (`--max-stage`, `--budget`, `--no-cloud`); the MCP `extract` tool takes `max_stage` and `budget`. The effective stage is the lowest of the environment, the operator cap `SEARCHIFY_MAX_STAGE` and the request, so `READER_STEALTH=1` cannot override a request for `plain`. With nothing set the behaviour is unchanged.
- User profile for the command line and `extract_report` (never the HTTP service or MCP): a first-run interview at a terminal (stage and budget), `searchify config` and `searchify config --show`. JSON file in the user config folder, written atomically.
- Product pages carry a compact `product` (name, price, priceCurrency, availability, gtin list, sku, brand), read from the JSON-LD that is already parsed. Pages without a product get no `product`, and the raw `structured` block is opt-in (see below).
- Region: `--country` (CLI), `country` (HTTP `/search`, Python filter) next to `language`. With a language, DuckDuckGo gets the matching region (`nl` gives `nl-nl`, `nl` + `BE` gives `be-nl`; table of 64 regions in `search/regions.py`); without one it stays `wt-wt`. `language=any` means no language parameter. SearXNG, Serper and Tavily ignore the new region mapping.
- DuckDuckGo: a real zero-hit search is an empty list instead of an error; rate limit and timeout errors start with `rate_limited:` or `timeout:` (`searchify.search.error_code()` reads it from `search_report()`).
- PDFs are read as normal documents (extra `pdf`, `pip install "searchify[pdf]"`, pypdfium2): up to 20 MB, 50 pages and 60,000 characters, with `[Seite N]` markers and `page_count`, text extraction in its own process per PDF. Failures say `pdf_encrypted`, `pdf_no_text` or `pdf_too_large` and skip the browser stages. Without the extra nothing changes.
- Every extracted page carries `stage` (static, browser, browser-direct, stealth or tavily), so a normal page gains this one small field and nothing else. The raw `structured` block (its JSON-LD blocks and namespaced meta tags, untouched) stays opt-in: `--structured` (CLI), `structured: true` (`/extract`), `structured=True` (`read_pages`, `read_pages_ex`, `extract_report`). Structured data, `stage` and the failure reasons build on #3 by @meewisseAI.
- Failures say why: `fetch_failure()` records the reason of the last fetch (`HTTP 403, also after retry`, `not HTML: application/pdf`, `timeout`, ...), the HTTP service returns a `failed` list next to `results`, and `search_report()` tells a backend error (rate limit, unreachable instance) apart from a query with no hits. `/search` and the CLI report that error.
- Fixed reason codes next to the failure text: each failed page keeps `error` and gets `reason` (blocked, rate_limited, not_found, unavailable, timeout, not_html, too_large, address_blocked, no_main_text, extract_failed). A page that only delivers thin static text after every browser stage failed counts as read but thin (thin_content or challenge); its document stays unchanged. Thin pages are only detected when a browser stage (render or stealth) is installed and on.
- `/extract` takes `detail: true` to add the reason code to every failed entry, and adds `status: {read, failed, thin, reasons}` only when a page failed or came back thin; `/search` adds `status: {reason, engine}` only when the list is empty because a backend failed. Header `X-Searchify-Status` carries fixed codes only. `extract_report(urls)` returns `{results, failed, status}`; `extract()` is unchanged. `searchify extract` shows a one-line progress ticker on stderr at an interactive terminal only (`--quiet` turns it off).
- `search_report()['error']` is the first backend error, or None when a backend answered cleanly with no hits. The default chain skips SearXNG without `SEARXNG_URL` (and Serper without a key); SearXNG accepts `country`. DuckDuckGo regions: `en` + `GB` is `uk-en`, `zh` + `TW`/`HK` is `tzh`, `SA` is `xa`. `/passages` passes `country` on.
- A crashed extraction worker no longer leaves page reading in threads for good: the pool is recreated.
- `passages(query)`: search, read the top pages and return the paragraphs that answer the query, best first, with their source URLs. Also as `searchify passages` on the command line and `POST /passages` in the HTTP service.
- MCP server `searchify-mcp` with the tools `passages`, `search` and `extract` (extra `mcp`).
- Splitting pages into ranked passages and asking for full questions instead of keywords follow [langchain-searchify](https://github.com/felixvew/langchain-searchify) by Félix Fautsch.

### Fixed

- Wikipedia and other Wikimedia pages load again: the default User-Agent names the project and its URL, as Wikimedia's policy asks; set `SEARCHIFY_USER_AGENT` to use your own.
- The Chrome-TLS fallback (curl_cffi) never ran: a swallowed `TypeError` turned every attempt into `None`. It now pins the checked address with `RESOLVE`, drops the answer if the connected IP is not that address, and logs why.
- Cleaning line ends is linear: a page with 40,000 trailing spaces took 2 s before.

### Security

- Page and browser-proxy reads ask only for encodings that can be decoded with an output cap (`Accept-Encoding: gzip, deflate`, plus `br` with Brotli 1.2 or newer) and decode them in pieces of at most 256 KB. More than one coding layer, or one without an output cap (zstd, compress), is refused (`network error (unsupported content encoding)`); unknown header values are ignored as before. The body of a followed redirect is never decoded.
- PDFs are requested uncompressed; a compressed answer is decoded with the same cap and stops at the 20 MB limit (`pdf_too_large`).
- The fetch timeout is an overall deadline that includes connecting, the TLS handshake and the headers, not only the body: a server dripping its headers no longer holds a worker past the timeout.
- At most 4 addresses are tried per host, alternating IPv6 and IPv4.
- The Chrome-TLS fallback ignores proxy settings from the environment, so the pinned public address cannot be routed elsewhere.
- The Tavily fallback for reading never gets a page refused as `address_blocked` (a private or internal address), so internal URLs and the tokens in them do not reach a third party.
- The PDF child: title and author capped at 300 characters, answer in JSON instead of pickle with a size cap, killed above 1 GB resident memory, environment emptied before pdfium loads (hygiene, not a sandbox).

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
