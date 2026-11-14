# pagerank-graph

[![Tests](https://github.com/J2K0000/pagerank-graph/actions/workflows/tests.yml/badge.svg)](https://github.com/J2K0000/pagerank-graph/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

See where your internal links send importance, and whether it reaches the pages that make you money.

## What it does, in plain words

Every link on a website passes a share of "importance" to the page it points to. Search engines use this
flow, known as PageRank, to judge which pages of a site matter most: Google still lists it among its
[link analysis systems](https://developers.google.com/search/docs/appearance/ranking-systems-guide). On your
own site, your menu, footer and in-text links decide where that importance ends up.

pagerank-graph answers one question: **does your internal linking send that importance to the pages that
actually bring you traffic, leads or revenue?**

PageRank on its own knows nothing about your business, and that is the point. The tool computes how
importance flows through your site, puts it next to what each page earns (Search Console clicks, GA4
conversions or revenue, or your own figures), and shows the gap: pages that earn a lot but receive little,
and pages that receive a lot but earn nothing. On many sites, the menu and footer hand the biggest share to
legal notices, login and contact pages, while product pages starve.

It then simulates fixes before you touch the site (move legal links to a sub-footer, add contextual links,
cap the menu) and ranks them in an action plan with before and after figures.

## What you get

- **An interactive report** (`report.html`), a single file that opens offline. **Analysis** mode is for you:
  explore the graph, every page, every silo, and test changes. **Presentation** mode is for your client or
  your team: a slide deck, one idea per screen, printable to PDF.
- **A ranked action plan**, each fix simulated on the graph, with ready-to-paste developer tickets
  (`brief_dev.md`).
- **An Excel workbook** (`report.xlsx`) and CSV exports of every table.
- **A before / after check** between two analyses (`prgraph diff`), to catch pages that lost importance
  after a release.

## What it is not

- **Not Google's own number.** It is a model. Links are weighted by where they sit on the page (main content,
  menu, footer), a practitioner heuristic inspired by Google's "reasonable surfer" patent
  ([US 7,716,225](https://patents.google.com/patent/US7716225B1/en)); Google does not publish its weights.
  Read the scores as a relative order of pages to strengthen, never as a ranking prediction.
- **Not a backlink tool.** It measures the links inside your site. External links can only be used as entry
  points (`--restart`).
- **Not a JavaScript renderer.** The crawler reads the HTML the server sends. On a site whose links are
  injected client-side, feed it the links from a rendering crawler instead.
- **Not an autopilot.** Business classes, template detection and verdicts are hypotheses to review before
  you hand a report to anyone.

**Language.** The tool's interface, reports and messages are in French. This documentation quotes the
French labels as they appear on screen, with a translation.

## Quick start

Python 3.11 or later.

```bash
pip install "git+https://github.com/J2K0000/pagerank-graph.git"

# Synthetic demo site (mega menu, huge footer, pagination, PDF, 301, 404, nofollow)
prgraph demo --out examples/demo --run
# then open examples/demo/out/report.html

# Analyse your own site from your browser
prgraph serve --port 8765
# then open http://localhost:8765/
```

## Installation

The package installs with its dependencies (numpy, scipy, pandas, beautifulsoup4, lxml, openpyxl, requests)
and provides the `prgraph` command (equivalent: `python -m prgraph`).

```bash
# From GitHub
pip install "git+https://github.com/J2K0000/pagerank-graph.git"
# With the Search Console and GA4 connectors
pip install "pagerank-graph[google] @ git+https://github.com/J2K0000/pagerank-graph.git"

# Development: editable install, your changes apply without reinstalling
git clone https://github.com/J2K0000/pagerank-graph.git && cd pagerank-graph
pip install -e ".[test,google]"
python -m pytest -q
```

Check: `python -c "import prgraph; print(prgraph.__version__)"` and `pip show pagerank-graph` must give the
same version (with an editable install, run `pip install -e .` again after a version bump: the installed
metadata does not follow `__init__.py` on its own). The version changes with every change to the computation
or the interface (`CHANGELOG.md`); quote it with your results so an analysis stays reproducible. Generated
files (`findings.md`, `brief_dev.md`, `diff.md`, `simulation.md`) start with `Outil : prgraph <version> · <date>`.

## What the tool computes

| Need | Answer |
|------|--------|
| Reasonable surfer | Weights by link position (content 1.0, breadcrumb 0.5, sidebar 0.25, header/nav 0.2, footer 0.05); several links A→B are merged, keeping the most visible weight |
| Clean-up first | 301 and canonical chains resolved, computation on final canonical URLs answering 200; a link to a 4xx/5xx is lost equity; an internal nofollow throws equity away (it stays in the denominator) |
| Dead ends | Mass of pages with no way out, and of leaks, redistributed: uniformly, along the teleportation vector, or to the home page |
| Biased teleportation | `--restart`: observed entry points (Search Console landing pages, backlinks) instead of a uniform jump |
| Scale | URL → integer table, scipy CSR matrix, vectorised power iteration (1,500 pages / 110,000 links: 0.02 s; designed for 10^5 to 10^6 pages in memory) |
| Log 0-100 score | `(ln PR - ln PRmin) / (ln PRmax - ln PRmin) × 100` |
| CheiRank | PageRank of the reversed graph; quadrants `puits` (sinks: receive a lot, pass little), `hub` (receive and pass a lot), `passoire` (leaky: pass a lot, receive little), `noeud_mort` (dead nodes) |
| Business alignment | Share of value against share of PageRank, per page (starving and overfed pages) and per segment (gap in points) |
| Silos | Matrix of PageRank flows between segments, leak share per segment, Louvain communities in the viewer |
| Black holes, over-linked pages | Best-fed dead ends, utility pages at the top of the score |
| Cannibalisation | PageRank ratio between pages competing for the same intent |
| Observed clicks | `--transitions`: observed share of transitions blended with the heuristic share by position (`--data-blend 0.7`, never 1.0), floor for links never clicked, coverage reported |
| Semantic component | `--embeddings`: weight multiplied by max(0, cos(source, target)) ** `--semantic-decay`, renormalised per source; "thermal loss" per segment |
| What-if | `simulate` sub-command (Python) and simulation panel in the HTML report (JavaScript recomputation in under a second) |
| Hairball | Contracted view per segment (meta-nodes and flows), filters by link position, sampling beyond `--max-nodes` |

## Verdict and action plan

Every URL gets a **business class**, decided on its path segments, never on substrings (`/blog/legal-tech`
is an article, `/legal` a legal page). A first segment that names a content section (blog, glossary, guides,
ebooks, webinars, case studies...) gives `acquisition`, whatever follows. Otherwise, a segment that is
entirely a legal or support word (legal, privacy-policy, terms, cart, account, jobs...) or ends with a legal
suffix (`-policy`, `-terms`, `-notice`...) gives `legal_support` (`/privacy-first-analytics` and
`/job-board-software` are not legal pages); a trust segment (about, team, press, faq) gives `confiance`
(trust); a segment that starts or ends with a money word (pricing, demo, contact, product, platform,
software, `-vs-`...; `/p/` and `/c/` only as a whole segment) gives `money`; an acquisition word gives
`acquisition`; anything else is `neutre` (neutral); the home page is `accueil`. Override with `--labels
rules.tsv` (`name<TAB>regex`, the older way) or a CSV `url,label`; pages passed with `--strategic` are classed
`money`. Classes are listed in `pages.csv` (`classe` column): review them for the site before handing over a
report.

**Business alignment score** (0-100) = 100 × (1 − ½ Σ |PageRank share of the class − target share|),
computed without the home page (its clicks are brand searches, not value that internal linking should
align). The target is the split of business value by class when it exists (revenue, conversions, clicks),
otherwise a default target (money 38 %, acquisition 38 %, trust 4 %, neutral 12 %, legal 0 %). The score
always comes with what could contradict it: the most underfed segment (share of value against share of
PageRank) and the rank correlation between value and score, with a scale (below 70 misaligned, 70 to 85 to
fix, above 85 aligned by class, check the segments); the same section across all languages; and the share
of importance received by **the pages that carry 80 % of the value**, the only measure that says whether
importance goes to what earns. With Search Console clicks alone, the measure underestimates conversion pages
(demo, pricing, solutions), which get few organic clicks: the report says so next to every value figure.
Prefer conversions or revenue per page (GA4).

The **action plan** simulates each fix on the graph (same engine as `simulate`) and ranks it by PageRank gain
on the money and acquisition classes: move links to legal pages into a sub-footer (site-wide and page by
page; the gain shown is that of the move, never that of deleting the links), group legal links behind a
legal hub page, rescue starving pages with three contextual links from the best pages in their language,
reconnect valuable pages that cannot be reached, cap menus, give dead ends a way out (pages only, never
files), remove internal nofollow. Outputs: `actions.csv`, `brief_dev.md` (tickets ready for Jira or Notion),
section "0. Verdict" of `findings.md`, "Plan d'action" (action plan) tab of the report. Effort levels are
default orders of magnitude, to be confirmed by the team. Turn it off with `--no-actions`, cap it with
`--max-sims`.

One rule applies to every recommendation: no link is hidden or obfuscated (legal notices stay reachable, and
link obfuscation is a manipulation practice). Links are de-duplicated, moved to a sub-footer separate from
the navigation, or added in the content.

In the report: alignment KPI at the top, **Zero-waste scenario** button (« Scénario zéro déchet »: template
links to legal pages removed, menus capped at 50 links, two editorial links per acquisition page to the money
pages of its language), **Simulation delta** colour mode (« Delta de simulation »: green gains, red loses)
with a Before / After switch, **Brief dev** button (Markdown).

## Steering by URL: pages to push, and governance

Each page card starts with a **plain reading**: verdict (underfed, fine, very well fed, utility), cause
(template or editorial links, depth), business stake, then "What to do" (« Quoi faire ») with three linking
pages from the same silo and the simulated gain right away (score and rank), a « + Pousser cette page »
(push this page) button and « Voir l'effet sur le graphe » (see the effect on the graph).

In the report, the **Pages to push** tab (« Cibles à pousser ») takes the URLs you want to rank (typed in,
regex, or the push button on a page card), whatever their automatic class: an article that ranks on a
business query belongs there. For each target: findings (score, rank, inbound links, depth, value), then a
**prescription** of linking pages, from the **same silo and the same language** first, then the same
language and a productive class. The number of links is computed, not asked for. Linking pages are added one
by one, PageRank is recomputed after each link, and the tool stops at the goal (for a chosen target: the top
10 % of its silo, at least the top quartile of the site; for a detected starving page: the top quartile of
its silo) or when the next link would bring less than one point; at least two links for a chosen target.
Each link says from which page (score, same silo or not, menu link to double), where in the page (body text,
first half, paragraph on the topic), with which anchor (canonical first, then declared variants, never
generic) and the score reached. « Simuler la prescription » (simulate the prescription) applies the whole
batch with a safeguard that lists money, strategic or target pages dropping by more than 2 points;
« Exporter les liens à poser » (export the links to add) gives a CSV of source, target, position, anchor.
« Recalculer le rapport avec ces cibles » (recompute the report with these targets) reruns the Python
computation with these URLs as `--strategic`: they become money pages, enter the alignment target, and the
action plan handles them as "Strengthen a chosen target" (« Renforcer une cible choisie »). From the command
line: `--strategic targets.csv` (`url,weight`).

Each action carries an **effort** label: 🧩 template (one component changed reaches every page, low dev
cost, ×N lever) or ✍️ editorial (links added page by page). The plan is sorted by priority, then by gain
over effort; the five best ratios are marked ⚡ quick win.

Internal PageRank is a zero-sum game: pushing one page pushes others down. To avoid sculpting in circles (a
decision undone later to push another URL):

1. decide as a **portfolio** (`--strategic`, Pages to push tab), never one URL at a time, and simulate the
   whole batch; read the Drops section and the safeguard before adding a single link;
2. prefer **template** levers (de-duplicate, move legal links out, cap menus), which lift every productive
   page without trading them against each other, over editorial transfers;
3. keep editorial transfers **inside the target's silo** (topic-sensitive);
4. log every link you add (source, target, anchor, reason, date) and check at each new analysis with
   `prgraph diff --before <analysis N> --after <analysis N+1> [--protected targets.csv]`: the `diff.md`
   report lists protected pages that dropped (score down 5 points or more, equity share down a quarter or
   more, or rank down 10 % of the pages or more: the log 0-100 score depends on the site's minimum and
   maximum, the equity share does not), links to them that disappeared, and protected pages missing from
   the new analysis. Exit codes, usable in continuous integration: 1 = regression found, 2 = incomplete or
   impossible comparison (folder without `pages.csv`, or no `edges.csv`: vanished links are then not
   checked), 0 = nothing to report. `diff` reads `pages.csv` and `edges.csv` from the **same folder**: run
   `compute` with `--out` on the crawl folder (or `prgraph crawl ... --run`). It only compares identical
   URLs and does not follow redirects: after a redesign that changes URLs, or against a staging site on
   another host, protected pages show as "missing" and are checked through the redirect plan. The server
   exposes the same comparison on `POST /api/diff`.

**Simulation delta** mode: colour = direction of the change, node intensity and size = **absolute PageRank
delta** (share of the total), never the relative percentage: a page that gains 0.0001 % stays grey.

## Local app (URL field)

```bash
python serve.py --port 8765        # or: python -m prgraph serve --port 8765 --runs runs
```

Open http://localhost:8765/ and enter a site address, the **pages to push** (URLs, paths or regex, one per
line: they become the report's strategic targets from the first computation), the maximum number of pages,
the delay, the weight profile and the dead-end policy, then click « Analyser » (analyse). The site is crawled
(robots.txt respected, `pagerank-graph` token and `*`; a robots.txt answering 5xx or unreachable refuses the
crawl; HTML as served, without JavaScript rendering; sitemap read to complete the inventory, its URLs not
crawled for lack of budget are written with the `uncrawled` status), PageRank is computed and the report
opens. The delay applies per request whatever the number of parallel requests (floor 0.1 s); 429 and 503 are
retried after `Retry-After`. The server listens on 127.0.0.1 only, requires an anti-CSRF token on every
action and refuses requests from another origin. Each analysis is kept in `runs/<host>-<date>/` (edges.csv,
urls.csv, pages.csv, findings.md, report.html). The same crawl exists on the command line:
`python -m prgraph crawl --url https://example.com --out audits/mysite --max-pages 500 --run`.

The « Données business » (business data) section of the launcher offers three sources to match value
against PageRank:

- **Local CSV**: path to a `url,revenue,conversions,clicks` file, a Search Console "Pages" export or a GA4
  "Pages and screens" export (read on this machine, nothing is sent);
- **Search Console**: property to type in (`sc-domain:example.com` or `https://www.example.com/`; empty =
  `sc-domain:` of the analysed site) or to pick from the list returned by « Lister mes propriétés » (list my
  properties, `sites.list`), period in days;
- **GA4**: property ID, dimension (landing page with or without query string), period.

During the computation phase, the server calls the API, writes `business.csv` in the run folder and passes
`--business` to compute. The log shows the number of URLs fetched, the number joined to the graph and the
coverage (share of graph pages with a value); if the API fails (credentials, quota), the computation goes on
without business alignment and the log says so.

## Business data: Search Console and GA4

Google credentials are read from the `GOOGLE_APPLICATION_CREDENTIALS` environment variable (or
`--credentials FILE`) and **never stored in the project**: a service account JSON file (add it as a reader on
the Search Console property and on the GA4 property), OAuth "installed application" credentials (a browser
opens the first time, the token is cached in `%APPDATA%\pagerank-graph\`) or an `authorized_user` file
(gcloud format). Optional libraries: `pip install google-api-python-client google-analytics-data` (lazy
import, explicit message if missing).

```bash
# Search Console: page dimension, web type, pages of 25,000 rows → url, clicks, impressions, ctr, position
python -m prgraph gsc-sites                                   # properties you can access
python -m prgraph fetch-gsc --site sc-domain:example.com --days 90 --out audits/example/business.csv

# GA4 Data API: landing pages → url (path prefixed with --base-url), sessions, conversions (keyEvents), revenue
python -m prgraph fetch-ga4 --property 123456 --base-url https://example.com --days 90 --out audits/example/ga4.csv

# Merge both sources by URL (clicks + impressions + sessions + conversions + revenue) into one CSV
python -m prgraph fetch-ga4 --property 123456 --base-url https://example.com --days 90 \
    --merge audits/example/business.csv --out audits/example/business.csv
python -m prgraph compute --edges edges.csv --business audits/example/business.csv --out audits/example
```

`--days N` covers N complete days ending three days before today (the latest days are incomplete);
`--start` / `--end` set the window. The merge joins on the normalised URL, sums the metrics, averages CTR and
position weighted by impressions, and fills columns missing from one source with zero.

When only Search Console data exists: the value column is `clicks` (**clicks = realised value**, what the
page captures) and impressions are read as **demand** (what Google shows). Starving pages are computed on
clicks; `findings.md` § 4 bis and the « Famine » (starving pages) tab add the pages with high impressions
and low PageRank, outside the starving list, where a linking gain is the most directly measurable. The
column used and the columns available are recalled at the top of § 4 and of the tab.

## More examples

```bash
# Demo site with observed clicks and generated semantic vectors (output in examples/demo/out-data/)
python -m prgraph demo --out examples/demo --run --with-data
```

```bash
# Screaming Frog export: Bulk Export > Links > All Inlinks, and Internal > HTML
python -m prgraph compute --edges all_inlinks.csv --urls internal_html.csv \
    --business gsc_pages.csv --segments segments.tsv --strategic strategic.csv \
    --site-name "mysite.com" --out audits/mysite
```

```bash
# Edge CSV from any crawler: source,target,position,nofollow,anchor
python -m prgraph compute --edges edges.csv --out audits/mysite
```

```bash
# Static HTML folder (Astro dist, Next export): positions detected in the DOM
python -m prgraph crawl-static --dir dist --base-url https://mysite.com --out audits/mysite
python -m prgraph compute --edges audits/mysite/edges.csv --urls audits/mysite/urls.csv --out audits/mysite/out
```

```bash
# What-if on the command line: remove the footer, cap the mega menu at 30 links, add links
python -m prgraph simulate --edges edges.csv --urls urls.csv --business business.csv \
    --remove-position footer --cap-position header:30 --add-edges additions.csv --out audits/mysite/sim
```

## Input files

| Option | Format | Notes |
|--------|--------|-------|
| `--edges` (required) | `source,target,position,nofollow,anchor` (positions) · Screaming Frog All Inlinks export (`Source, Destination, Follow, Link Position, Status Code`) · `source,target[,weight]` | Detected from the header. Target statuses from the Screaming Frog export are used |
| `--urls` | `url,status[,redirect_target][,canonical][,segment][,title]` or the SF `internal_html` export | Without this file every node is assumed to answer 200. The literal statuses `robots` and `uncrawled` (written by the crawler) mean a URL known but never fetched: neither a node nor a valid target. Providing the full inventory (sitemap, logs, Search Console) reveals the real orphan pages |
| `--business` | `url` + `revenue` / `conversions` / `clicks` / `sessions` / `impressions` / `crawl_hits` | Search Console "Pages" and GA4 "Pages and screens" exports accepted (French or English headers, `#` comment lines ignored). The number convention (decimal comma "12,5" or thousands separator "1,234") is decided per column. Priority: revenue > conversions > clicks > sessions; `--value-col` forces one. Output of `fetch-gsc` / `fetch-ga4` usable as is |
| `--business-period` | free text, e.g. `9 August to 8 November 2026` | Period of the business data, shown with the value in the report and the workbook; read automatically from the `# période : ...` line that `fetch-gsc` and `fetch-ga4` write at the top of the CSV |
| `--business-base-url` | `https://example.com` | Prefixes relative paths in the business CSV (`/products/a/`) to join them to the graph; `(not set)` rows are ignored |
| `--segments` | `name<TAB>regex` (first match wins) or CSV `url,segment` | Default: first path segment |
| `--strategic` | `url[,weight]` | Pages to watch even without business data |
| `--restart` | `url,weight` | Observed teleportation vector |
| `--cannibalisation` | `url,group` | Pages competing for the same intent |
| `--transitions` | `source,target,transitions` (or `count`, `clicks`, `sessions`) | Observed page-to-page clicks: server logs (same-origin referrer), GA4 BigQuery export (`page_referrer` → `page_location`), Matomo Transitions API. URLs are normalised like the edges; pairs missing from the graph are counted and ignored |
| `--embeddings` | `url` then numeric columns, or `url,vector` with one JSON vector per line | One vector per page, same dimension everywhere. Pages without a vector are not penalised |

Weighting beyond position (`prgraph/weights.py`), applied after the clean-up and before PageRank:

- **Observed clicks**, `--transitions FILE.csv --data-blend 0.7`. For each source page with at least one
  transition, a link's weight becomes `blend × observed share + (1 − blend) × heuristic share`; the page's
  outgoing mass is kept (its leak share does not move). A link never clicked on a page that has data keeps
  `(1 − blend)` × its heuristic weight: this floor lets you conclude "badly placed link" rather than "link
  that does not exist". Pages without data keep the heuristic alone. **`--data-blend 1.0` is refused**
  (circularity trap: clicks describe the current design, not a target; a link never clicked would drop to
  zero and could no longer be diagnosed as badly placed). The coverage (share of links and of heuristic mass
  covered, source pages with data, pairs not found) is reported in `findings.md` § 2 bis and in the
  « Modèle » (model) tab.
- **Semantic component**, `--embeddings FILE.csv --semantic-decay 0.5`. Each link's weight is multiplied by
  `max(0, cos(source, target)) ** decay`, then weights are renormalised per source page: the mass sent does
  not change, an off-topic link passes less and its on-topic neighbours get the difference. `decay 0` turns
  the penalty off, a higher value makes it harsher. A link with an end that has no vector keeps a factor of
  1; if every link of a page drops to zero, its original weights are kept. **Thermal loss** measures, per
  segment, the mass that would have flowed without the penalty against the effective mass (same stationary
  PageRank): a positive gap means the segment was receiving equity through off-topic links. It is reported
  in `findings.md`, `graph.json` (`thermal`, the `thermal_*` fields of segments) and the « Modèle » tab. Both
  options can be combined (clicks first, then semantics; the loss is then measured against the already
  blended graph).

Model options: `--weights-profile raisonnable|modere|uniform`, `--weights "footer=0.02,nav=0.3"`,
`--alpha 0.85`, `--dangling uniform|teleport|home`, `--home URL`, `--strip-query`, `--utility-regex`,
`--money-share 0.8`, `--starving-percentile 40`, `--overfed-percentile 80`, `--max-nodes 3000`,
`--no-excel`, `--cdn`, `--brand-name`, `--brand-logo`, `--brand-color`, `--uncrawled leak|nodes` (targets
found but missing from the URL table: a leak by default), `--boilerplate-share 0.6`,
`--boilerplate-min-links 200`, `--no-boilerplate-inference`.

**Template inference.** A mega menu built with `<div>` or a logo strip inside `<main>` is declared `content`
by DOM detection. To make up for it, `content` or `unknown` links to a target cited by at least 60 % of the
linking pages (floor 20 pages, cap 200 links) are requalified as template links, with a graded weight
`max(template weight, weight × (1 − share of linking pages))`. Strategic and money pages are never
requalified (a "book a demo" link in every article is an editorial choice). The affected targets, the
threshold and the spared targets are listed in `findings.md` § 1 and the « Modèle » tab: review this list
before concluding, and turn it off with `--no-boilerplate-inference` to compare.

**Leaks.** A link to a 4xx/5xx, to a redirect that leaves the site (another host), to a URL known but never
fetched (`robots`, `uncrawled`) or to a target found but missing from the URL table loses its equity; its
weight stays in the source page's denominator. The `lost_404`, `lost_external`, `lost_uncrawled` counters are
in `findings.md` § 1.

Profiles: `raisonnable` (reasonable, the default: content 1.0, breadcrumb 0.5, sidebar 0.25, header / nav 0.2,
footer 0.05), `modere` (moderate: header / nav 0.35, footer 0.10, softer on template links) and `uniform`,
the naive PageRank of 1998. The former names `brief` and `library` are still accepted.

## Outputs (`--out folder/`)

| File | Content |
|------|---------|
| `report.html` | Self-contained report, one file to send: **Analysis** mode (explorer) and **Presentation** mode (client deck printable to PDF). Data and libraries embedded: it opens offline, with no third-party call (`--cdn` loads the libraries from the CDN instead) |
| `report.xlsx` | Formatted Excel workbook: summary, action plan, pages, starving pages, overfed pages, segments, flows between silos, model, read-me (tabs named in French). **In Excel set to a locale that uses a decimal comma (French, German...), open this workbook rather than the CSVs** (comma-separated, they open in a single column). `--no-excel` skips it |
| `findings.md` | Report in French: summary, where equity goes by zone, alignment by segment, starving pages, overfed pages, black holes, silo tightness, quadrants, cannibalisation, limits |
| `pages.csv` | One row per canonical URL: score, raw PageRank, CheiRank, quadrant, links, real click depth, value, flags |
| `segments.csv`, `flows_segments.csv` | Alignment and flows between segments |
| `graph.json` | Viewer data (reusable elsewhere) |
| `simulation.md` | With `simulate`: before / after, biggest gains and drops (same teleportation vector as the reference computation) |
| `CHANGELOG.md` (repository) | What changes in each version |

## The report: Analysis and Presentation modes

`report.html` has two modes, switched in the header; `report.html#presentation` opens the second one directly.

**Presentation mode**, for the client: a deck you page through (keyboard arrows, on-screen buttons), one
idea per screen, no jargon, printable to PDF with « Exporter en PDF » (one slide per page, 16:9). Cover,
contents, verdict in three tiles, what earns against what receives, silo map, cost of the template, profitable
pages that are poorly linked, action plan as cards with "how we will know it works", before / after of the
template fixes, a single Method slide. A slide whose data is missing (no business value, no scenario) is
left out. Optional branding, none by default:

```bash
python -m prgraph compute ... --brand-name "My agency" --brand-logo logo.png --brand-color "#1640ef"
```

**Analysis mode**, for the consultant: the verdict first (score, scale, worst segment gap, rank correlation,
share of equity in the template), then the action plan; during a simulation, the score shows "before →
after". Tables can be sorted, filtered, paged and exported to CSV; light or dark theme; usable on mobile
(verdict first, graph on demand); print stylesheet.

- **Silo view** (default): one meta-node per family of sections (`blog`, `fr/blog` and `uk/blog` form the
  "blog" family), arrows = share of the flow, labels in percent; fixed colours from a palette checked for
  colour-blind readers, "others" beyond seven families. Double-click to enter.
- **Page view**: one node per URL, size = log score, colour of your choice (family, business alignment,
  PR × CheiRank quadrant, click depth, Louvain communities). Template links are hidden by default so you
  see the editorial linking; they always count in the computation.
- **Panels**: page card (where its equity comes from, where it goes), starving pages, overfed pages, black
  holes, silo matrix, PR × CheiRank scatter, cannibalisation, model and clean-up (with, when used, the
  clicks / position blend, the coverage, the semantic decay and the loss per segment).
- **What-if**: remove link positions (optionally towards some targets), cap header/nav at N links per page,
  add k editorial links from source pages to targets (regex, starving pages by default). PageRank is
  recomputed in the browser and the deltas show in the tables and on the graph.
- **Export CSV**: the current table, with the simulation columns when one is active.

Beyond `--max-nodes`, the viewer gets a sample (home page, strategic pages, anomalies, best scores, a sample
stratified by segment). The CSVs, `findings.md` and the silo view are still computed on the full graph; the
browser simulation is then flagged as approximate.

## Reading the results

- Position weights are a **practitioner heuristic** inspired by the reasonable surfer patent
  ([US 7,716,225](https://patents.google.com/patent/US7716225B1/en)). Google does not publish its own. Read
  the scores as a **relative order of pages to strengthen**, never as a ranking prediction.
- Orphan pages found are a **floor**: a crawl cannot find its own orphans.
- Weighting is relative to the source page: adding contextual links to a page dilutes its navigation
  links; that is a lever in itself.
- The deficit matters more than the ranking: the pages to push are those whose received equity is below
  their business importance, not those already at the top.
- The log 0-100 score is a relative position **within this report**: it depends on the site's minimum and
  maximum and cannot be compared from one analysis to another. To follow a page over time, read its equity
  share (`pr_share_pct`) and its rank; `diff` does the same.
- PageRank × CheiRank quadrants describe a position in the graph, never a decision: a page that is "little
  linked and links little" is investigated with its clicks and impressions before any action.

## Out of scope, extension points

JavaScript rendering (the crawler reads the HTML as served), time decay, distributed computation. On the
semantic side, the tool does not compute the vectors: produce them upstream (TF-IDF, an embedding model, your
choice) and provide them as CSV; the `max(0, cos) ** decay` penalty is a documented function, not a
reconstruction of Google's Topic-Sensitive PageRank. On the click side, no extraction from logs or BigQuery:
provide the `source,target,transitions` triplet (same-origin referrer from server logs, GA4 BigQuery export
or Matomo Transitions API).
Entry points: `prgraph/loaders.py` (new formats), `prgraph/connectors.py` (other business sources: Matomo,
BigQuery), `prgraph/weights.py` (other link weightings), `prgraph/pagerank.py` (teleportation variants),
`prgraph/analysis.py` (new diagnostics), `prgraph/viewer/template.html` (views).

## Project structure

```
prgraph/
  cli.py          compute | simulate | demo | crawl | crawl-static | serve | fetch-gsc | fetch-ga4 | gsc-sites
  crawl_live.py   live crawl (robots, 301, canonical, sitemap, DOM positions)
  server.py       local server: launcher, crawl + business connectors + background computation, reports
  connectors.py   Search Console (Search Analytics, pages of 25,000), GA4 (Data API), credentials, merge by URL
  loaders.py      input formats (including GSC / GA4 exports, prefixed relative paths), weight profiles, segments
  clean.py        normalisation, 301 / canonical resolution, leaks, merging of multiple links
  weights.py      observed clicks (blend with a floor), semantic component (cos ** decay, renormalisation)
  graph.py        CSR matrix, outgoing sums, flow per link
  pagerank.py     power iteration, dead ends, CheiRank, log score
  analysis.py     real depth, quadrants, alignment, silos, black holes, cannibalisation
  simulate.py     graph change rules and comparison
  actions.py      business classes, simulated action plan (effort, quick wins), developer brief
  diff.py         comparison of two analyses: regressions on protected targets, vanished links
  export.py       CSV, findings.md, graph.json, report.html
  demo.py         synthetic site, demo transitions.csv and embeddings.csv
  crawl_static.py HTML folder → links with DOM position
  viewer/template.html  viewer (sigma.js 3, graphology, ForceAtlas2, Louvain), shipped with the package
  viewer/present.html   Presentation mode (client deck), inserted into report.html
  viewer/vendor/        embedded libraries (graphology, graphology-library, sigma.js) and their MIT licenses
  workbook.py     formatted Excel workbook (report.xlsx)
  viewer/launcher.html  start page of the local app
pyproject.toml  installable package, `prgraph` command
tests/                pytest tests (model checked against networkx, clean-up, analyses, weightings, mocked connectors, end to end)
docs/design.md        design note
LICENSE               MIT license
```

## Contributing, reporting a vulnerability

The contribution guide is in `CONTRIBUTING.md` (development install, tests, conventions, synthetic test data
only). Report a security vulnerability privately, never in a public issue: see `SECURITY.md`. Tests run
automatically on GitHub on every change (Linux and Windows, Python 3.11, 3.13 and 3.14).

## License

MIT, see `LICENSE`. Free to use, modify and redistribute, including in a commercial product, as long as the
license notice is kept.
