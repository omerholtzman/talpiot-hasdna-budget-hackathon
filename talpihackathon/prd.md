# PRD: Markdown Dashboard Viewer

## Problem

An external script connects to an MCP server, pulls data from the web, and asks a
model to summarize it into a Markdown "dashboard" file. There's no way to browse
these dashboards — they're just files on disk. We need a local viewer that turns a
directory of generated `.md` files into a navigable, readable set of dashboards.

## Goals

- Present every `.md` file in `content/` as a dashboard: title, metadata, rendered body.
- Support diagrams (Mermaid) and standard GFM markdown (tables, lists, links, images).
- Let dashboards link to each other (including nested subfolders) and navigate
  between them without a full page reload.
- Pick up new/changed files written by the generator script without a rebuild —
  a browser refresh is enough.

## Non-goals (v1)

- Building the generator script itself (MCP calls, web fetching, model summarization).
- Live/auto-reload while the app is open (chokidar/SSE watching `content/`).
- Multi-user, auth, or anything beyond a single local user on one machine.
- A production/served-build mode (Express serving `dist/`) — this is a dev-time tool,
  run via `npm run dev`.

## Users

Just the person running the pipeline locally — no shared/hosted deployment.

## Pipeline

```
generator script (not built here)
  → MCP tool calls + web fetch
  → model writes content/<slug>.md  (frontmatter + markdown body)
  → this app reads content/ and renders it
```

## Data model: dashboard frontmatter

Each `.md` file in `content/` starts with a YAML frontmatter block:

```yaml
---
title: Q1 Metrics Report
created: 2026-07-15
updated: 2026-07-30
model: claude-opus-5
path: reports/2026-q1
---
```

| Field     | Type   | Meaning                                            |
| --------- | ------ | --------------------------------------------------- |
| `title`   | string | Display name (sidebar + detail header)              |
| `created` | date   | When the dashboard was first generated              |
| `updated` | date   | When it was last refreshed; sidebar sorts on this    |
| `model`   | string | Which model produced it                              |
| `path`    | string | Free-form source/category tag, shown in the header  |

Dates may be quoted or unquoted YAML — the API normalizes both to `YYYY-MM-DD`
strings (unquoted YAML dates parse as JS `Date` objects via `gray-matter`/`js-yaml`,
which the server coerces before returning).

The file's own location under `content/` (minus the `.md` extension) becomes its
**slug** — e.g. `content/reports/q1-metrics.md` → slug `reports/q1-metrics`, URL
`/d/reports/q1-metrics`. `path` in the frontmatter is independent, free-form
metadata — it does not have to match the slug.

## Functional requirements

1. **Sidebar** lists every dashboard (title, model, updated date), sorted by
   `updated` descending. Clicking navigates to `/d/<slug>`.
2. **Detail pane** renders the selected dashboard: a header (title, model badge,
   created/updated dates, `path`) followed by the rendered markdown body.
3. **Markdown rendering** supports GFM (tables, task lists, strikethrough, etc.)
   via `remark-gfm`.
4. **Diagrams**: fenced ` ```mermaid ` code blocks render client-side as SVG via
   the `mermaid` package.
5. **Cross-links**: a relative link to another `.md` file in `content/` (e.g.
   `[Q1](./reports/q1-metrics.md)` or `../overview.md`) resolves against the
   current dashboard's directory and renders as an in-app link (client-side
   navigation, no reload). External links (`http(s):`, `mailto:`) open normally
   in a new tab.
6. **Empty state** at `/` when no dashboard is selected, and a not-found state for
   an unknown slug.

## Architecture

- **Frontend**: Vite + React 19 + TypeScript, `react-router` for `/` and `/d/*`.
- **Local API**: a small Express server (`server/`) that scans `content/` at
  request time — not a build-time bundle — so new files show up on refresh.
- Vite dev server proxies `/api/*` to the Express server (`vite.config.ts`,
  `server.proxy`), so the browser only ever talks to one origin. `npm run dev`
  starts both processes together via `concurrently`.
- The Express server binds to `127.0.0.1` only — this is a local-only tool, not
  meant to be reachable on the network.

### API contract

`GET /api/dashboards` → `200 { dashboards: DashboardMeta[] }`, sorted by
`updated` desc, metadata only (no body content). A file that fails to parse is
skipped (logged), not fatal to the list.

`GET /api/dashboards/<slug>` → `200 { ...DashboardMeta, content: string }`
(frontmatter-stripped markdown body). `404 { error }` if the slug resolves
outside `content/`, doesn't exist, or isn't a `.md` file.

Path-traversal is the one real security boundary in this app (slugs come from
the URL): the server resolves the absolute path and verifies it stays inside
`content/` before ever reading a file, rejecting `.`/`..` segments outright.

## Out of scope / accepted risks (v1)

- **No compiler-enforced type sharing**: the Express server is plain ESM JS (so
  it's directly runnable via `node server/index.js`, independent of the `tsc -b`
  project); its response shape is kept in sync with `src/features/dashboards/types.ts`
  by convention, not by import.
- **Mermaid + `dangerouslySetInnerHTML`**: rendering a diagram requires injecting
  the SVG string `mermaid.render()` returns — there's no non-dangerous API for
  this. Diagram source comes from local, user-generated files (trusted), and
  mermaid's `securityLevel: 'strict'` sanitizes its own output.
- **No live-reload**: the generator script can write files at any time; the app
  only sees them on the next `/api/dashboards` fetch (i.e. a browser refresh).
- **`content/` in git**: the 3 seed dashboards are tracked; everything else under
  `content/` is gitignored, since future generator output shouldn't be committed.

## Success criteria

- Sidebar shows all dashboards in `content/`, correctly sorted.
- Clicking a dashboard renders its metadata, GFM markdown, and any Mermaid
  diagrams correctly.
- Cross-links between dashboards (including nested folders, `./` and `../`)
  navigate client-side to the right dashboard.
- A crafted traversal slug (e.g. containing `..`) returns 404, never file content
  outside `content/`.
- `npm run build` and `npm run lint` pass clean.
