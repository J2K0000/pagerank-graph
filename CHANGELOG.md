# Changelog

Format inspired by Keep a Changelog; SemVer numbering. The package version is `prgraph.__version__` (run
`pip install -e .` again after a version bump to update the installed metadata).

## Unreleased

### Documentation

- README, CONTRIBUTING, SECURITY, this changelog and the design note are now in English. The README opens with
  a plain-language explanation of what the tool does, what you get and what it is not. The tool's interface,
  reports and messages stay in French.

## 0.5.0 (2026-11-11)

The report becomes usable by the consultant and presentable to the client.

### HTML report

- Two modes in the same `report.html` file: **Analysis** (the explorer, to work) and **Presentation** (a deck
  to page through in front of the client, one idea per screen, printable to PDF, one slide per page). Switch
  in the header; `report.html#presentation` opens the deck directly.
- Presentation mode: cover, contents, verdict in three tiles, what earns against what receives, silo map,
  cost of the template, profitable pages that are poorly linked, action plan as cards with "how we will know
  it works", before / after of the template fixes, a single Method slide. Options `--brand-name`,
  `--brand-logo`, `--brand-color` (neutral by default).
- Analysis mode: readable verdict first, score showing "before → after" during a simulation, silo view by
  default at the level of section families (language variants grouped), palette checked for colour-blind
  readers, tabs accessible from the keyboard, a definition line under each tab and a "how to read this
  report" tab, tables that can be sorted, filtered, paged and exported, light or dark theme, usable on
  mobile, print stylesheet.
- Offline: the graph libraries (graphology, graphology-library, sigma.js, MIT licenses in
  `prgraph/viewer/vendor/`) are embedded in the report; no third-party call on opening any more (system
  font). `--cdn` keeps the CDN tags, with integrity hashes.

### Data

- `graph.json`: `families` (sections without language prefix, fixed colour slot, "others" beyond seven),
  `segments[].family`, `scenario` (template fixes applied together: alignment, share of the pages that earn,
  share of the most underfed segment, before and after), `meta.brand`.
- `alignment.value_pages_pr_share`: share of importance received by the pages that carry 80 % of the
  measured value, the only measure that says whether importance goes to what earns (the share of the money +
  acquisition classes describes types of addresses and is no longer presented as "what earns"). Written in
  `findings.md` § 0 and the workbook.
- `alignment.worst_family`: worst section gap across all languages, the headline figure of Presentation
  mode, also written in `findings.md` § 0 and the summary tab so it has its evidence.
- The before / after scenario applies exactly the template fixes of the plan (`scenario.tickets`), with the
  volume of each step (`scenario.changes`), instead of a separate set of rules.
- Period of the business data: `--business-period`, or the `# période : ...` line written by `fetch-gsc` and
  `fetch-ga4` at the top of the CSV; `meta.value_kind` tells a traffic value (clicks, impressions, sessions),
  which the report labels as such, from a business value (revenue, conversions).
- Gain texts in French number format ("+0,48 pt").
- Joining business data, targets and groups uses the same normalisation as the graph: with `--strip-query`, a
  Search Console URL with `?utm_source=...` or `#anchor` joins its page.

### Spreadsheets

- `report.xlsx` always produced (`--no-excel` to skip it): summary, action plan, pages, starving pages,
  overfed pages, segments, flows between silos, model and read-me tabs; French headers, filters, frozen
  panes, formatted percentages, text cells never interpreted as formulas. The CSVs stay in machine format
  (comma, decimal point) for `diff` and other tools: in Excel with a decimal-comma locale, open the workbook,
  not the CSV.

### Project

- Automatic tests on GitHub on every change (Linux and Windows, Python 3.11, 3.13 and 3.14), actions pinned to
  an exact commit and read-only permissions; Dependabot updates with a 7-day delay.
- `CONTRIBUTING.md` (install, tests, conventions, synthetic test data only) and `SECURITY.md` (private
  vulnerability reporting, scope).
- Crawler rate test: the gap between two requests is measured when they are sent, the total duration on the
  server side; it no longer depends on the machine's load.

### Launcher and local server

- Backslash refused in paths under `/runs/` on every system (on Linux it is not a separator but a file name
  character): the security rule is the same everywhere.
- System font in the launcher; report CSP without any third-party origin (the CDN is only allowed for a
  report produced with `--cdn`); `favicon.ico` answers 204; warning if an embedded library is missing.
- List of analyses limited to real analysis folders, error message on an empty or invalid address, delay
  presented per request, warning on the page budget, clear error when no page could be fetched (robots.txt).

## 0.4.1 (2026-11-11)

Fixes from the independent review of 0.4.0.

- Template inference: the threshold only counts links declared content / unknown (a menu page cited three
  times in a text keeps its three links at full weight); the share shown is the one that decided.
- Numbers: an export where one column proves the thousands separator ("12,345,678") is read that way
  entirely, and a count metric "1,234" without a one- or two-digit decimal part is a thousand.
- `--uncrawled leak`: decided on the URL table alone, never again on the order of rows.
- Classes: legal and trust pages recognised on the whole segment (or a legal suffix), no longer on a word
  at the start of the slug; `/p/` and `/c/` on the whole segment only; root of a language (`/fr/`) = home.
- Clarification: UX-09 (sorting, paging, printing) is not addressed; only the score scale is.

## 0.4.0 (2026-11-11)

Version resulting from a full audit of the project: security of the local server, reliability of the
advice, readability of the report.

### Security

- Local server (`prgraph serve`): safe resolution of paths under `/runs/` (no more traversal through a
  Windows drive-relative path `C:..\`), `HEAD` refused, `POST` accepted only as `application/json`, with an
  anti-CSRF token issued at start-up and checked on `X-Prgraph-Token`, checks of the `Origin` and `Host`
  headers, capped `Content-Length`, errors returned as JSON (400 / 500), CSP and `nosniff` headers.
- HTML report: injected data escapes `<`, `>`, `&`, U+2028 and U+2029; a page title `<!--<script>` no longer
  empties the report. URLs inserted into the DOM are escaped and limited to `http(s)`.
- Launcher: every displayed value is escaped.
- CSV and Excel exports: cells starting with `=`, `+`, `-`, `@` are neutralised (formula injection).

### Model and advice

- Business classes decided on path segments, never on substrings (`/blog/legal-tech` is an article, `/legal`
  a legal page); new `confiance` (trust) class (about, team, press, FAQ); `contact` in money.
- Graded template inference (weight `max(template, weight × (1 − share of linking pages))`), never applied to
  strategic or money pages, list of affected targets in `findings.md` and the Model tab; option
  `--no-boilerplate-inference`.
- Overfed-page tickets: the gain shown is that of the prescribed action (links moved to the footer, or
  grouped behind a legal hub page), never that of deleting the links.
- A redirect to another host is a leak (`lost_external`), not a node; targets found but not crawled are
  leaks by default (`--uncrawled leak|nodes`).
- The `--restart` teleportation vector is kept by `simulate`, by the action plan and by the report's what-if
  (`meta.teleport`).
- Alignment score: the home page is excluded from the distance (brand clicks); the worst segment gap and the
  rank correlation are shown next to the score, with a scale.
- Black holes: only pages that are actually fed (at least one inbound link, PageRank above the median),
  never files (PDF, images).
- Quadrants: no more "candidates for deletion"; "to investigate with clicks and impressions".
- `diff` also compares the equity share and the rank (the log 0-100 score depends on the site's minimum and
  maximum).
- Numbers: the "comma = thousands" or "comma = decimal" convention is decided per column (English `1,234` is
  no longer read as 1.234).
- Ticket efforts presented as default orders of magnitude, to be confirmed.

### Crawl

- `robots.txt`: `pagerank-graph` token respected as well as `*`; 5xx or network error = crawl refused (RFC
  9309); disallowed URLs written with the `robots` status (never assumed to answer 200); no report on a site
  with no page fetched; `stats` separates `fetched`, `blocked`, `errors`.
- Delay applied per request whatever the number of workers (floor 0.1 s), back-off on 429 and 503 with
  `Retry-After`, body read as a stream and capped at 5 MB, `pagerank-graph/<version>` user agent.
- Sitemap: URLs not crawled for lack of budget are written with the `uncrawled` status.
- `crawl-static`: `.html` pages listed under both forms (canonical from one to the other), PDFs and documents
  present answer 200, `index.html` removed only as the last segment, `rel="sponsored"` and `rel="ugc"` treated
  as nofollow (function shared by both crawlers).

### Outputs

- `findings.md`: "unreachable" instead of `-1`, silo matrix limited to the ten largest segments, full label
  for template zones, paths instead of full URLs, equity share (`pr_share_pct`) next to the score.
- Input errors turned into messages (unknown position, malformed `--cap-position`, invalid weight, edges as
  relative paths, ignored segment rules reported).

### Tests

- New tests: HTTP server (traversal, CSRF, token, limits), crawl on a fake local site (robots, rate, 429, rel,
  sitemap, static), export (escaping, formulas, teleportation), model fixes, single version.

## 0.3.0 (2026-11-10)

- Weight profiles renamed `raisonnable` and `modere` (former names accepted), MIT license, repository
  published.

## 0.2.0 (2026-11-03)

- Weighting by observed clicks and semantic component, `diff` between analyses, Search Console and GA4
  connectors.
