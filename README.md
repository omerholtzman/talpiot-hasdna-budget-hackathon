# Budget Topic Pages (דפי נושא)

Generates a Hebrew markdown "topic page" for a subject — renewable energy, gifted youth, women's health — out of the Israeli state budget. One subject in, one `.md` file out, containing prose, tables, and `​```plotly` chart blocks, plus a directory of CSVs holding the data the page summarises.

Everything comes from the **BudgetKey MCP server** at `https://next.obudget.org/mcp` (see [.mcp.json](.mcp.json)), which exposes `DatasetInfo`, `DatasetFullTextSearch` and `DatasetDBQuery` over a read-only Postgres.

The markdown file is the deliverable. Nothing in this repo renders it — see [langgraph-module/PLOTLY_BLOCK_SPEC.md](langgraph-module/PLOTLY_BLOCK_SPEC.md) for the chart-block contract a viewer has to implement, and `langgraph-module/preview_plots.py` for a local sanity-check renderer.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate  elsewhere
pip install -r requirements.txt
```

Model access today is **Gemini on Vertex AI via local gcloud credentials** — no API key:

```bash
gcloud auth application-default login
```

Then set your own project, since `config.py` defaults to a hackathon lab project that will stop working:

```bash
export GCP_PROJECT=your-gcp-project-id      # or put it in langgraph-module/.env
export GCP_REGION=europe-southwest1
```

See [langgraph-module/.env.example](langgraph-module/.env.example) for every recognised variable.

## Running it

**Several subjects — the normal path.** Edit [orchestrator/orchestrator-config.json](orchestrator/orchestrator-config.json), which maps a category to its subjects and their output slugs:

```json
{ "health": [ {"subject": "בריאות", "slug": "health"} ] }
```

```bash
python orchestrator/orchestrator.py --dry-run                       # print the plan, run nothing
python orchestrator/orchestrator.py                                 # run everything in the config
python orchestrator/orchestrator.py --category health               # one category
python orchestrator/orchestrator.py --category health --subject gynecology
python orchestrator/orchestrator.py --model gemini-2.5-pro          # override the model for this batch
```

Each subject runs as its own subprocess with a 30-minute timeout (`--timeout`), so one hanging subject cannot stall the batch. Outcomes land in `orchestrator/orchestrator-state.json` and each run's console output in `reports/<slug>/run.log`. When the batch finishes, every report in a touched category gets a regenerated "דוחות קשורים" cross-link block.

**One subject.** Same pipeline, no state file, no cross-links, output straight to the console:

```bash
cd langgraph-module
python main.py "אנרגיה מתחדשת" --slug renewables
```

`--slug` is required and is not derived from the Hebrew subject: it names the output files and goes into the page's frontmatter `path`. Existing slugs are hand-picked English words.

## Output

A run writes into `langgraph-module/reports/`:

| Path | What it is |
|---|---|
| `<slug>.md` | the finished Hebrew page — **the deliverable** |
| `<slug>/selected_items.csv` | every level-4 budget line judged on-subject, with `counts_in_total` |
| `<slug>/item_budgets.csv` | one row per line per year: allocated / revised / used |
| `<slug>/hierarchy.csv` | levels 1–3 of the funding ministries, latest year |
| `<slug>/candidates.csv`, `programs.csv`, `domains.csv`, `excluded_items.csv` | the audit trail: everything considered, and why each verdict fell as it did |
| `<slug>/report.json` | computed `data_errors` and `possible_misses` |
| `<slug>/run_summary.json` | counts, verdict splits, SQL/LLM cost per step |

The CSVs are the data and the markdown is a view of them. When checking a page, start from `excluded_items.csv` and `report.json`'s `possible_misses` — those are what catch a subject that was under-reported rather than one that was reported wrongly.

## Switching the model provider

Every phase currently runs on Gemini. Moving to Claude is a small, deliberate change — `langchain-anthropic` is already in [requirements.txt](requirements.txt) and `ANTHROPIC_API_KEY` / `MODEL_NAME` are already read by `config.py`; nothing consumes them yet. Three places construct or name a model:

1. **[langgraph-module/agent_engineering/agents.py](langgraph-module/agent_engineering/agents.py)** — `_llm()` builds the `ChatGoogleGenerativeAI` used by phases 2, 3 and synthesis. Swap it for `ChatAnthropic(model=MODEL_NAME, api_key=ANTHROPIC_API_KEY)`. The Gemini schema-warning filter above it becomes dead and can go.
2. **[langgraph-module/agent_engineering/llm_json.py](langgraph-module/agent_engineering/llm_json.py)** — `JSONLLM` is the schema-constrained JSON call behind all of phase 1's classification. Its `generation_config={"response_mime_type", "response_schema"}` path is Gemini-specific; the Anthropic equivalent is a tool/`response_format` constraint, and if you don't implement one, the existing fallback (ask for JSON in the prompt, parse tolerantly via `parse_json_response`) already works. `sanitize_gemini_schema` becomes unnecessary.
3. **[langgraph-module/config.py](langgraph-module/config.py)** — make `MODEL_NAME` the value both call sites read, so the model recorded in each page's frontmatter is the model that actually ran. It is currently not (see "Known problems" in [CLAUDE.md](CLAUDE.md)).

Do 1 and 2 together. Splitting them leaves a run half on each provider, and `run_summary.json` records a single model name per run, so its cost figures stop meaning anything.

## Repo map

| Path | What it is |
|---|---|
| `langgraph-module/` | the generator: LangGraph pipeline, prompts, deterministic phase-1 SQL, chart rendering |
| `orchestrator/` | batch runner over a JSON config of subjects |
| `query_optimizer/` | standalone bash workflow for compiling NL questions into saved re-runnable SQL specs. Not wired into the generator — kept as future optimization infrastructure; see [query_optimizer/query_README.md](query_optimizer/query_README.md) |
| `docs/` | [BUDGETKEY_MCP_IMPROVEMENTS.md](docs/BUDGETKEY_MCP_IMPROVEMENTS.md) — bugs found in the MCP server itself, worth passing upstream |
| `CLAUDE.md` | architecture, the domain rules that break budget queries silently, known problems, and future work |

Read [CLAUDE.md](CLAUDE.md) before changing any budget SQL. The `code` column is a hierarchy where parents already contain their children, and the ways to get that wrong are all silent.
