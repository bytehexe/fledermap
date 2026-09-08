# Fledermap Statistics — Design

**Status:** approved 2026-09-08 — prerequisite Species/Site list+detail pages now exist; "Page
layout" section added the same day after a mockup session settled the dashboard-vs-plain-sections
question. Ready for an implementation plan.
**Date:** 2026-09-05 (page-layout addition: 2026-09-08)

## Problem

The Obsidian backlog's "Statistics" item asks for a dashboard covering Global/Per-site/
Per-species scopes: pie charts (recordings per species), stat tiles (total recordings), and line
charts (recordings per time-of-year and per time-of-night, per species) — explicitly flagged
`[!!refine]`, i.e. too vague to implement as written. Nothing in the app currently aggregates
recordings beyond the map's per-request filtered set and the sessions list; there is no query
layer, page, or chart-rendering mechanism for this today.

This is primarily a **citizen-scientist engagement** feature (per discussion — "look what I've
recorded"), with a secondary but real use as a lightweight monitoring view (which sites are
active, when, for which species). It is explicitly not meant to replace serious ecological
analysis tooling.

## Prerequisite

This design assumes the backlog's separate **Species list/detail** and **Site list/detail**
pages (both currently unchecked) already exist, sequenced before this feature. Statistics does
not build them; it links to and embeds into them (see "Pages" below). Do not schedule this
spec's implementation before those pages exist.

## Goals

- A `/statistics` area covering three scopes — global, per-site, per-species — sharing one query
  layer and one set of chart components, not three parallel implementations.
- Charts that are genuinely visually appealing (real hover/tooltip interactivity, the project's
  existing taxon marker colors carried through) and easy enough to reason about that they don't
  become their own maintenance burden.
- Numbers that are always correct against the live database, with no caching/invalidation logic
  to design or maintain.

## Non-goals

- **No per-session statistics scope.** A single session is too sparse for pie/line charts to say
  anything meaningful. If a session-level chart earns its place later, it's a small addition to
  the existing session detail page, not part of this feature.
- **No timezone-conversion work.** Time-of-night/time-of-year bucketing uses whatever timestamp
  is already stored (the DB session's timezone, UTC by default) — the same accepted v1 gap
  documented elsewhere in CLAUDE.md, not something this feature fixes. Charts must be honest
  about this (see "Timezone honesty" below) rather than silently implying local time.
- **No gauge widgets.** "Gauges/Digits" from the backlog resolves to plain stat tiles (a single
  headline number), per the dataviz form heuristic: a bare number is the right form for a single
  total, not a decorative dial.
- **No new species/site list-and-detail pages.** Prerequisite, not part of this spec (see above).
- **No exports (CSV/image download) of charts or data.** Not raised as a requirement; would be
  a separate, later addition if wanted.
- **No filtering the statistics pages by arbitrary date range** in this pass — the charts already
  bucket by month-of-year and hour-of-day; an explicit "last N days" filter is a plausible
  future addition but not designed here.

## Pages

Three pages under one area, plus link-ins from existing pages:

- **`/statistics`** — the global dashboard: stat tiles, global species-composition donut,
  global time-of-year and time-of-day line charts, plus a rarest-species list (see below).
- **`/statistics/species/<taxon_id>`** — that species' share of recordings by site (bar chart)
  plus its own time-of-year/time-of-day lines (single series, no top-N grouping needed since
  the species is already the filter), plus two stat tiles: total recordings of this species and
  distinct sites it's been detected at (both via `totals(session, taxon_id=...)`).
- **`/statistics/sites/<site_id>`** — that site's species-composition donut (top-N + Other,
  scoped to the site) plus its own time-of-year/time-of-day lines, plus three stat tiles: total
  recordings at this site (`totals(session, site_id=...)`), species richness, and Shannon
  diversity index (the latter two via `site_diversity(session, site_id=...)` — see "Site
  diversity" below).

The species detail page and site detail page each get a **"Statistics" link** into their
respective sub-page — the same interlinking convention as the existing "Session"/"Show on map"
links on the recording details page. The global page's donut slices and bar-chart bars are
click-throughs to the matching per-species/per-site page, so browsing flows in both directions:
detail page → its statistics, global statistics → a specific species/site's statistics.

No embedding of charts directly on the species/site detail pages themselves — a single home for
all statistics avoids duplicating chart-rendering code onto pages that already have their own
job (recording lists, metadata) to do.

### Name and label linking

Two distinct kinds of click target exist on these pages, and every name/label consistently picks
one or the other rather than mixing them per-chart:

- **Chart elements** (a donut slice, a bar in the site-ranking chart) click through to that
  species/site's **statistics sub-page** — this is the behavior already described above for the
  global page's donut/bar-chart drill-down.
- **Plain-text name labels** — the rarest-species list's species names, the site-ranking bar
  chart's axis labels, donut/line-chart legend text — link to that species/site's own **detail
  page** (`/species/<taxon_id>`, `/sites/<site_id>`) instead, since a bare name in a legend or
  table reads as "go look at this thing," not "see more statistics about it." Getting from a
  detail page back to its statistics is already covered by the "Statistics" link mentioned above.

This means a donut can have both: clicking the colored slice goes to the species' statistics
sub-page, clicking its name in the legend goes to the species detail page. Two different clicks,
two different destinations, each doing the thing its own affordance implies.

### Rarest-species list

Global page only (per discussion — a site-scoped version is a plausible later addition, not
built here). A ranked list — plain table/list markup, not a chart, per the dataviz form
heuristic: a handful of small counts doesn't earn an axis — of the bottom-N species **by
recording count, among species with at least one current-best recording**. Species with zero
recordings are excluded entirely: "never detected" is a different fact from "rarely detected,"
and would otherwise flood this list with every species in `taxa_eu.yaml`/`taxa_na.yaml` that
happens to be outside the region actually being monitored, which says nothing interesting.
`NOISE`/`NO_ID`/no-identification-at-all recordings and unmapped-species results are also
excluded (they aren't a species); a multi-species recording counts toward every individual
species it contains rather than being folded into one combined bucket — see "Species-breakdown
inclusion rules" below for the full reasoning.

Each row shows the species name, its marker-color swatch (via `colorForTaxon()`, same as every
other chart here), and its recording count; the species name links to that species' own detail
page (`/species/<taxon_id>`, see "Name and label linking" below) — not the statistics sub-page,
which is what chart elements like the donut's slices link to instead. Complements rather than
duplicates the top-N donut: the donut answers "what's common," this list answers "what's rare" —
both drawn from the same underlying per-taxon counts.

A second, companion list — **unmapped codes**, bottom-N by raw code string
(`Identification.raw_label`) among unmapped `SPECIES`-verdict results — sits alongside it.
Unlike the taxon-keyed rarest list, this one has no `taxon_id` to link anywhere, so its rows are
plain text (code + count), not links. It surfaces the same information a review queue would
(rare/unusual unmapped codes are exactly the ones worth checking for a missing `taxon_code` entry
or a typo'd label), without building a separate review-queue page for it.

A third and fourth, unrelated global-only pair of lists — **most species-rich sites** and **most
diverse sites** — sit alongside these two; see "Site diversity" below.

### Site diversity

Two related numbers, both derived from the same per-site species breakdown
`recording_counts_by_site` already computes, but not currently surfaced anywhere:

- **Species richness** — the count of distinct species (current-best `taxon_id`s) ever recorded
  at a site.
- **Shannon diversity index (H)** — richness weighted by evenness: a site with 10 species seen
  roughly equally often ranks higher than one with 10 species where one dominates 95% of
  recordings. Reported as a plain number (natural-log base, the conventional choice for H), not
  normalized to 0–1 — that would be Pielou's evenness, a different metric, out of scope here.

Both use the **same inclusion rules as `rarest_species`** (see "Species-breakdown inclusion
rules" below), for consistency across every species-counting feature on these pages:
`NOISE`/`NO_ID`/no-identification recordings excluded entirely; unmapped species excluded (no
taxon to count); a multi-species recording counts toward **every** taxon it contains, using the
same membership rule `recording_counts_by_site`'s `taxon_id` filter already uses.

**Richness and Shannon can rank sites differently** — a site with fewer species spread evenly can
out-score a richer site dominated by one species — so a single list sorted one way and captioned
with the other's number would misrepresent whichever metric it wasn't sorted by. Two separate
top-N lists instead, same pattern as the existing rarest-species/rarest-unmapped-codes pair:

- **Most species-rich sites** — ranked by richness descending.
- **Most diverse sites** — ranked by Shannon H descending.

Each row in both lists shows the site name (linking to `/sites/<site_id>`, plain-text-label
convention — same reasoning as the rarest-species list's species-name links) plus **both**
numbers, so a reader can cross-reference a site's rank on the metric the list isn't sorted by.

Two global-page surfaces, one site-page surface, one query function:

- **Global page**: the two ranked lists above, plain list markup like the rarest-species list — a
  handful of rows doesn't earn a chart axis.
- **Site statistics page**: two plain stat tiles, "Species richness" and "Diversity index
  (Shannon H)", for that one site — **not gauges**. Richness and Shannon are both open-ended
  single totals with no natural min/max to scale a dial against, exactly the case the "No gauge
  widgets" non-goal above already rules out; a bare number is the right form here too.

All three surfaces are one function, `site_diversity`, since all three need the identical
per-site richness/Shannon computation — see the Query functions section below for its two call
shapes (ranked top-N vs. single site).

### Page × diagram matrix

| Diagram | `/statistics` (global) | `/statistics/sites/<id>` | `/statistics/species/<id>` |
|---|---|---|---|
| Stat tiles: total recordings | ✅ | ✅ (this site) | ✅ (this species) |
| Stat tile: total species | ✅ | — (see richness instead) | — (page *is* one species) |
| Stat tile: total sites | ✅ | — (page *is* one site) | ✅ (sites this species detected at) |
| Species-composition donut (top-N + Other) | ✅ (all recordings) | ✅ (scoped to this site) | — (the page *is* one species) |
| Site-ranking bar chart | — (no single-species filter to rank sites by) | — (the page *is* one site) | ✅ (this species' recordings by site) |
| Recordings-per-month line chart | ✅ (top-N species + Other) | ✅ (top-N species + Other, scoped to this site) | ✅ (single series, this species only) |
| Recordings-per-hour-of-day line chart | ✅ (top-N species + Other) | ✅ (top-N species + Other, scoped to this site) | ✅ (single series, this species only) |
| Rarest-species list | ✅ (global only) | — | — |
| Rarest unmapped-codes list | ✅ (global only) | — | — |
| Most species-rich sites list | ✅ (global only) | — | — |
| Most diverse sites list (Shannon H) | ✅ (global only) | — | — |
| Site diversity stat tiles (richness / Shannon H) | — | ✅ | — |

Stat tiles are all one `totals()` call per scope (global fills all three; site fills only "total
recordings," paired with `site_diversity`'s richness/Shannon tiles; species fills "total
recordings" and "total sites" via membership, since "total species" is meaningless when the page
already is one species). Every chart row is one of the query functions below with `site_id`/
`taxon_id` left unset (global), `site_id` set (site page), or `taxon_id` set (species page) — no
page gets a diagram type the other two don't also support in some form, which is what keeps this
a shared chart-component set rather than three bespoke pages. The rarest-species list, the two
site-diversity lists, and the site diversity tiles are each deliberately scoped to only one page
(see their own subsections above) and so are the rows without a full site/species-scoped
counterpart.

### Page layout: bands + selective cards

Decided by mockup 2026-09-08 (see that session's brainstorm files) after the page's original
plain-`<h2>`-separated-sections description read as a scroll of unrelated fragments rather than a
dashboard. The three pages share one two-level layout:

- **Bands** (a subtle background-tint block, no border) give the page its macro-structure — a
  handful of large named groupings, **each holding however many cards its own content calls
  for** (not a fixed count per band — see "variable card count" below). Global: **Overview**
  (3 stat tiles, no cards), **Species** (3 cards: donut, rarest-species list,
  rarest-unmapped-codes list), **Sites** (2 cards: most-species-rich-sites list, most-diverse-
  sites list — the site-diversity lists are about sites, not species, so they get their own band
  rather than being folded into "Species"), **Activity over time** (2 cards: the month/hour line
  charts). Site page: **Overview** (its one stat tile + the two site-diversity tiles, no cards),
  **Species** (1 card: its donut), **Activity over time** (2 cards: its month/hour lines).
  Species page: **Overview** (its two stat tiles, no cards), **Sites** (1 card: the site-ranking
  bar chart), **Activity over time** (2 cards: its single-series month/hour lines). A band's
  label is a small bold/uppercase caption (`.band-label`), not a full `<h2>`.
- **Cards** (a bordered, white/panel-background box with its own small header bar) appear
  **only where a band holds 2+ widgets meant to be compared side by side** — e.g. the global
  page's donut next to its rarest-species list, or the two line charts next to each other. A
  band holding exactly one widget (the species page's site-ranking bar chart, either page's
  single donut) renders that widget directly in the band, with its own header line but no extra
  card border — a lone widget doesn't need a second box nested inside the band's own tint.
  Stacking a bordered card inside a bordered band was tried and looked cluttered (double-boxing);
  a card box only ever nests inside a *tinted, borderless* band, never inside another card.
- **Variable card count, not a fixed grid.** A band's card area uses a responsive
  `grid-template-columns: repeat(auto-fit, minmax(280px, 1fr))` rather than a hardcoded
  `1fr 1fr` — the global page's Species band holds 3 cards, its Sites/Activity bands hold 2 each,
  the site/species pages' single-card bands hold 1 (rendered without card chrome, per above).
  `auto-fit`/`minmax` lays out whatever count a band actually has (wrapping to a new row past
  however many 280px-minimum cards fit) without per-band CSS.
- The stat-tiles row itself never gets individual card wrapping — `.dash-tile`'s own background/
  border already makes each tile visually distinct, so wrapping the whole row in a second card
  would be a third nested box for no gain.

New shared classes for this, alongside the existing `.entity-list`/`.panel-columns` (see
`docs/style-guide.md`, to be added there as part of implementation): `.stats-band` (the tinted
macro-section), `.band-label`, `.stats-panel` (the bordered per-widget card), `.stats-tile` (a
single stat number — likely a rename/reuse of whatever the mockup called `.dash-tile`, kept
distinct from `.entity-list`'s table rows since a stat tile is a single number, not a row of
comparable fields).

## Data layer

A new `services/statistics.py`, sibling to `services/map_query.py`, doing **live SQL aggregation
on every request** — no cache, no precomputation, no invalidation logic to design. Deliberate
choice: self-hosted single-user/small-group scale means recording counts stay in a range plain
`GROUP BY` queries handle comfortably; revisit only if this stops being true in practice.

Query functions, each taking an `OrmSession` and optional `site_id`/`taxon_id` filters so the
global, per-site, and per-species pages call the *same* functions rather than three separate
implementations:

- `totals(session, *, site_id=None, taxon_id=None) -> Totals` — the plain stat-tile numbers,
  scoped like every other function here. Global (no filter): total recordings, total distinct
  species (taxa with at least one non-superseded, current-best SPECIES-verdict identification),
  total sites. `site_id` set: total recordings at that site only (`total_species`/`total_sites`
  left unset — richness is a different, already-covered metric via `site_diversity`, and "total
  sites" doesn't mean anything scoped to one site). `taxon_id` set: total recordings of that
  species and total distinct sites it's been recorded at, both using the same **membership**
  rule as `recording_counts_by_site`'s `taxon_id` filter (`total_species` left unset — the page
  already *is* one species). Renamed from the single-purpose `global_totals` once the site and
  species pages needed their own scoped totals too.
- `recording_counts_by_taxon(session, *, site_id=None, top_n=8) -> TaxonBreakdown` — ranked
  counts by current-best taxon, plus an `"Other"` bucket for everything past `top_n`. Must use
  `current_best.py`'s current-best-identification logic (the same one the map/drawer/recording
  details page already share), not raw `Identification` rows — a superseded classification must
  never be double-counted alongside its replacement. See "Species-breakdown inclusion rules"
  below for how `NOISE`/`NO_ID`/no-identification-at-all/unmapped/multi-species recordings are
  each handled — none of them is a plain single-taxon match, and the naive "one recording, one
  taxon slice" model doesn't cover them.
- `recording_counts_by_month(session, *, site_id=None, taxon_id=None, top_n=8) -> SeriesByMonth`
  — 12 buckets (month-of-year, irrespective of which calendar year), grouped by top-N species +
  Other when no single `taxon_id` filter narrows it to one species already.
- `recording_counts_by_hour(session, *, site_id=None, taxon_id=None, top_n=8) -> SeriesByHour` —
  24 buckets by **absolute clock hour** (0–23, from the stored timestamp), same grouping rule as
  above. Deliberately not sunset-relative for this pass (see "Hour-of-day vs. hour-of-night"
  below) — labeled "Hour of day," not "of night," so the chart doesn't overstate its own
  precision.
- `recording_counts_by_site(session, *, taxon_id=None) -> SiteBreakdown` — ranked counts by
  site, for the per-species page's "which sites" bar chart. `taxon_id` filters by **membership**
  in the recording's current-best taxon set, not equality — a multi-species recording containing
  the filtered species must still be counted, even though it's not that recording's sole result
  (see "Species-breakdown inclusion rules" below). The per-species page's month/hour single-series
  charts (`recording_counts_by_month`/`recording_counts_by_hour` with `taxon_id` set) use the same
  membership rule for the same reason.
- `rarest_species(session, *, bottom_n=8) -> TaxonBreakdown` — the bottom-N current-best taxa by
  recording count, **excluding taxa with zero recordings** (see "Rarest-species list" above).
  Same `TaxonBreakdown` shape as `recording_counts_by_taxon`, just sorted ascending and with no
  `"Other"` bucket (a bottom-N list has nothing left over to fold in). Global-scoped only — no
  `site_id`/`taxon_id` filter params, unlike every other function here. See "Species-breakdown
  inclusion rules" below for how this differs from `recording_counts_by_taxon` on multi-species
  and unmapped-species handling — the two functions deliberately do **not** treat those cases
  the same way.
- `rarest_unmapped_codes(session, *, bottom_n=8) -> CodeBreakdown` — bottom-N raw code strings
  among unmapped `SPECIES`-verdict results, grouped by `Identification.raw_label` rather than
  `taxon_id` (there is no taxon to group by). A separate function rather than a variant of
  `rarest_species` because its return shape has no `taxon_id`/color/link at all — just a code and
  a count (see "Rarest-species list" above).
- `site_diversity(session, *, site_id=None, sort_by="richness", top_n=8) -> SiteDiversityBreakdown`
  — species richness + Shannon diversity index per site. With `site_id` unset: top-N sites ranked
  by `sort_by` (`"richness"` or `"shannon"`) descending — the global page calls this twice, once
  per sort key, for its two separate lists ("most species-rich sites" / "most diverse sites");
  both numbers are present in every row regardless of which one it's sorted by, so each list can
  show its own cross-reference column. With `site_id` set: the single-row breakdown for that
  site's two stat tiles (`sort_by` is meaningless for one row and ignored). One function, not
  separate ranking/single-site/per-metric variants, since all of them need the identical per-site
  richness/Shannon computation — only the "which column sorts" and "one row vs. top-N rows"
  shapes differ. See "Site
  diversity" above for the inclusion rules (same as `rarest_species`) and why this isn't a gauge.

### Species-breakdown inclusion rules

Not every recording's current-best result is a single clean species match, and the charts above
don't all treat the non-clean cases the same way — this needed its own pass rather than a silent
"one recording, one taxon slice" assumption:

- **Always excluded** from every species-breakdown chart (donut, rarest-species list,
  `recording_counts_by_site`, and the per-species-grouped month/hour lines): recordings whose
  current-best verdict is `NOISE` or `NO_ID`, and recordings with no identification at all
  (`current_best_identification` returns `None`). None of these represents a species, so they
  have no place in a *species* breakdown. This means the donut's slices (+ "Other") will **not**
  sum to the global `total recordings` stat tile — a deliberate gap, not a bug, and the donut
  needs a small caption noting it only covers recordings with a species-level result.
- **Unmapped species** (`SPECIES` verdict, but the raw code never mapped to a `Taxon` —
  `taxon_id is None`): included in the **donut** as its own slice, using the same reserved
  `#333333` color `marker_colors.js` already uses for this case on the map. **Excluded from the
  `rarest_species` list** — that list is keyed by `taxon_id` and every row's name links to
  `/species/<taxon_id>`; an unmapped result has no taxon to link to, so it cannot appear there no
  matter how few recordings share its raw label. It has its own companion list instead —
  `rarest_unmapped_codes`, grouped by raw code string rather than `taxon_id` (see
  "Rarest-species list" above).
- **Multi-species recordings** (`CurrentIdentification.is_multi`, `taxon_ids` has more than one
  member): the **donut** counts each such recording once, under one dedicated "Multiple Species"
  slice using the existing `MULTI_SPECIES_COLOR` — consistent with how the recording's own
  headline already renders ("Multiple Species"), and keeps every recording contributing to
  exactly one donut slice. The **rarest-species list** does the opposite deliberately: it counts
  a multi-species recording once toward **each** individual taxon it contains, since the list's
  purpose is "which species are seldom detected," not "which combined-result classes are seldom
  detected" — so a multi-species recording can add to more than one row in that list, and the
  list's total row-sum can therefore exceed `total recordings`. This is why
  `recording_counts_by_taxon` and `rarest_species` are two separate functions rather than one
  shared implementation with a sort-order flag.
- **`totals`'s global-scope "distinct species" count** must be computed from the full `taxon_ids`
  frozenset of every current-best identification, not just each recording's single/primary
  taxon — otherwise a species that only ever appears as part of a multi-species result would
  never be counted as detected at all.

All return plain dataclasses, not ORM rows — matching `map_query.py`'s existing `SiteDetail`
convention. The month/hour top-N grouping is one shared internal helper
(`_top_n_series_by(...)`), used by both `recording_counts_by_month` and
`recording_counts_by_hour`, so the bucketing/Other-folding logic exists in exactly one place.

### Timezone honesty

Every month/hour chart's axis label and/or a small caption states the timezone actually in use
(e.g. "Hour of day (server time)") rather than presenting the axis as if it were the viewer's
local time. This is a labeling fix, not a conversion fix — no new timezone logic is introduced
by this feature.

### Hour-of-day vs. hour-of-night

`recording_counts_by_hour` buckets by absolute clock hour (0–23), not by time relative to
sunset. Clock hour is ecologically imprecise — a 22:00 recording means a very different point in
the bat night in June vs. December — but sunset-relative bucketing needs a real sunset-time
computation (site coordinates + date, e.g. via the `astral` library) that doesn't exist in this
codebase yet, and belongs to the separate, already-backlogged "H:MM before/after sunset" feature
rather than being scoped into this one.

**Deliberate deferred conversion, not a permanent design choice:** once "time after sunset"
exists, `recording_counts_by_hour` should be revisited to bucket by hours-before/after-sunset
instead of absolute clock hour — the chart will need it anyway, per this session's discussion.
Until then, the axis is labeled "Hour of day," never "Hour of night," so the chart doesn't imply
a precision it doesn't have.

## Chart set

Mapped via the dataviz skill's form heuristic (job → form, color last):

| What | Form | Scope(s) |
|---|---|---|
| Total recordings / species / sites | Stat tile (plain HTML/CSS, no chart) | Global only |
| Recordings per species | Donut, top-N + "Other" | Global, per-site |
| Recordings per site | Horizontal bar, ranked | Per-species |
| Recordings per month-of-year | Multi-line, top-N + "Other" (or single series when already scoped to one species) | Global, per-site, per-species |
| Recordings per hour-of-day | Multi-line, same grouping rule | Global, per-site, per-species |
| Rarest species (bottom-N by recording count) | Ranked list/table, not a chart | Global only |
| Rarest unmapped codes (bottom-N by raw code string) | Ranked list/table, not a chart, no color/link | Global only |

Color: each taxon's donut/line color is the same `colorForTaxon()` value already used for its
map marker (loaded from `marker_colors.js`, which the statistics page also includes) — a species
looks the same color everywhere in the app. The `"Other"` bucket gets a fixed neutral gray,
reserved the same way `marker_colors.js` already reserves gray/orange/etc. for non-taxon meanings
— never handed out by `colorForTaxon()`, so it can't collide with a real species' color. The
donut's "Unmapped species" and "Multiple Species" slices (see "Species-breakdown inclusion
rules" above) reuse `marker_colors.js`'s existing reserved `#333333` and `MULTI_SPECIES_COLOR`
constants for the same reason — one color vocabulary across map and statistics, not a second one
invented for charts.

This does mean the statistics palette does **not** go through the dataviz skill's own generated
categorical palette — it deliberately reuses the app's existing taxon-color system instead, for
cross-page recognizability, and folds anything beyond the top N into "Other" specifically so the
number of distinct hues on any one chart stays within the range dataviz's rules assume (≤8
before folding into Other/small-multiples). `marker_colors.js`'s hash-based colors for taxa
beyond the fixed 10-color palette are not separately re-validated for contrast here — same
open-ended-hash tradeoff the map itself already accepts.

Every chart ships hover/tooltip interactivity (Chart.js's built-in tooltips satisfy this) and a
legend for the donut/line charts (their multi-series nature requires one per dataviz's rules);
stat tiles need neither, being a single number with no plot.

### Info affordance

Every chart's one-line caption (the timezone-honesty label, the species-breakdown exclusion
note, etc.) stays brief by design — a caption that tries to explain everything stops being a
caption. The fuller explanation instead lives behind a small "ⓘ" affordance next to the caption,
revealed on click/tap. A native `<details>`/`<summary>` disclosure covers this with no new JS or
CSS framework: `<summary>` renders as the ⓘ icon, its content is the longer explanation (what's
included/excluded, which timezone, what "Other" folds in). Consistent with this project's
no-build-step JS convention — this is markup and default browser behavior, not a component to
maintain.

## Chart tech

**Chart.js**, vendored the same way Leaflet/htmx/Alpine already are: a pinned version fetched
into `FLEDERMAP_STATIC_ROOT` with a recorded sha256 in `services/vendor_assets.py`'s `ASSETS`
tuple, loaded as a plain `<script>` tag (no build step, matches every other frontend dependency
in this project).

**License check** (per explicit instruction this session): Chart.js is **MIT**. The realistic
alternatives were also checked — ApexCharts is MIT too; ECharts is Apache-2.0 (also acceptable,
but heavier, a worse API fit for this project's minimal-JS style, and its native "gauge" chart
type turned out unneeded once stat tiles cover that job per the Non-goals above). Chart.js is the
recommendation: smallest footprint, covers doughnut/line/bar out of the box, built-in tooltips
satisfy the dataviz hover-layer requirement without custom code.

Pure aggregation/bucketing logic in `services/statistics.py` (the top-N + Other grouping helper,
month/hour bucketing) is Python and covered by ordinary `hatch test` unit tests against a real
`db`-marked fixture, same pattern as `map_query.py`'s existing tests. Any new pure JS (e.g. a
small helper that reshapes a `TaxonBreakdown` dataclass's JSON into the exact array shape
Chart.js's dataset API expects) follows this project's existing split: pure logic in its own
file, `node --test`-covered, no top-level DOM access; anything that actually mounts/updates a
Chart.js canvas needs the mandatory headless-Chrome live-verification pass before being
considered done, per CLAUDE.md's JavaScript tooling section.

## Testing

- `services/statistics.py`'s query functions: `db`-marked tests against seeded
  recordings/identifications/sites, asserting exact counts and correct current-best-only
  filtering (a superseded identification must not appear), same style as the existing
  `tests/test_map_query.py`. `rarest_species` additionally needs a test asserting a
  zero-recording taxon never appears in its result.
- **Species-breakdown inclusion rules** (see that section above) each need their own seeded
  fixture, mirroring how `test_migrations.py`'s per-column blind spots each got a dedicated test
  rather than trusting one general-purpose case to cover all of them: a `NOISE`/`NO_ID`/
  no-identification recording contributes to neither `recording_counts_by_taxon` nor
  `rarest_species`; an unmapped-species recording appears as its own donut slice but is absent
  from `rarest_species`'s results entirely; a multi-species recording appears once under
  `recording_counts_by_taxon`'s "Multiple Species" bucket but once *per taxon* in
  `rarest_species`; and `recording_counts_by_site`/the per-species month/hour charts match a
  multi-species recording by taxon-set membership, not equality.
- Top-N + Other grouping helper: a plain unit test independent of the database (feed it a list of
  `(taxon_id, count)` pairs, assert the top-N/Other split), since it's pure logic once the raw
  counts are in hand.
- New routes (`/statistics`, `/statistics/species/<id>`, `/statistics/sites/<id>`): view tests
  asserting the page renders, stat tiles show correct totals, and both link types from "Name and
  label linking" resolve correctly — a chart element (donut slice/bar) to the statistics
  sub-page, a plain-text name (rarest-species row, legend entry) to the entity's own detail page.
- JS: pure reshaping helpers (if any) via `node --test`; the actual rendered charts via the
  mandatory headless-Chrome pass — screenshot each of the three pages, confirm charts render with
  real data (not an empty canvas), tooltips appear on hover, and click-through navigation works.

## Open follow-ups (explicitly out of scope here, noted for later)

- A "most active site" / "most recorded species" highlight-style tile — considered and declined
  for this pass (plain totals only) but a natural next stat-tile addition.
- ~~**Estimated true richness + sample coverage**~~ — **implemented 2026-09-08**, alongside the
  observed richness/Shannon this pass added. `services/statistics.py`'s `_chao1_richness`/
  `_sample_coverage` compute both directly from the same per-taxon detection counts
  `site_diversity` already builds, no new dependency: Chao1's bias-corrected asymptotic estimator
  (`S_est = S_obs + f1²/(2·f2)`, or `S_obs + f1·(f1-1)/2` when there are no doubletons) and the
  Good-Turing sample-coverage estimate (`C = 1 - f1/n`), where f1/f2 are the counts of taxa
  detected exactly once/twice. The candidate `hillrep` library this entry originally named was
  checked and rejected: it pulls in `pandas` as a new hard dependency (plus a heavy AIRR-immune-
  repertoire-oriented API) just to compute two closed-form numbers this project already had
  every input for. Surfaced as two more Overview stat tiles on the site page ("Est. true richness"/"Sample
  coverage"), and as a third ranked list on the global page's Sites band ("Least-sampled sites",
  `site_diversity(sort_by="coverage")` ranking ascending — lowest coverage, i.e. least-trustworthy
  observed count, first) rather than as a column on the two existing richness/Shannon lists,
  which were themselves fixed at the same time to show only their own sort metric per row
  (previously each showed both numbers, redundantly, in swapped order). No confidence interval
  was added — Chao1's asymptotic variance formula was judged not worth the added complexity for
  a first pass; a straight point estimate plus the coverage percentage already answers "how many
  species are probably here" and "how much do we trust that."
- Date-range filtering on the statistics pages.
- Revisiting live-vs-cached aggregation if recording counts grow large enough that per-request
  `GROUP BY` queries become a real page-load cost.
