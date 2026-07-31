# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A hackathon project that generates Hebrew "topic pages" (דפי נושא) about Israeli government spending. Everything is driven off the **BudgetKey MCP server** at `https://next.obudget.org/mcp` (declared in [.mcp.json](.mcp.json)), which exposes three tools over a read-only Postgres: `DatasetInfo`, `DatasetFullTextSearch`, `DatasetDBQuery`.

The end product is a markdown file with YAML frontmatter, rendered by a React viewer. `talpihackathon/main_agent/instructions/content-file-schema.md` is the contract between generator and viewer.

## Repository layout — three independent pieces

| Path | What it is | Language |
|---|---|---|
| `talpihackathon/main_agent/` | **The primary generator.** Custom MCP client + LLM abstraction + the deterministic phase-1 pipeline. | Python (stdlib + `requests`) |
| `langgraph-module/` | A LangGraph re-implementation of the same pipeline, as a `StateGraph` fan-out/fan-in. | Python (langgraph/langchain) |
| `talpihackathon/` (root) | The viewer: Vite + React 19 SPA with a small Express API that reads `content/*.md`. | TypeScript/JS |

`talpihackathon/query_optimizer/` is a separate bash workflow (`query_gen.sh` / `query_run.sh`) for compiling NL questions into saved, re-runnable SQL specs in `queries/*.json` — no LLM needed at run time.

**The two Python trees are deliberate duplicates.** `langgraph-module/pipeline.py`, `budget_api.py`, `budget_reference.py`, and `prompts/` are ports of the `main_agent/` originals; `skill_phase1_budget.md` exists in both. When you change logic or prompts in one, mirror it in the other or explicitly note the divergence — the READMEs claim they are kept in sync.

## Commands

### Viewer (from `talpihackathon/`)

```bash
npm install
npm run dev          # Vite on :5173 + Express API on :3001 (proxied at /api) via concurrently
npm run dev:client   # Vite only
npm run dev:server   # Express only
npm run typecheck    # tsc -b
npm run lint         # oxlint
npm run build        # generate:content -> tsc -b -> vite build -> copy-404
```

There are no tests. `npm run build` is the closest thing to a full check.

The API re-scans `content/` on **every request** — after writing a `.md` file, a browser refresh is enough, no restart. The production build is static (GitHub Pages): `scripts/generate-static-api.js` snapshots `content/` into `public/api/*.json` at build time, so a deployed site does **not** pick up new content without a rebuild. Base path is `/talpiot-hasdna-budget-hackathon/` in production ([vite.config.ts](talpihackathon/vite.config.ts)); CI is [.github/workflows/deploy.yml](.github/workflows/deploy.yml), which builds from `talpihackathon/` on push to `main`.

### main_agent (from `talpihackathon/main_agent/`)

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# ReAct agent, one subject end-to-end -> output_examples/<slug>-<timestamp>/
./venv/bin/python agent.py --subject "חינוך" --provider vertex
./venv/bin/python agent.py --prompt "מה התקציב של משרד החינוך ל-2025?" --provider gemini
./venv/bin/python agent.py --list-tools            # sanity-check the MCP connection
./venv/bin/python agent.py --subject health -t     # 1 turn only, quick smoke test

# Deterministic phase-1 pipeline -> pipeline_runs/<subject>-<timestamp>-<model>/
./venv/bin/python pipeline.py --subject "אנרגיה ירוקה" --provider vertex
./venv/bin/python pipeline.py --subject "..." --stop-after retrieve   # cheap partial run
./venv/bin/python pipeline.py --subject "..." --max-parallel 1        # sequential judging

# Score / diff runs
./venv/bin/python compare.py pipeline_runs/runA/ pipeline_runs/runB/
./venv/bin/python compare.py pipeline_runs/runA/ --truth eval/truth_energy.csv

# Batch over orchestrator-config.json -> ../structured_report/<category>/<subject>/
./venv/bin/python orchestrator.py --provider vertex
./venv/bin/python orchestrator.py --dry-run
./venv/bin/python orchestrator.py --category healthcare --subject center-healthcare
```

Providers: `vertex` (default for `pipeline.py`, uses local gcloud ADC, no key), `gemini` (`GEMINI_API_KEY`), `anthropic` (`ANTHROPIC_API_KEY`), `cli-claude` (shells out to a local `claude` executable).

`compare.py` is the evaluation harness: for every ground-truth code a run missed, it reports **where** it was lost (never retrieved / dropped at program triage / dropped at item judging). Use it before/after prompt changes — that's the only signal distinguishing "the pipeline broke" from "one prompt needs tuning".

### langgraph-module (from `langgraph-module/`)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # ANTHROPIC_API_KEY
python main.py "בריאות" --slug health    # -> reports/health.md + reports/health/*.csv
```

## Architecture: how a topic page gets made

Five phases, fanned out then synthesized (see [langgraph-module/README.md](langgraph-module/README.md) for the graph diagram):

- **Phase 1 — budget items.** *Not an agent.* `pipeline.py` runs `expand → triage domains → retrieve → triage programs → judge items → materialise → report`. Retrieval, materialisation and the data-quality report are plain SQL and never touch a model, so **no budget figure can be invented**; the LLM only ever classifies (which ministries, domains, programs, items) over bounded, chunked input with a JSON schema on the reply.
- **Phases 2/3 — contracts, government decisions.** ReAct agents (model + MCP tools), scoped by their prompts, each fed phase 1's digest so they can filter by the budget codes it found.
- **Phase 4 — hierarchy.** Pure rendering of the CSV phase 1 already wrote.
- **Final synthesis.** No tools at all — a pure writing/formatting pass over the four phases' output, producing the frontmatter + markdown the viewer consumes.

Prompts are files, not string literals: `main_agent/instructions/skill_*.md` (system prompts for the agent phases, plus `synthesis_template.md` and `subject_prompt.txt`) and `main_agent/prompts/*.md` (the pipeline's four classification prompts). Editing behaviour usually means editing these, not Python.

A pipeline run directory is the real deliverable — `selected_items.csv` + `item_budgets.csv` are the data, the markdown is a summary of them, and `excluded_items.csv` + `report.json`'s `possible_misses` are the audit trail a reviewer uses to catch false negatives. `run_summary.json` records verdict splits and per-step SQL/LLM cost.

## Domain rules that break things silently

From `main_agent/instructions/skill_phase1_budget.md` — read it before touching any budget SQL.

- `budget_items_data.code` is a dotted hierarchy: level 1 = ministry (`24`), 2 = domain (`24.16`), 3 = program (`24.16.03`), 4 = the atomic line item / תקנה (`24.16.03.62`). **Parent rows already contain their descendants — never sum across mixed levels.**
- **Never use `%` in a `code` filter.** `code LIKE '24%'` mixes levels and double-counts; the server attaches a warning and results carrying warnings must not be reported. Use `LEFT(code,2)='24'` plus an explicit `level = N`. `ILIKE '%…%'` on `title` is fine.
- Every policy area exists **twice**: an ordinary-budget office and a high-numbered development-budget office (בריאות = `24` *and* `67`/`92`/`93`/`94`). Missing the second half is the most common way to under-report a subject. `budget_reference.py` holds the checked-in office list and the ordinary↔development pairs.
- `(code, year)` is unique and codes get **recycled** — the same code can carry a different title in a different year. Always carry the year; prefer the latest year's title.
- `functional_class_*` and `economic_class_*` are populated for `level=4` rows only.
- Hard limits in `budget_api.py`: `PAGE_SIZE = 1000` (server-side cap, larger silently clamped) and `MAX_SQL_CHARS = 2800`. `verify_sql()` / `check_sql()` enforce the rules above before a query is sent.
- Tuning knobs at the top of `pipeline.py` (`CHUNK_ITEMS`, `MAX_PROGRAMS_PER_CALL`, `MAX_PARALLEL_CALLS`, `MAX_DOMAINS_INLINE`) carry measured comments. The failure mode to watch is a **truncated judging reply**, which costs recall silently — only the unjudged-count log reveals it.

`talpihackathon/BUDGETKEY_MCP_IMPROVEMENTS.md` documents known bugs in the MCP server itself (its `DatasetDBQuery` description omits two real datasets and its example query names columns that don't exist). Don't trust the tool descriptions over `DatasetInfo`.

## Conventions

- Content and prompts are **Hebrew**; code, comments and docs are English. Output is RTL markdown.
- Hebrew paths and console output on Windows: `compare.py` re-wraps stdout as UTF-8 because a cp1252 pipe aborts on the first Hebrew line. Do the same in any new CLI.
- Generated output is gitignored: `content/*` (except the three seed dashboards and `content/reports/`), `public/api/`, `main_agent/venv/`, `main_agent/orchestrator-state.json`, `.env`. `output_examples/` and `pipeline_runs/` are checked in as reference runs.
