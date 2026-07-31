# Government Data Wiki Harness

Generates a wiki-style Hebrew markdown "dashboard" for a subject (health,
education, ...) by combining three independent research phases with a
final synthesis phase. Built on **LangGraph**.

## Why LangGraph (and not, say, Prefect)

Both would work. LangGraph was chosen here because the pipeline is
fundamentally a **fan-out / fan-in**: three research phases that don't
depend on each other, feeding into one LLM that depends on all three.
That's exactly the shape LangGraph's `StateGraph` is built for, and its
prebuilt `create_react_agent` gives us the model → tool-call → model loop
for free where a phase still needs one.

Prefect (or Airflow, Dagster, etc.) would be a better fit if you later
need things LangGraph doesn't focus on: cron scheduling, a run-history
UI, retries/backoff policies configured per task, or running this across
many subjects as a batch job. Nothing here is LangGraph-specific enough
to make that swap painful later — `graph.py` is the only file that would
need to change shape; `agents.py`, `prompt_loader.py`, and `mcp_tools.py`
would carry over almost as-is as plain async functions.

## Architecture

```
                              ┌──────────────────┐
                         ┌───►│ phase2_contracts ├───┐
                         │    └──────────────────┘   │
  ┌───────────────┐      │    ┌──────────────────┐   ▼
  │ phase1_budget ├──────┼───►│ phase3_decisions ├──►│ final_synthesis ├─► END
  └───────────────┘      │    └──────────────────┘   ▲
                         │    ┌──────────────────┐   │
                         └───►│ phase4_hierarchy ├───┘
                              └──────────────────┘
```

**Phase 1 is not an agent.** Finding every budget item for a subject is a
recall problem, and a ReAct loop is a bad fit for it: the agent pays for
every row it has ever fetched on every subsequent turn, so it is pushed
toward sampling rather than enumerating, and any figure it retypes into
its answer can be wrong. `pipeline.py` replaces that loop with a
deterministic sequence:

```
expand → triage domains → retrieve → triage programs → judge items → materialise → report
```

Retrieval, materialisation and the data-quality report are plain SQL and
never touch a model, so **no budget figure can be invented**. The model
is used only to classify — which ministries, which of their domains,
which programs, which individual lines — over bounded, chunked input with
a JSON schema constraining the reply. Ported from
`talpihackathon/main_agent/pipeline.py`; keep the two in sync.

Phases 2 and 3 are still ReAct-style agents (the model + the MCP tools)
scoped by their own system prompts, and both receive Phase 1's digest so
they can filter their datasets by the budget codes it found. Phase 4 just
renders the hierarchy CSV the pipeline already wrote. Final synthesis has
no tools at all — per its skill file, it's a pure writing/formatting task
over what the four phases produced — and only runs once all three of its
predecessors finish.

Before a Phase 2/3 ReAct loop starts, the runner now checks for a matching
saved query spec in `../query_optimizer/queries`. A cache hit runs through
`agent_engineering.query_run` with no model. On a cache miss, the old ReAct
path runs once, then its successful `DatasetDBQuery` calls are saved as a
query spec for the next run.

Synthesis does not write the charts. Four blocks of the template — the
trend chart, the top-10 pie, the sources pie and the nested item list —
are fully determined by phase 1's CSVs, so `blocks.py` computes them and
substitutes them into the model's reply afterwards; the template carries
a `{{TOKEN}}` where each one goes. The model writes prose and the
phase 2/3 tables, which are the parts that actually need judgement.
Asking it for the charts too meant re-typed numbers at best and, in
`reports/GreenEnergy.md`, whole sections replaced by "לא נמצא מידע"
while the items sat in `selected_items.csv`. If the model drops a token
anyway, `apply_blocks` puts the block back under its heading and logs it.

## Output

`python main.py "אנרגיה ירוקה" --slug energy` writes:

| Path | What it is |
|---|---|
| `reports/energy.md` | the finished Hebrew dashboard |
| `reports/energy/selected_items.csv` | every level-4 item judged on-subject, with `counts_in_total` |
| `reports/energy/item_budgets.csv` | one row per item per year: allocated / revised / used |
| `reports/energy/hierarchy.csv` | levels 1–3 of the funding ministries, latest year |
| `reports/energy/candidates.csv`, `programs.csv`, `domains.csv`, `excluded_items.csv` | the audit trail: everything considered, and why each verdict fell as it did |
| `reports/energy/report.json` | computed `data_errors` and `possible_misses` |
| `reports/energy/run_summary.json` | counts, verdict splits, SQL/LLM cost per step |

The CSVs are the data; the markdown is a summary of them. `excluded_items.csv`
plus `possible_misses` are what a reviewer uses to catch false negatives.

## File map

| File | Responsibility |
|---|---|
| `config.py` | All environment-dependent settings (API key, model name, MCP URL, limits) |
| `state.py` | The `WikiState` TypedDict that flows through the graph |
| `prompt_loader.py` | Reads `prompts/*.md` and fills in `{TODAY}` / `{MODEL}` / the pipeline's fields |
| `mcp_tools.py` | Connects to the obudget MCP server; exposes its tools to LangChain, and to the pipeline's synchronous code via `SyncMCPBridge` |
| `budget_api.py` | Paging, warning-aware SQL layer over the MCP — the model-free half of phase 1 |
| `query_run.py` | Runs saved `query_optimizer` JSON query specs through MCP without a model |
| `research_query_cache.py` | Phase 2/3 saved-query cache: check spec, save ReAct SQL calls, run via `query_run.py` |
| `budget_reference.py` | Checked-in office list, functional classes, ordinary↔development pairs |
| `pipeline.py` | The deterministic phase-1 pipeline, plus the digest and hierarchy renderers |
| `llm_json.py` | Schema-constrained one-shot JSON calls, for the pipeline's classification steps |
| `blocks.py` | The template blocks phase 1's CSVs fully determine — Plotly fences and the nested item list — computed rather than written by the model |
| `agents.py` | The actual phase implementations (the "workers" behind each node) |
| `graph.py` | Wires the five phases into the LangGraph pipeline |
| `main.py` | CLI entry point: run one subject end-to-end, write the report |
| `prompts/` | Skill files (system prompts for phases 2/3/synthesis) and the pipeline's four classification prompts |

`prompts/skill_phase1_budget.md` and `skill_phase4_hierarchy.md` are no
longer wired into the graph — those phases stopped being agents. The
phase-1 file is kept because it is still the best description of the
dataset and its traps, and it stays in sync with
`talpihackathon/main_agent/instructions/skill_phase1_budget.md`.

## Setup

```bash
cd gov_wiki_harness
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in ANTHROPIC_API_KEY
```

## Running it

```bash
python main.py "בריאות" --slug health
```

This writes `reports/health.md` and the phase-1 data files under
`reports/health/` (see [Output](#output)).

## Things to double-check before your first real run

This was written and reviewed for correctness, but authored in a
sandbox with **no network access to the live MCP server**, so it hasn't
actually been executed end-to-end. Two spots most likely to need a
one-line fix, both called out in comments where they occur:

1. **MCP transport** (`mcp_tools.py`) — set to `"streamable_http"`,
   which is the modern MCP-over-HTTP transport. If
   `https://next.obudget.org/mcp` turns out to speak the older SSE
   transport instead, change that one string to `"sse"`.
2. **`create_react_agent` system-prompt argument** (`agents.py`) — the
   keyword for "here's the system prompt" has changed name across
   `langgraph` versions (`prompt=`, `state_modifier=`,
   `messages_modifier=`). If you hit a `TypeError` on that call, check
   your installed version's signature.
3. **Native JSON schemas** (`llm_json.py`) — the first classification
   call asks `ChatGoogleGenerativeAI` to constrain its reply with
   `generation_config={"response_mime_type", "response_schema"}`. Not
   every version of `langchain-google-genai` accepts that; if yours
   doesn't, the `TypeError` is caught and every later call falls back to
   asking for JSON in the prompt and parsing tolerantly. Worth confirming
   which path a real run took — the constrained one removes a whole class
   of truncated/fenced-reply failures.

## Extending

- **Add a phase**: write `prompts/skill_phaseN_thing.md`, add it to
  `PROMPT_FILES` in `constants.py`, add a `phaseN_thing_node` in
  `agents.py`, wire it into `graph.py`'s fan-out/fan-in. Remember: if two
  concurrent nodes might ever write to the same `WikiState` key, that key
  needs an `Annotated[..., operator.add]`-style reducer (see `errors` in
  `state.py` for why).
- **Batch over many subjects**: wrap `main.run(subject, slug)` in a loop
  (or a Prefect flow, if you migrate) — each call is fully self-contained.
- **Swap models per phase**: `agents.py`'s `_llm()` and `llm_json.py`'s
  `JSONLLM` both read `GEMINI_MODEL` from `config.py`, so every phase
  currently shares one model. Give the research phases and the synthesis
  phase their own instances if you want e.g. a cheaper model for
  data-fetching and a stronger one for the final write-up — but note that
  `run_summary.json` records one model name per run, so a mixed run's
  cost numbers stop being comparable.
- **Tune phase-1 recall vs. cost**: `CHUNK_ITEMS`, `MAX_PROGRAMS_PER_CALL`
  and `MAX_PARALLEL_CALLS` at the top of `pipeline.py`. The comments there
  record what was measured, including why a truncated judging reply is the
  failure mode to watch for.
