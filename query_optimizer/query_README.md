# Query Compiler

This directory contains a small workflow for turning natural-language questions
about BudgetKey / OpenBudget data into saved, reproducible query specs.

## Why
1. Pay the agent cost (tokens) once, save the SQL it produced, and later run the saved query quickly without asking an agent again.
1. Reproducible results. Once SQL query was generated, LLM is not needed and 2 runs yield exactly identical results
1. Reduce load on the backed. Less MCP calls. No need for LLM to learn from scratch the schema of database and research of tables and apis
1. Schema safety: When schema of DB changes SQL queries may break. Query runner detects such problems. You can use LLMs to fix them

## Files
- `query_gen.sh`: asks an MCP-capable agent to generate a saved query spec.
- `query_run.sh`: runs a saved query spec against `https://next.obudget.org/mcp`.
- `queries/*.json`: saved query specs.

## Integration with the app
- Introduced cache layer for faster rerun, saving tokens and reliably reproducing results (during debugging, testing rerunning).
- Proof of concept in this directory (query_optimizer). See this readme file.
- App Phase 1 (research) - Still no cache for this layer. Second run on the same input runs the full Phase 1. But for testing purposes can skip it (input .csv directly for later phases). Todo: use the same cache ideas as exist in phases 2,3.
- Phase 2,3
  - Active cash, saves SQL queries.
  - 2 implementations: bash query_gen.sh for terminal/manual testing and python code as part of the app.
  - Runs them via MCP without agent. 2 implementations: bash query_run.sh for terminal/manual testing and python as part of the app.

### Python re-implementation
Both are equivalent:
```bash
./query_optimizer/query_run.sh query_optimizer/queries/top_city_budgets.json
cd langgraph-module
python3 -m agent_engineering.query_run ../query_optimizer/queries/top_city_budgets.json
```

## Generate A Query

Example:

```bash
./query_gen.sh \
  --force \
  --agent-cmd "$HOME/.vscode-server/extensions/openai.chatgpt-26.721.41059-linux-x64/bin/linux-x86_64/codex exec -C \"$PWD\" --dangerously-bypass-approvals-and-sandbox --ephemeral -" \
  --out queries/taxes.json \
  "how much taxes the government gathered from businesses in year 2024"
```

What this does:

1. Builds a strict prompt for Codex.
2. Codex uses the BudgetKey MCP server to inspect schemas and validate SQL.
3. Codex emits a query spec JSON.
4. `query_gen.sh` extracts the JSON.
5. `query_gen.sh` validates it with `query_run.sh --check`.
6. If validation passes, it saves the spec to `queries/taxes.json`.

Use `--force` when you want to overwrite an existing query file.

### Run A Saved Query

```bash
./query_run.sh queries/taxes.json
./query_run.sh --out /tmp/taxes.json --format json queries/taxes.json
```

### Validate A Query

Run:

```bash
./query_run.sh --check queries/taxes.json
./query_run.sh --check --format json queries/taxes.json
```

Validation catches common drift problems:
- MCP transport failure
- SQL/server error
- MCP warnings
- missing expected columns
- too few rows
See Exit codes

The deliberately broken test spec should fail with exit code `14`:
```bash
./query_run.sh --check queries/broken_missing_expected_column.json
```

## Query Spec Format

Single-table query:

```json
{
  "title": "Business income tax revenue in 2024",
  "human_request": "how much taxes the government gathered from businesses in year 2024",
  "notes": "Important caveats and time period.",
  "dataset": "income_items_data",
  "page_size": 1,
  "min_rows": 1,
  "columns": [
    "revenue_year",
    "source_budget_year",
    "code",
    "title",
    "taxes_gathered_nis",
    "item_url"
  ],
  "query": "SELECT ..."
}
```

Multi-table dashboard query:

```json
{
  "title": "Health Ministry contracts dashboard",
  "human_request": "Create a multi-table dataset for plotting Health Ministry contract activity.",
  "notes": "Use --format json for graph formatters.",
  "queries": [
    {
      "name": "active_health_contracts_trend_2021_2025",
      "title": "Health Ministry active contracts trend, 2021-2025",
      "dataset": "contracts_data",
      "page_size": 5,
      "min_rows": 5,
      "columns": ["year", "active_contracts", "total_volume", "total_executed"],
      "query": "WITH years(year) AS (...) SELECT ..."
    }
  ]
}
```

`columns` controls output order and also acts as the expected-column contract.
If validation should check different columns than the display output, add
`expected_columns`.

## Agent Command Notes

`query_gen.sh` needs an agent command that:

- reads the generated prompt from stdin
- can use the BudgetKey MCP server
- prints the final response to stdout

The Codex CLI non-interactive form is:

```bash
codex exec -C "$PWD" --ephemeral -
```

In the VS Code extension install used here, the full path is:

```bash
$HOME/.vscode-server/extensions/openai.chatgpt-26.721.41059-linux-x64/bin/linux-x86_64/codex
```

If enterprise policy blocks dangerous bypass, use the policy-compatible form:

```bash
./query_gen.sh \
  --agent-cmd "$HOME/.vscode-server/extensions/openai.chatgpt-26.721.41059-linux-x64/bin/linux-x86_64/codex exec -C \"$PWD\" -s workspace-write --ephemeral -" \
  --out queries/taxes.json \
  "how much taxes the government gathered from businesses in year 2024"
```

If the bypass form is allowed in your controlled environment:

```bash
./query_gen.sh \
  --force \
  --agent-cmd "$HOME/.vscode-server/extensions/openai.chatgpt-26.721.41059-linux-x64/bin/linux-x86_64/codex exec -C \"$PWD\" --dangerously-bypass-approvals-and-sandbox --ephemeral -" \
  --out queries/taxes.json \
  "how much taxes the government gathered from businesses in year 2024"
```

## Manual Fallback

If automated agent execution is blocked, print the prompt:

```bash
./query_gen.sh --print-prompt \
  "how much taxes the government gathered from businesses in year 2024"
```

Paste that prompt into an MCP-capable agent, save the response, then import it:

```bash
./query_gen.sh \
  --agent-output /tmp/agent_response.txt \
  --out queries/taxes.json \
  "how much taxes the government gathered from businesses in year 2024"
```

## Example Timing

Generation is slower because it runs an agent ~180K[tokens], 5[min]:

```bash
time ./query_gen.sh --force --agent-cmd "$AGENT_CMD" --out queries/taxes.json \
  "how much taxes the government gathered from businesses in year 2024"
The local generator prompt is the same contract you gave, and the runner validates only `DatasetDBQuery`; it does not call `DatasetInfo`. I’ll use the saved examples plus direct MCP protocol knowledge unless I can get a successful server call.
web search: site:next.obudget.org income_items_data BudgetKey DatasetInfo amount_used code 01.03
web search: https://www.juheapi.com/mcp-servers/OpenBudget/budgetkey-mcp
web search: https://www.juheapi.com/mcp-servers/OpenBudget/budgetkey-mcp
web search: BudgetKey API income_items_data /api/query
web search: https://raw.githubusercontent.com/OpenBudget/budgetkey-mcp/main/server.py
web search: https://next.obudget.org/api/tables/income_items_data/info
web search: https://next.obudget.org/api/tables/income_items_data/info
web search: https://next.obudget.org/datapackages/
web search: https://next.obudget.org/datapackages/
web search: https://next.obudget.org/datapackages/budget/
web search: https://next.obudget.org/datapackages/budget/national/
web search: site:next.obudget.org/datapackages/ income_items_data datapackage
web search: https://next.obudget.org/datapackages/
web search: https://next.obudget.org/datapackages/budgetkey/
web search: https://next.obudget.org/datapackages/budgetkey/budget/
web search: site:next.obudget.org/datapackages/budgetkey "income_items_data"
web search: https://next.obudget.org/mcp
web search: site:next.obudget.org/i/budget/000103 "מס הכנסה סקטור עסקי"
web search: https://next.obudget.org/i/budget/00000103/2024
web search: https://next.obudget.org/i/budget/00000103/2025
web search: https://next.obudget.org/api/tables/income_items_data/info
codex
The public BudgetKey item page confirms the “actual 2024” value is exposed on the 2025 income item page for code `00.01.03`, matching the candidate query’s note that `amount_used` is the previous year’s actual revenue.
codex
I’m making one last validation attempt through the provided local runner, which is the same path downstream users will run.
...

END_QUERY_SPEC_JSON
tokens used
178,199
OK: /tmp/tmp.9pws0QmQJo
OK: result rows=1 min_rows=1 columns=6 expected_columns=6
Saved query spec: queries/taxes.json
real    4m47.174s
```

Running a saved query is much faster: 0[tokens], 1.5[sec]:

```bash
time ./query_run.sh queries/taxes.json
revenue_year    source_budget_year      code    title   taxes_gathered_nis      item_url
2024    2025    01.03   מס הכנסה סקטור עסקי     121587471414.0  https://next.obudget.org/i/6b2cd656b888
real    0m1.132s
```
