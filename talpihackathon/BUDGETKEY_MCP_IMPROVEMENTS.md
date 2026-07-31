# BudgetKey MCP — Suggested Improvements

Findings from connecting directly to the BudgetKey MCP server (`https://next.obudget.org/mcp`)
and inspecting its tool schemas, descriptions, and live behavior (`DatasetInfo`,
`DatasetFullTextSearch`, `DatasetDBQuery`). Scope is the MCP server/tools themselves — not any
particular client's implementation — since every agent in the hackathon pipeline will be talking
to the same server surface.

---

## Critical

1. **`DatasetDBQuery`'s dataset list is missing 2 of the 10 real datasets.**
   `government_decisions_data` and `social_services_data` are listed in `DatasetInfo` and
   `DatasetFullTextSearch`, but absent from `DatasetDBQuery`'s own description/enum. This isn't
   just a cosmetic gap — I verified live that `government_decisions_data` is in fact fully
   SQL-queryable (`SELECT * FROM government_decisions_data LIMIT 1` returned real rows), so the
   tool description actively under-represents what's usable. Any agent that trusts the tool
   description at face value will conclude those two datasets can't be queried via SQL and either
   give up on them or hallucinate a workaround.

2. **`DatasetDBQuery`'s built-in example query references fields that don't exist.**
   The tool description's example is:
   `SELECT year, code, title, net_allocated, net_executed, item_url FROM budget_items_data ...`
   but the real schema (per `DatasetInfo`) has `amount_allocated`, `amount_revised`,
   `amount_used` — there is no `net_allocated`/`net_executed`. A model that pattern-matches the
   example instead of reading the schema first will generate a query that errors or silently
   returns nulls for those columns.

---

## High

3. **`DatasetFullTextSearch` hard-caps at 20 results with no pagination parameter.**
   A test search (`"משרד החינוך"` against `budget_items_data`) returned `num_results: 20` while
   `total_results: 31400`. There's no offset/page parameter in the tool's input schema, so once
   the entity you need isn't in the top 20 text-relevance matches, there is no way to reach it
   through this tool at all — the caller's only recourse is guessing a narrower query string.

4. **The "avoid >4 parallel tool calls" concurrency limit is prose-only, not enforced.**
   `DatasetFullTextSearch`'s description warns callers to "avoid calling more than 4 tools in
   parallel to prevent memory overflow," but this is guidance embedded in tool documentation, not
   a server-side guardrail (no visible rate-limiting, 429 responses, or queuing behavior). Any
   agent — or any of several concurrent pipeline agents that haven't all read and internalized
   that specific sentence — has nothing on the server side catching it if it's ignored.

5. **No documented relationships between datasets.** `DatasetInfo` returns column schema for one
   dataset at a time, and `DatasetDBQuery`'s guidance says to "use JOINs when querying related
   datasets," but no tool documents which columns actually join across datasets (e.g. how
   `entities_data` keys relate to `contracts_data`, `support_programs_data`, or
   `supports_transactions_data`). Callers have to discover join keys by trial and error rather
   than being told them up front.

---

## Medium

6. **Session handshake returns HTTP 406 on the initial GET, yet still carries the required
   session header.** The first handshake request (before the client sends an SSE-accepting
   `Accept` header) gets a 406 Not Acceptable — but the `Mcp-Session-Id` header is present in that
   406 response and is required to proceed. This deviates from what a generic/strict MCP client
   SDK would expect (treating non-200 as a hard failure), so any pipeline agent using a stock MCP
   client library rather than a hand-rolled one is at risk of breaking on this server specifically.

7. **`warnings` field in `DatasetDBQuery` responses isn't documented.** The response schema
   includes a `warnings` value (seen as `null` in normal responses) and the tool description says
   "check for warnings — if present, fix the query and re-run," but no enum or example of actual
   warning strings/formats is given anywhere, making it hard to build reliable programmatic
   handling instead of just re-running blindly on any non-null value.

8. **No documented limits on query result size, complexity, or timeout behavior for
   `DatasetDBQuery`.** It's unclear from the tool description how large a result set or how
   expensive a query (e.g. multi-table joins, aggregations across full history back to 1997) the
   server tolerates before truncating, erroring, or timing out.

---

## Low

9. **Dataset usage notes are Hebrew-only** (e.g. `budget_items_data`'s guidance about not summing
   `WHERE code LIKE 'XX%'` due to hierarchical rollups). Reasonable for a Hebrew-first product,
   but worth flagging for any pipeline component that isn't Hebrew-tuned and might miss
   dataset-specific gotchas embedded only in Hebrew prose.

10. **No version/last-updated marker per dataset.** If the underlying schema or data changes over
    time, there's no field to detect that a previously cached `DatasetInfo` result is stale.
