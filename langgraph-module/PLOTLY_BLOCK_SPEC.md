# The `​```plotly` block

This is the contract for embedding a chart in a generated dashboard markdown
file. It replaces the previous `​```mermaid` fences. It is written for
whoever implements rendering in the BudgetKey viewer — nothing in this repo
renders these blocks yet except the local preview script described at the
bottom.

## Syntax

A fenced code block tagged `plotly`, containing one JSON object, fully
hardcoded (no server round-trip, no external data references):

````markdown
```plotly
{
  "data": [
    { "type": "scatter", "mode": "lines+markers", "name": "תקציב מקורי",
      "x": [2016, 2017, 2018], "y": [41671518000, 47630805000, 48033338000] }
  ],
  "layout": {
    "title": "מגמה תקציבית לאורך זמן",
    "xaxis": { "title": "שנה", "type": "category" },
    "yaxis": { "title": "תקציב ב-₪", "rangemode": "tozero", "separatethousands": true }
  }
}
```
````

The object has two required keys and one optional one:

- `data` — an array of Plotly trace objects, exactly as passed to
  `Plotly.newPlot`. **`chart` is accepted as an alias for `data`** — BudgetKey's
  own item descriptors use `chart` for this array (see the live example
  below), so either key name must work.
- `layout` — a Plotly layout object, merged into the renderer's own base
  layout (see "Renderer implementation" below).
- `config` (optional) — a Plotly config object, merged into the renderer's
  base config.

All numbers are bare JSON numerals — no `₪`, no thousands separators, no
quoted strings for values. All text (titles, labels, trace names) is a plain
JSON string; standard JSON escaping (`\"` for an embedded quote) is all that
is ever needed — there is no analog to Mermaid's fragile label-quoting rules.

## Renderer implementation (reference)

This is what BudgetKey's own Angular component does for the equivalent
descriptor-driven charts (`budgetkey-app`,
`src/app/charts/chart-plotly/chart-plotly.component.ts`), and what the
markdown-fence renderer should replicate:

```js
const layout = Object.assign({ height: 600, font: { size: 10 } }, spec.layout);
const config = Object.assign({ responsive: true }, spec.config);
Plotly.newPlot(el, spec.data ?? spec.chart, layout, config);
```

On a JSON parse failure, render the raw fence text as a `<pre>` block rather
than failing the whole page (same fallback behavior `MermaidBlock.tsx` uses
today for a bad Mermaid diagram).

**Bundle:** every trace type used by the generator (`scatter`, `pie`, `bar`)
is registered in `plotly-basic`, the same slim bundle BudgetKey already loads
from `https://cdn.plot.ly/plotly-basic-2.26.1.min.js`. No larger bundle is
required for this change. (Trace types like `sankey`, `treemap`, and
`sunburst` are *not* in `plotly-basic` — if a future dashboard wants a true
hierarchy visual instead of the flattened pie described below, that's the
trigger to switch to the full `plotly-2.26.1.min.js`.)

## Chart types used by the generator

### 1. Trend over time — `scatter`

One trace per budget series (original / after changes / executed), `x` =
years, `y` = amounts. Mirrors the live chart at
`https://next.obudget.org/i/budget/C111/2026`:

```json
{
  "data": [
    { "type": "scatter", "mode": "lines+markers", "name": "תקציב מקורי", "x": [2016, 2017], "y": [41671518000, 47630805000] },
    { "type": "scatter", "mode": "lines+markers", "name": "אחרי שינויים", "x": [2016, 2017], "y": [55624343000, 57373185000] }
  ],
  "layout": {
    "xaxis": { "title": "שנה", "type": "category" },
    "yaxis": { "title": "תקציב ב-₪", "rangemode": "tozero", "separatethousands": true }
  }
}
```

### 2. Share of a total — `pie`

Used both for the top-15-suppliers breakdown and for the budget-sources
breakdown (see below).

```json
{
  "data": [
    { "type": "pie", "textinfo": "label+percent",
      "labels": ["ספק א", "ספק ב", "אותי (ע\"ר)"],
      "values": [1674159804.8, 893112482.5, 250000000] }
  ],
  "layout": { "title": "התפלגות היקפי התקשרויות לפי ספק" }
}
```

### 3. Budget sources ("מקורות תקציב") — `pie`, not Sankey

The original hackathon brief (`Topic Page.md`) asked for a Sankey diagram of
budget sources. This isn't used: the obudget site's own "sources" visuals
aren't Plotly Sankey diagrams, and `sankey` isn't in `plotly-basic` anyway.
Instead this section uses the same `pie` shape as above — one slice per
top-level office/program the hierarchy rolls up to, `values` = each branch's
total budget. The actual parent→child structure is conveyed separately by
the nested bulleted list of budget line items that follows this chart in the
template, not by the chart itself.

### 4. Ranking — `bar`

Available for any section that needs a ranked comparison (e.g. as an
alternative to a pie when there are too many categories to read as slices):

```json
{
  "data": [
    { "type": "bar", "x": ["ספק א", "ספק ב", "ספק ג"], "y": [1674159804.8, 893112482.5, 250000000] }
  ],
  "layout": { "yaxis": { "title": "תקציב ב-₪", "separatethousands": true } }
}
```

## Local preview (this repo only)

`langgraph-module/preview_plots.py` extracts every `​```plotly` fence from a
generated report, validates it's parseable JSON, and renders a standalone
HTML page using the same `plotly-basic` bundle and layout/config merge
described above — see that script for usage. This is a development aid only;
it is not part of the contract above and the BudgetKey viewer doesn't need
to match its HTML output, only the `newPlot` call semantics.
