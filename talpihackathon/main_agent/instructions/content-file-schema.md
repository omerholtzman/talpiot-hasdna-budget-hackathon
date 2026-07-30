# Content File Schema

Defines the exact shape a generated dashboard file (`content/**/*.md`) must have.
This is the contract between the generator (`main_agent/`, driven by the BudgetKey
MCP tools) and the viewer (`server/` + React app), per `prd.md`. A file that
doesn't match this schema either fails to parse (skipped from the sidebar) or
renders with missing/incorrect metadata.

## 1. Frontmatter (metadata)

Every file starts with a YAML frontmatter block, exactly the fields below — no
more, no fewer. The server parses this into `DashboardMeta`; unknown extra
fields are ignored, missing required fields make the file fail to parse.

```yaml
---
title: תקציב טיפות חלב בישראל
created: 2026-07-30
updated: 2026-07-30
model: claude-sonnet-5
path: reports/tipat-chalav
---
```

| Field     | Type   | Required | Meaning                                                        |
| --------- | ------ | -------- | ---------------------------------------------------------------- |
| `title`   | string | yes      | Display name shown in the sidebar and detail header.              |
| `created` | date   | yes      | `YYYY-MM-DD`. When the dashboard was first generated.             |
| `updated` | date   | yes      | `YYYY-MM-DD`. When it was last refreshed. Sidebar sorts on this.  |
| `model`   | string | yes      | Model that produced the file, e.g. `claude-sonnet-5`.             |
| `path`    | string | yes      | Free-form source/category tag shown in the header. Independent of the file's slug — does not need to match the folder it's saved in. |

Rules:

- Dates may be quoted (`"2026-07-30"`) or bare YAML dates — the server
  normalizes both to `YYYY-MM-DD` strings. Prefer quoting to avoid YAML
  parsing it as a `Date` object with unexpected timezone shifting.
- `title` may be Hebrew or English; the body may mix both (see
  `hebrew-example.md` / `tipat-chalav.md` for precedent).
- `updated` must be `>= created`.
- The file's path under `content/` (minus `.md`) is the slug (URL identity).
  `path` is separate, human-facing metadata — do not conflate the two.

## 2. Body content

After the frontmatter, the body is GFM Markdown. Three things are expected of
every non-trivial dashboard: graphs, cross-links, and sourced/reliability
information.

### 2.1 Graphs

Use fenced ` ```mermaid ` blocks for any data with a natural visual shape —
distributions, trends over time, or process flow. Rendered client-side as SVG;
diagram source must be valid Mermaid syntax (no HTML injection — the viewer
renders with `securityLevel: 'strict'`, so `click`/script bindings are
stripped).

Guidance on which diagram type to reach for:

| Data shape                              | Mermaid diagram   |
| ---------------------------------------- | ------------------ |
| Share of a total across categories       | `pie`              |
| Trend across time (multiple series)      | `xychart-beta` or a Markdown table (Mermaid has no native multi-series line chart — prefer a table for exact figures, a chart only for the shape of the trend) |
| Process / pipeline / data flow           | `flowchart`        |
| Sequence of events between actors        | `sequenceDiagram`  |

Every chart's numbers must also appear in an adjacent Markdown table when
precision matters (see `q1-metrics.md`, `tipat-chalav.md`) — the diagram
communicates shape, the table carries the exact values and is what a reader
can actually check against the source.

### 2.2 Links to other pages

Cross-link related dashboards using relative Markdown links, resolved against
the **current file's directory**:

```markdown
See the [product overview](../overview.md).
[Q1 metrics](./reports/q1-metrics.md)
```

Rules:

- Relative links to another `.md` file under `content/` are resolved
  client-side and navigate without a full page reload. Always link to the
  `.md` file itself (not a slug URL) — the app resolves the relative path the
  same way the filesystem would.
- External links (`http://`, `https://`, `mailto:`) are left as normal links
  and open in a new tab. Use these for citing source records (see 2.3).
  Never rewrite an external URL into a relative path.
  - Do not fabricate URLs. Only link to sources actually returned by the MCP
    tools (`DatasetInfo` / `DatasetFullTextSearch` / `DatasetDBQuery`) during
    generation.
- Don't link outside `content/` or use absolute filesystem paths — the server
  rejects any resolved path escaping `content/` (404), and such a link would
  never navigate correctly client-side anyway.
- Prefer linking back to a natural "parent" page (e.g. an overview or index
  dashboard) at the end of the body, so a reader can always navigate back up.

### 2.3 Information reliability

Because every figure in a dashboard originates from a live MCP query against
real budget data, the body must let a reader trace a number back to its
source and judge how solid it is. Every dashboard that presents figures (not
pure narrative) must include:

1. **A closing `## Sources` section** (or `## מקורות` for Hebrew dashboards)
   listing the concrete records the figures came from, as external links —
   typically `item_url` values returned by `DatasetDBQuery`, or dataset/program
   identifiers (e.g. a `program_key`). Do not summarize without attaching at
   least one traceable source link per major claim.

2. **Actual vs. projected data marked inline.** Budget data mixes originally
   planned, revised, and executed amounts, and the most recent year is often
   incomplete. Mark incomplete/not-yet-executed figures explicitly rather than
   presenting them as final — e.g. a table cell of `טרם בוצע` / "not yet
   executed" or "pending" instead of a number, or a footnote flagging an
   estimate.

3. **A stated coverage window.** Call out the year/date range the data spans
   (e.g. "מכסים את השנים 2016–2026") so a reader knows what's in scope and
   what predates or postdates the report.

4. **Caveats on anomalies called out, not silently presented.** If a number
   looks like an outlier (a large deviation, revised budget far exceeding the
   original, etc.), name it and offer the most likely explanation available
   from the data — don't just print the table and move on.

Narrative-only sections (e.g. a "Notes" section giving color on a chart) don't
individually need citations if the section immediately above already sourced
the underlying figures.

## 3. Minimal template

```markdown
---
title: <Display Title>
created: <YYYY-MM-DD>
updated: <YYYY-MM-DD>
model: <model-id>
path: <free-form/tag>
---

## <Section: e.g. summary>

<Narrative, with coverage window and any caveats.>

## <Section: e.g. main data table/chart>

| ... |

​```mermaid
pie title ...
​```

## Sources

- [<record description>](<item_url or dataset link>)

Back to the [<parent dashboard>](<relative-link>.md).
```

