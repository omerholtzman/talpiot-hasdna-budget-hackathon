---
title: Getting Started
created: 2026-07-10
updated: 2026-07-20
model: claude-sonnet-5
path: docs/getting-started
---

## How this works

1. A script connects to an MCP server and fetches data from the web
2. The script asks a model to summarize the findings
3. The model's output is written to a `.md` file under `content/`, with
   frontmatter metadata and any diagrams as Mermaid code blocks
4. This viewer reads `content/` and renders each file as a dashboard

## Frontmatter fields

| Field   | Meaning                          |
| ------- | --------------------------------- |
| title   | Display name in the sidebar       |
| created | When the dashboard was first generated |
| updated | When it was last refreshed        |
| model   | Which model produced it           |
| path    | Free-form source/category tag     |

Head back to the [product overview](./overview.md) to see a real example, or check
the [Hebrew example](./hebrew-example.md) to see right-to-left (RTL) content rendered.
