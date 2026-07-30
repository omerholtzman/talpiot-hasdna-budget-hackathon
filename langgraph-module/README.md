# Government Data Wiki Harness

Generates a wiki-style Hebrew markdown "dashboard" for a subject (health,
education, ...) by combining three independent research phases with a
final synthesis phase. Built on **LangGraph**.

## Why LangGraph (and not, say, Prefect)

Both would work. LangGraph was chosen here because the pipeline is
fundamentally an **agentic fan-out / fan-in**: three tool-calling LLM
agents that don't depend on each other, feeding into one LLM that
depends on all three. That's exactly the shape LangGraph's `StateGraph`
is built for, and its prebuilt `create_react_agent` gives us the
model → tool-call → model loop for free.

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
        ┌────►│ phase1_budget    ├────┐
        │     └──────────────────┘    │
  START ├────►┌──────────────────┐    ▼
        │     │ phase2_contracts ├──► phase4_synthesis ──► END
        │     └──────────────────┘    ▲
        └────►┌──────────────────┐    │
              │ phase3_decisions ├────┘
              └──────────────────┘
```

Phases 1–3 each run a ReAct-style agent (Claude + the three MCP tools)
scoped by its own system prompt (`prompts/skill_phase*.md`, copied
verbatim from the original skill files you shared). They have no
dependency on each other, so LangGraph runs them concurrently. Phase 4
has no tools at all — per its skill file, it's a pure writing/formatting
task over the text the first three phases produced — and only runs once
all three finish.

## File map

| File | Responsibility |
|---|---|
| `config.py` | All environment-dependent settings (API key, model name, MCP URL, limits) |
| `state.py` | The `WikiState` TypedDict that flows through the graph |
| `prompt_loader.py` | Reads `prompts/*.md` and fills in `{TODAY}` / `{MODEL}` |
| `mcp_tools.py` | Connects to the obudget MCP server, exposes its tools to LangChain |
| `agents.py` | The actual phase implementations (the "workers" behind each node) |
| `graph.py` | Wires the four phases into the LangGraph pipeline |
| `main.py` | CLI entry point: run one subject end-to-end, write the report |
| `prompts/` | The four skill files, used as system prompts (edit these to change phase behavior) |

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

This writes `reports/health.md`.

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

## Extending

- **Add a phase**: write `prompts/skill_phaseN_thing.md`, add it to
  `_PROMPT_FILES` in `prompt_loader.py`, add a `phaseN_thing_node` in
  `agents.py`, wire it into `graph.py`'s fan-out/fan-in. Remember: if two
  concurrent nodes might ever write to the same `WikiState` key, that key
  needs an `Annotated[..., operator.add]`-style reducer (see `errors` in
  `state.py` for why).
- **Batch over many subjects**: wrap `main.run(subject, slug)` in a loop
  (or a Prefect flow, if you migrate) — each call is fully self-contained.
- **Swap models per phase**: `agents.py`'s `_llm()` currently uses one
  model for everything; give the research phases and the synthesis phase
  their own `ChatAnthropic(...)` instances if you want e.g. a cheaper
  model for data-fetching and a stronger one for the final write-up.
