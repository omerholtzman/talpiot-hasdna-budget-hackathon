# BudgetKey MCP Autonomous Agent (Python)

An autonomous CLI agent that connects to the **BudgetKey MCP Server**
(`https://next.obudget.org/mcp`) and orchestrates multi-step tool execution
loops utilizing Large Language Models (Gemini, Claude, or Vertex AI).

The agent acts as a smart budget research assistant. Given a high-level question
in Hebrew or English, it automatically inspects the database schemas, searches
for relevant entity IDs/budget codes, and compiles and runs PostgreSQL queries
to yield accurate data.

---

## Architecture Overview

```
                      +-----------------------------+
                      |          agent.py           |
                      |  (CLI Loop & Orchestrator)  |
                      +--------------+--------------+
                                     |
             +-----------------------+-----------------------+
             |                                               |
             v                                               v
   +-------------------+                           +-------------------+
   |   mcp_client.py   |                           |  llm_providers.py |
   |  (MCP Connection) |                           |   (LLM Wrapper)   |
   +---------+---------+                           +---------+---------+
             |                                               |
             v (SSE / POST)                                  v
   +-------------------+                           +-------------------+
   | BudgetKey MCP Svr |                           | Gemini / Claude   |
   | (Dataset DB/SQL)  |                           | (Vertex or Studio)|
   +-------------------+                           +-------------------+
```

- **`mcp_client.py`:** Handles the initial session handshake, manages a
  background thread to consume the SSE GET response stream, and sends JSON-RPC
  POST requests to execute tools.
- **`llm_providers.py`:** Abstracts different model APIs (AI Studio, Anthropic,
  Vertex AI) into a unified messaging and function-calling interface.
- **`agent.py`:** The main autonomous coordinator loop. It appends system
  instructions, manages conversation history, parses tool call requests from the
  model, executes them via the MCP client, and feeds the results back to the
  model.

---

## File Structure

```
talpihackathon/main_agent/
├── agent.py            # CLI entrypoint and main agent loop
├── mcp_client.py       # Lightweight MCP client
├── llm_providers.py    # Interface and client adapters for Gemini & Claude
├── requirements.txt    # Python package dependencies
└── README.md           # This documentation
```

---

## Setup Instructions

1. **Navigate to the agent directory:**

   ```bash
   cd talpihackathon/main_agent
   ```

2. **Create a Python Virtual Environment (`venv`):**

   ```bash
   python3 -m venv venv
   ```

3. **Install Dependencies:** Install required packages via the public PyPI
   index:
   ```bash
   ./venv/bin/pip install --index-url https://pypi.org/simple -r requirements.txt
   ```

---

## Usage Guide

The agent supports two query modes (`--prompt` and `--subject`) and four hosting
environments (`--provider`).

### Query Modes

You must specify **either** `--prompt` or `--subject` (they are mutually
exclusive):

- **Custom Prompt Mode (`--prompt`):** Run a custom question/instruction
  directly.
  ```bash
  ./venv/bin/python agent.py --prompt "מה התקציב של משרד החינוך לשנת 2025?" --provider=vertex
  ```
- **Subject Mode (`--subject`):** Run a query about a general subject using the
  pre-compiled template loaded from `instructions/subject_prompt.txt`.
  ```bash
  ./venv/bin/python agent.py --subject "חינוך" --provider=vertex
  ```

---

### Hosting Providers

Select which LLM provider to use via the `--provider` flag:

#### 1. Google Cloud Vertex AI (Default / Zero Config)

If your machine is already authenticated with Google Cloud, you don't need any
API keys. The agent automatically generates temporary OAuth2 tokens using your
local GCP credentials.

```bash
./venv/bin/python agent.py --prompt "מה התקציב של משרד החינוך לשנת 2025?" --provider=vertex
```

_Note: By default, this uses the `gemini-2.5-flash` model on Vertex AI._

#### 2. Gemini AI Studio (API Key)

Generate a key at [Google AI Studio](https://aistudio.google.com/), export it,
and run:

```bash
export GEMINI_API_KEY="your-gemini-api-key"
./venv/bin/python agent.py --prompt "מה התקציב של משרד החינוך לשנת 2025?" --provider=gemini
```

#### 3. Anthropic Claude (API Key)

Generate a key from Anthropic Console, export it, and run:

```bash
export ANTHROPIC_API_KEY="your-anthropic-api-key"
./venv/bin/python agent.py --prompt "מה התקציב של משרד החינוך לשנת 2025?" --provider=anthropic
```

_Note: By default, this uses `claude-3-5-sonnet-latest`._

#### 4. Local Claude CLI Wrapper (`cli-claude`)

If you have an installed CLI command `claude` that accepts a text prompt,
supports tool calling via `--allowedTools`, and returns JSON responses, you can
use the `cli-claude` provider:

```bash
./venv/bin/python agent.py --prompt "מה התקציב של משרד החינוך לשנת 2025?" --provider=cli-claude
```

By default, this invokes the `claude` executable. You can customize the name of
the command by specifying the executable using the `--model` flag:

```bash
./venv/bin/python agent.py --prompt "מה התקציב של משרד החינוך לשנת 2025?" --provider=cli-claude --model=my-claude-cli
```

---

---

### Overriding Parameters

* **Model / Command name Override (`--model`):**
  You can specify a different model name or executable override using the `--model` flag:
  ```bash
  ./venv/bin/python agent.py --prompt "..." --provider=vertex --model=gemini-3.5-pro
  ```
* **Output File Override (`--output` / `-o`):**
  By default, the final response is saved to a Markdown file under `/tmp/<subject_or_prompt_slug>-<timestamp>.md`. You can customize this file destination:
  ```bash
  ./venv/bin/python agent.py --subject "health" --provider=vertex -o ./reports/my_health_report.md
  ```

---

## Detailed Orchestration Loop (ReAct Pattern)

`agent.py` executes a deterministic Reasoning-Action (ReAct) loop that
coordinates the remote LLM and the local tool execution:

1. **CLI Parsing:** Reads user query, selected LLM provider, custom model
   override, and the MCP server URL.
2. **MCP Handshake:** Connects to the BudgetKey MCP server, handles session ID
   extraction from headers, starts a background worker thread to drain the SSE
   GET response stream, and calls `tools/list` to fetch descriptions of all
   tools (`DatasetInfo`, `DatasetFullTextSearch`, `DatasetDBQuery`).
3. **Provider setup:** Instantiates the LLM client (Gemini Studio, Anthropic, or
   GCP Vertex AI) using environment variables or application default
   credentials.
4. **Chat History Initialization:** Prepares a chat history log pre-loaded with:
   - Robust system instructions (directing the LLM to write PostgreSQL queries,
     start with `DatasetInfo`, limit parallel calls, and respond in Hebrew).
   - The user query.
5. **Execution Loop (Up to 10 Turns):**
   - **Reasoning phase:** Sends the entire chat history (including preceding
     tool execution results) and the tool definitions to the LLM.
   - **Action phase:** Receives the LLM's response.
     - **If the model requests tool execution:** `agent.py` loops through each
       request, calls the corresponding tool on the MCP server, prints the
       output preview, and appends a `Message(role="tool")` containing the raw
       output to the history. It then loops back to the reasoning phase.
     - **If the model returns a final text response:** It prints the final
       answer to the user and breaks out of the loop.
6. **Connection teardown:** Cleanly closes background SSE stream threads and
   terminates.

---

## Example Run Walkthrough

When you submit a query such as:

> _"מה התקציב של משרד החינוך לשנת 2025?"_

The agent loops through the following sequence:

1. **Turn 1 (Schema Check):** The model requests a tool call to `DatasetInfo` to
   understand what tables (like `budget_items_data`) and columns are available.
2. **Turn 2 (Entity Lookup / Search):** The model executes
   `DatasetFullTextSearch` (or goes straight to DB query if it knows the code)
   to find the ID code of "משרד החינוך" (Code `20`).
3. **Turn 3 (SQL Execution):** The model calls `DatasetDBQuery` with the exact
   generated query:
   `SELECT title, amount_allocated, item_url FROM budget_items_data WHERE year = 2025 AND code = '20'`
4. **Turn 4 (Synthesis):** The model returns the final answer in Hebrew with the
   exact budget amount and a link to the BudgetKey details portal.
