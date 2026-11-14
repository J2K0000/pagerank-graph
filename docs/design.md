# pagerank-graph: design note

## 1. Problem solved

Put the map of business priorities (margin, conversions, clicks) next to the map of actual link power
(weighted internal PageRank), show where link equity really flows, and make it visible as a graph to product
and tech teams.

## 2. What the tool adds

A PageRank weighted by link position takes a few lines to compute; what is usually missing is scale, the
clean-up beforehand, the business reading and the visualisation. The tool reads an edge CSV
`source,target,position,nofollow,anchor` (or the Screaming Frog "All Inlinks" export) and adds:

| Need | Answer |
|-----------------|---------|
| Scale (10^5 to 10^6 pages) | URL → integer hash table, scipy CSR matrix, vectorised power iteration |
| Reasonable surfer | Weights by position, multiple links merged (most visible weight kept) |
| Clean-up first | 301 / canonical chains resolved, 404 = lost equity, nofollow = equity thrown away |
| Dead ends | Mass of pages with no way out redistributed (uniform, teleportation vector or home page) |
| Biased teleportation | `--restart`: observed entry vector (Search Console, backlinks) |
| Log 0-100 normalisation | `score = (ln PR - ln PRmin) / (ln PRmax - ln PRmin) * 100` |
| CheiRank | Same computation on the reversed graph, quadrants `puits` (sinks) / `hub` / `passoire` (leaky) / `noeud_mort` (dead nodes) |
| Business alignment | Share of value against share of PageRank, per page and per segment |
| Silo tightness | Matrix of PageRank flows between segments, leak share per segment |
| Black holes, over-linked pages | Dead-end pages with high inflow, utility pages at the top of the score |
| What-if | `simulate` sub-command (Python) and simulation panel in the viewer (JS) |
| Hairball | Contraction into meta-nodes per segment, capped export of detailed nodes |
| Communication | Self-contained `report.html` (sigma.js + ForceAtlas2), shareable without a server |

## 3. Architecture

```
prgraph/
  cli.py          compute | simulate | demo | crawl | crawl-static | serve | fetch-gsc | fetch-ga4 | gsc-sites
  connectors.py   connected business sources: Search Console (Search Analytics), GA4 (Data API), credentials, merge
  loaders.py      CSV reading (positions format, Screaming Frog, GSC, GA4, relative paths), segment rules
  clean.py        URL normalisation, 301 / canonical resolution, status filtering, edge merging
  weights.py      weighting beyond position: observed clicks (blend), semantic component (cos ** decay)
  graph.py        URL → id table, weighted CSR matrix, outgoing sums, flow per edge
  pagerank.py     power iteration, dead ends, teleportation, CheiRank, log score
  analysis.py     segments, real click depth, quadrants, business alignment, leaks, black holes
  simulate.py     graph change rules and before / after comparison
  export.py       pages.csv, segments.csv, flows.csv, findings.md, graph.json, report.html
  demo.py         synthetic site (mega menu, huge footer, pagination, PDF, 301, 404, cannibalisation)
  crawl_static.py static HTML folder (Astro dist) → edges with position detected in the DOM
  viewer/template.html  viewer, data injected into the page (shipped with the package)
tests/                pytest
```

Pipeline: `loaders → clean → graph → [weights: clicks, semantics] → pagerank (PR, CheiRank) → analysis → export`.

## 4. Mathematical model

Default weighting (`raisonnable` profile): content 1.0, breadcrumb 0.5, sidebar 0.25, header / nav 0.2,
footer 0.05, unknown 0.6. `modere` profile: header / nav 0.35, footer 0.10. Override:
`--weights "footer=0.02,nav=0.3"`.

For a source page v with outgoing sum S(v) (including the weights of nofollow links and of links to 404s,
which count in the denominator but pass nothing):

```
PR(u) = (1 - d) * t(u) + d * [ sum_v w(v,u) / S(v) * PR(v) + m * r(u) ]
```

t = teleportation vector (uniform or `--restart`), m = mass of dead ends and leaks at the current iteration,
r = redistribution vector (`--dangling uniform|teleport|home`). Stops when the L1 norm of the difference is
< 1e-9 or after 200 iterations. Sum = 1 at every iteration.

CheiRank: same procedure on the transpose (reversed edges, same weights).

Leaks (weight in the denominator, nothing passed): link to a 4xx/5xx, nofollow / sponsored / ugc link, link
whose final target is on another host, link to a URL known but not fetched (status `robots`, `uncrawled`) or
found but missing from the URL table (`--uncrawled leak`, the default).

Template inference (`clean.py`): a target linked as `content` / `unknown` from at least `boilerplate_share`
of the linking pages (floor 20, cap `boilerplate_min_links`) gets these links requalified as `boilerplate`
with weight `max(w_boilerplate, w × (1 − share))`; never for a strategic or money page (`protect`). List of
targets in `stats["boilerplate_target_list"]`.

The teleportation vector (`--restart`) is kept by `simulate`, the action plan and the viewer's what-if
(`meta.teleport`).

### 4bis. Weighting beyond position (`weights.py`)

Two optional adjustments of the weights w(v,u), applied in this order before PageRank.

**Observed clicks** (`--transitions`, `--data-blend b`, default 0.7). For any source page v with at least
one observed transition:

```
w'(v,u) = S_e(v) × [ b × T(v,u) / T(v) + (1 − b) × w(v,u) / S_e(v) ]
```

T(v,u) = observed transitions v→u, T(v) = outgoing transitions of v, S_e(v) = heuristic sum of the weights
of v's edges (leaks excluded). The mass sent by v is kept, and so is its leak share. An edge never clicked
on a page that has data keeps (1 − b) × w: the floor avoids the circularity trap (an invisible link would
vanish from the model and could no longer be diagnosed), hence b = 1 is refused. Pages without data keep the
heuristic. Coverage reported: share of edges and of heuristic mass covered, source pages with data, pairs
not found.

**Semantic component** (`--embeddings`, `--semantic-decay γ`, default 0.5). With e(v) the normalised vector
of page v:

```
f(v,u) = max(0, e(v) · e(u)) ** γ        w''(v,u) = w'(v,u) × f(v,u) × S'(v) / Σ_u w'(v,u) f(v,u)
```

Renormalising per source keeps the mass sent: an off-topic link passes less, its on-topic neighbours get the
difference. Factor 1 for an edge with an end that has no vector; original weights kept if every edge of a
page drops to zero; γ = 0 turns the penalty off. An edge of zero weight stays in the graph but passes
nothing (the denominator is protected).

**Thermal loss** (`analysis.py`, `w_ref` = weights before the penalty). At the effective stationary
PageRank, reference flow `α PR(v) w'(v,u) / S'(v)` against effective flow `α PR(v) w''(v,u) / S''(v)`,
aggregated by target segment: `thermal_in_ref`, `thermal_in_eff`, `thermal_loss` (positive = the segment was
receiving equity through off-topic links), plus the total mass rerouted and the flow between different
segments before / after. Reported in `findings.md` § 2 bis, `graph.json` (`thermal`) and the viewer's
« Modèle » (model) tab.

## 5. Inputs

- `--edges`: CSV `source,target,position,nofollow,anchor` (positions), Screaming Frog "All Inlinks" export
  (columns `Source, Destination, Follow, Link Position, Type`), or `source,target[,weight]`. Detected from
  the header.
- `--urls`: CSV `url,status[,redirect_target][,canonical][,segment][,title]` or Screaming Frog
  `internal_html` export (`Address, Status Code, Redirect URL, Canonical Link Element 1`). Without this file,
  every node is assumed to answer 200. Literal statuses `robots` and `uncrawled` = URL not fetched (status 0:
  neither a node nor a valid target).
- Business classes (`analysis.class_of`): decided by path segments (content section as first segment, then a
  whole legal segment or one with a legal suffix, a whole trust segment, a money or acquisition word at the
  start or end of a segment; root of a language = home), six classes (`money`, `acquisition`, `confiance`,
  `legal_support`, `neutre`, `accueil`); `--labels` keeps regexes.
- Business data: number convention decided per column (`loaders.comma_is_thousands`).
- `--business`: CSV `url` + value columns (`revenue, conversions, clicks, sessions, impressions,
  crawl_hits`), Search Console "Pages" or GA4 "Pages and screens" export accepted (`--business-base-url`
  prefixes relative paths). `--value-col` forces the column.

### 5bis. Connected business sources (`connectors.py`)

- **Search Console**: `searchanalytics.query` with `dimensions=[page]`, `type=web`, `rowLimit=25000`,
  `startRow` incremented as long as a full page comes back; output `url, clicks, impressions, ctr, position`.
  `sites.list` feeds the launcher's list of properties; with no property typed in, `sc-domain:` of the site.
- **GA4**: `runReport` on `landingPagePlusQueryString` (or `landingPage`) with `sessions, keyEvents,
  totalRevenue`, `limit` 100,000 and `offset`; paths are prefixed with the site's origin (`(not set)`
  ignored); output `url, sessions, conversions, revenue`.
- **Credentials**: `GOOGLE_APPLICATION_CREDENTIALS` or `--credentials`; service account, OAuth "installed"
  (token cached in the user folder) or `authorized_user`. Nothing in the project.
- **Merge** (`--merge`, `merge_business`): key = normalised URL, additive metrics summed, CTR and position
  averaged weighted by impressions, missing columns filled with zero.
- **Period**: `--days N` = N complete days ending three days before today (recent data incomplete).
- **Server**: `prepare_business` calls the connector during the computation phase and writes `business.csv`
  in the run folder; `business_coverage` reads `pages.csv` back to report joined URLs and coverage. A
  connector failure does not stop the analysis (explicit log, computation without business data).
- **Reading**: when `impressions` is available without being the value column, `analysis.py` computes a
  secondary "unserved demand" list (80 % cumulative impressions, score below the starving threshold, outside
  the starving list) exposed in `findings.md` § 4 bis, `graph.json` (`top.demand_starving`, `demand` field of
  nodes, `meta.demand_col`, `meta.business_cols`) and the « Famine » (starving pages) tab.
- `--segments`: rules `name<TAB>regex` (first match) or CSV `url,segment`. Default: first path segment.
- `--strategic`, `--restart`, `--home`, `--utility-regex`, `--cannibalisation` (CSV `url,group`).
- `--transitions`: CSV `source,target,transitions|count|clicks|sessions` (observed page-to-page clicks).
- `--embeddings`: CSV `url` + numeric columns, or `url,vector` (one JSON vector per line).

## 6. Outputs (`--out folder/`)

`pages.csv`, `segments.csv`, `flows_segments.csv`, `findings.md` (in French, tool version and date at the
top), `graph.json`, `report.html`. The viewer embeds `graph.json`: a single file to send.

## 7. Viewer

- sigma.js graph, ForceAtlas2 layout (worker), size = log score, colour of your choice: segment, business
  alignment (starving, overfed, aligned, neutral), PR × CheiRank quadrant, click depth.
- Filters: link positions, segments, minimum score, URL search.
- Contracted view: one meta-node per segment, edges = PageRank flows, label = leak share; back to the
  detailed view with a double-click.
- Panels: page card (inbound / outbound links with position), tables of starving pages, overfed pages, black
  holes, leak matrix, PR × CheiRank scatter.
- Client-side what-if: remove a link position, cap navigation links, add links by regex; recomputation in JS
  (CSR, typed arrays) and deltas displayed.
- Beyond `--max-nodes` (3000 by default) the detail is sampled (home page, strategic pages, anomalies, top
  score, sample per segment); the contracted view stays exact and the client what-if is flagged as
  "approximate".

## 8. Out of scope (on purpose)

JavaScript rendering, computing the semantic vectors (provided as CSV, produced upstream), extracting
transitions from logs or BigQuery (provided as CSV), time decay, distributed Spark computation. Extension
points are listed in the README.
