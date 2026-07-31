# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

For setup and how to run things, see [README.md](README.md). This file is the architecture, the domain traps, and the handover notes.

## What this is

A hackathon project, now handed over, that generates Hebrew "topic pages" (דפי נושא) about Israeli government spending. Everything is driven off the **BudgetKey MCP server** at `https://next.obudget.org/mcp` (declared in [.mcp.json](.mcp.json)), which exposes three tools over a read-only Postgres: `DatasetInfo`, `DatasetFullTextSearch`, `DatasetDBQuery`.

The deliverable is a markdown file with YAML frontmatter, containing prose, tables and `​```plotly` chart blocks. Nothing here renders it; [langgraph-module/PLOTLY_BLOCK_SPEC.md](langgraph-module/PLOTLY_BLOCK_SPEC.md) is the contract a viewer must implement.

## Repository layout

| Path | What it is |
|---|---|
| `langgraph-module/` | **The generator.** LangGraph `StateGraph`, the prompts, the deterministic phase-1 SQL pipeline, the chart/frontmatter rendering. |
| `orchestrator/` | Batch runner. One `main.py` subprocess per subject in `orchestrator-config.json`, with a state file and cross-report link sync. The normal entry point. |
| `query_optimizer/` | A standalone bash workflow (`query_gen.sh` / `query_run.sh`) that compiles NL questions into saved, re-runnable SQL specs in `queries/*.json` — no LLM at run time. **Not wired into the generator**; kept as future optimization infrastructure. |
| `docs/` | `BUDGETKEY_MCP_IMPROVEMENTS.md` — bugs in the MCP server itself, worth passing upstream. |

There is one Python tree. An earlier `talpihackathon/main_agent/` implementation and a Vite/React viewer both existed and were removed once `langgraph-module` superseded them; if you find a reference to either, it is stale and should be deleted.

## Architecture: how a topic page gets made

Five phases, fanned out then synthesized, wired in [langgraph-module/agent_engineering/graph.py](langgraph-module/agent_engineering/graph.py):

```
START → phase1_budget → ┬→ phase2_contracts ─┐
                        ├→ phase3_decisions ─┼→ final_phase_synthesis → END
                        └→ phase4_hierarchy ─┘
```

Phase 1 alone first (everything downstream needs its codes), then 2/3/4 concurrently, then synthesis once all three land. Phases 2/3/4 all write to `WikiState.errors`, which is why that key — and only that key — carries an `operator.add` reducer; see [state.py](langgraph-module/agent_engineering/state.py).

| Phase | LLM? | What it does |
|---|---|---|
| 1 · budget | classification only | Finds every level-4 budget line for the subject; writes the CSVs |
| 2 · contracts | ReAct agent | `contracts_data`: top contracts by volume + supplier totals |
| 3 · decisions | ReAct agent | Government decisions mentioning the subject |
| 4 · hierarchy | **none** | Formats `hierarchy.csv` as text |
| final · synthesis | one call, no tools | Writes the page; four blocks computed in Python |

**Phase 1 is not an agent.** Seven steps in [step1_pipeline.py](langgraph-module/agent_engineering/step1_pipeline.py), and the split is the point — *the LLM only ever classifies, never counts:*

| Step | How | (scale, from one real run) |
|---|---|---|
| 1 expand | LLM | subject → ministries, functional classes, keywords |
| 2 triage domains | LLM | 139 level-2 domains → 68 kept/ambiguous |
| 3 retrieve | **SQL** | 8,031 candidate lines under those domains |
| 4 triage programs | LLM | 380 level-3 programs → 123 |
| 5 judge items | LLM | 2,339 lines judged → 87 selected |
| 6 materialise | **SQL** | budgets for the 87 → `selected_items.csv`, `item_budgets.csv` |
| 7 report | **SQL** | computed `data_errors` + `possible_misses`, then `hierarchy.csv` |

Every LLM step is a schema-constrained one-shot JSON call at temperature 0 over bounded, chunked input — not an agent loop. Steps 3/6/7 never touch a model, so **no budget figure can be invented**; the model's influence is entirely in *which* rows were selected, and that is auditable in `excluded_items.csv`. Judging dominates cost (16 of 21 LLM calls in that run).

The pipeline is synchronous code run via `asyncio.to_thread`; its SQL reaches the event loop through `SyncMCPBridge` ([mcp_tools.py](langgraph-module/agent_engineering/mcp_tools.py)) and its model calls through `JSONLLM` ([llm_json.py](langgraph-module/agent_engineering/llm_json.py)).

Phases 2/3 are the only real ReAct loops (model → MCP tool → model, step-capped); phase 2 is fed `build_scope()`'s three-tier SQL filter ladder so it joins contracts on the exact budget codes phase 1 found rather than widening to a whole ministry, and a failure is caught per-phase so one dead agent doesn't kill the run.

Phase 4 is pure formatting. It was once an agent that queried the tree itself with `code LIKE '24%'` — the wildcard that double-counts a parent with its children. Phase 1 already writes those rows correctly, so it now only renders them.

Final synthesis is split down the middle. The model writes the prose, the contracts/suppliers/decisions tables, the suppliers pie and all inline linking. **[blocks.py](langgraph-module/agent_engineering/blocks.py) writes the four blocks phase 1's CSVs fully determine** — trend chart, top-10 pie, sources pie, appendix item table. The template carries a `{{TOKEN}}` where each goes and `apply_blocks()` substitutes after the call, so the model never transcribes a number or a Plotly fence. It used to, and it silently dropped whole charts (see `langgraph-module/reports/GreenEnergy.md`). If a token goes missing, the block is repaired back under its heading and logged.

**The frontmatter is computed too** — `blocks.apply_frontmatter()` strips whatever the model emitted (including a code fence wrapping the whole document) and prepends the real block; the skill file tells the model to start at the `# {SUBJECT_HEBREW}` heading instead. It has to be at offset 0 or a gray-matter-style parser ignores it, and the model was inventing `model:` values.

Prompts are files, not string literals, in `langgraph-module/prompts/`, keyed by `PROMPT_FILES` in [config.py](langgraph-module/config.py). Editing behaviour usually means editing these, not Python.

A run directory is the real deliverable alongside the page — `selected_items.csv` + `item_budgets.csv` are the data, the markdown is a summary of them, and `excluded_items.csv` + `report.json`'s `possible_misses` are the audit trail a reviewer uses to catch false negatives. `run_summary.json` records verdict splits and per-step SQL/LLM cost.

## Domain rules that break things silently

From `langgraph-module/prompts/skill_phase1a_main.md` — read it before touching any budget SQL. (That file is no longer loaded by any phase, but it remains the best description of the dataset and its traps.)

- `budget_items_data.code` is a dotted hierarchy: level 1 = ministry (`24`), 2 = domain (`24.16`), 3 = program (`24.16.03`), 4 = the atomic line item / תקנה (`24.16.03.62`). **Parent rows already contain their descendants — never sum across mixed levels.**
- **Never use `%` in a `code` filter.** `code LIKE '24%'` mixes levels and double-counts; the server attaches a warning and results carrying warnings must not be reported. Use `LEFT(code,2)='24'` plus an explicit `level = N`. `ILIKE '%…%'` on `title` is fine.
- Every policy area exists **twice**: an ordinary-budget office and a high-numbered development-budget office (בריאות = `24` *and* `67`/`92`/`93`/`94`). Missing the second half is the most common way to under-report a subject. [budget_reference.py](langgraph-module/helpers/prompts/budget_reference.py) holds the checked-in office list and the ordinary↔development pairs.
- `(code, year)` is unique and codes get **recycled** — the same code can carry a different title in a different year. Always carry the year; prefer the latest year's title.
- `functional_class_*` and `economic_class_*` are populated for `level=4` rows only.
- Hard limits in [budget_api.py](langgraph-module/helpers/prompts/budget_api.py): `PAGE_SIZE = 1000` (server-side cap, larger silently clamped) and `MAX_SQL_CHARS = 2800` (past ~2990 chars the server returns **zero rows, no error** — the most dangerous limit here, because it looks exactly like "no such data"). `verify_sql()` / `check_sql()` enforce the rules above before a query is sent.
- Tuning knobs at the top of `step1_pipeline.py` (`CHUNK_ITEMS`, `MAX_PROGRAMS_PER_CALL`, `MAX_PARALLEL_CALLS`, `MAX_DOMAINS_INLINE`) carry measured comments. The failure mode to watch is a **truncated judging reply**, which costs recall silently — only the unjudged-count log reveals it.

`docs/BUDGETKEY_MCP_IMPROVEMENTS.md` documents known bugs in the MCP server itself (its `DatasetDBQuery` description omits two real datasets and its example query names columns that don't exist). Don't trust the tool descriptions over `DatasetInfo`.

## Known problems

Live issues in the code as handed over. None of these break a run; all of them will mislead someone.

- **The `model:` field in every generated page is wrong.** [config.py](langgraph-module/config.py) sets `MODEL_NAME = "claude-sonnet-5"`, `main.py` puts it in the state, and `blocks.frontmatter()` writes it verbatim — but every actual call goes through `ChatGoogleGenerativeAI` with `GEMINI_MODEL`. `reports/renewables.md` claims `model: claude-sonnet-5`; it was generated by `gemini-2.5-flash`. Fixing it means pointing both call sites at one config value — see "Switching the model provider" in the README. `ANTHROPIC_API_KEY` is likewise read and never used, and `_llm()`'s comments still describe Claude-on-Vertex model IDs.
- **`GOOGLE_PROJECT` defaults to a hardcoded qwiklabs lab project.** That project will expire. Set `GCP_PROJECT`.
- **Two prompts are wired but never loaded.** `PROMPT_FILES` maps `PHASE1 → skill_phase1a_main.md` and `PHASE4 → skill_phase4_hierarchy.md`; nothing calls `load_prompt` for either, because those phases stopped being agents. Keep `skill_phase1a_main.md` (it is the dataset documentation). `skill_phase4_hierarchy.md` is actively harmful to copy from — it is the file that teaches `code LIKE '24%'`.
- **Debug prints left in [agents.py](langgraph-module/agent_engineering/agents.py)** around the phase-1 node: `print("type(summary)", ...)`.
- **`budget_reference.py` says to regenerate itself with `tools/refresh_reference.py`.** No such script exists. The office list, functional classes and development pairs were generated on 2026-07-31 and are checked in by hand; they need updating when a new budget year lands (`LATEST_YEAR` is `2026`).
- **`AGENT_MAX_STEPS` is passed as LangGraph's `recursion_limit`**, which counts graph supersteps, not agent turns — a ReAct turn is roughly two. `12` is about 6 tool calls, not 12.

## Future work

- **Phase 3 is unscoped.** Phase 2 gets `build_scope()`'s tiered budget-code filters and is explicitly forbidden from filtering by ministry. Phase 3 gets only the digest, and its prompt tells it to match a Hebrew keyword against `title`/`content`. That is the weakest link in the page: decisions are selected by substring, not by any connection to the budget lines the rest of the page is about. Whether decisions *can* be linked to budget codes at all is an open question — investigate before designing the fix.
- **`hierarchy.csv` is wider than the subject.** `build_hierarchy()` queries every office in `plan["offices"]` — every ministry the expansion step named — at full budget, not just the parts funding selected items. `blocks.py` deliberately refuses to build the sources pie from it for exactly that reason, and uses it only to name and link the level 1–3 ancestors of selected items. Either narrow the query to the ancestors actually needed, or drop the file and read the ancestors directly.
- **`query_optimizer/` is unintegrated.** Its premise — pay an agent once to compile SQL, then re-run the saved spec for free and deterministically — applies directly to this pipeline's fixed queries. Nothing connects the two today.
- **No viewer.** The pages are markdown with `​```plotly` fences and the render contract is written down, but nothing in this repo or downstream implements it yet.
- **No tests, and no evaluation harness.** An earlier implementation had a `compare.py` that scored a run against a ground-truth CSV and reported *where* a missed code was lost (never retrieved / dropped at program triage / dropped at item judging). It was removed with that tree. Prompt changes are currently unmeasurable — this is the single most valuable thing to rebuild, since every phase-1 prompt is a recall/precision tradeoff with no signal on it.

## Conventions

- Content and prompts are **Hebrew**; code, comments and docs are English. Output is RTL markdown.
- Hebrew paths and console output on Windows: CLIs re-wrap stdout as UTF-8 because a cp1252 pipe aborts on the first Hebrew line. Do the same in any new CLI.
- Generated output is gitignored (`__pycache__`, `orchestrator-state.json`, `.env`, `*.preview.html`). `langgraph-module/reports/` is checked in as reference runs — some of them (`gynecology.md`, `tipathalav.md`, `Magendavidadom.md`) predate the current pipeline and have no run directory.
