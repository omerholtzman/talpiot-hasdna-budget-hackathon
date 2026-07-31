import os
import sys
import concurrent.futures
import argparse
import uuid
import json
import re
import csv
import io as _io
from datetime import datetime
from typing import List, Optional, Any, Dict
from mcp_client import MCPClient, ToolDefinition
from llm_providers import (
    Message,
    GeminiStudioProvider,
    AnthropicProvider,
    VertexAIProvider,
    CLIClaudeProvider,
    LLMProvider,
)

from colorama import Fore, Style, init

# Hebrew appears in subjects, data and even this project's own path. On Windows the
# console/pipe defaults to cp1252, which raises UnicodeEncodeError mid-run. Force UTF-8
# before colorama wraps the streams; the helpers below still guard as a fallback.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

init()


# Color mappings backed by colorama
class Colors:
    BLUE = Fore.BLUE
    GREEN = Fore.GREEN
    YELLOW = Fore.YELLOW
    RED = Fore.RED
    CYAN = Fore.CYAN
    MAGENTA = Fore.MAGENTA
    RESET = Style.RESET_ALL
    BOLD = Style.BRIGHT


def fix_bidi(text: str) -> str:
    """Formats Hebrew/RTL text for LTR terminals."""
    try:
        from bidi.algorithm import get_display

        return get_display(text)
    except ImportError:
        return text


def emit(line: str, stream=None):
    """Prints a line, degrading gracefully if the stream cannot encode it.

    Logging must never be able to abort a run part-way through writing output files.
    """
    stream = stream or sys.stdout
    try:
        print(line, file=stream)
    except UnicodeEncodeError:
        enc = getattr(stream, "encoding", None) or "ascii"
        print(
            line.encode(enc, errors="replace").decode(enc, errors="replace"),
            file=stream,
        )


def print_agent(text: str):
    emit(f"{Colors.BLUE}{Colors.BOLD}[Agent]{Colors.RESET} {fix_bidi(text)}")


def print_mcp(text: str):
    emit(f"{Colors.GREEN}{Colors.BOLD}[MCP]{Colors.RESET} {fix_bidi(text)}")


def print_llm(text: str):
    emit(f"{Colors.YELLOW}{Colors.BOLD}[LLM]{Colors.RESET} {fix_bidi(text)}")


def print_error(text: str):
    emit(
        f"{Colors.RED}{Colors.BOLD}[Error]{Colors.RESET} {fix_bidi(text)}",
        stream=sys.stderr,
    )


def clean_markdown_fences(content: str) -> str:
    """Removes leading/trailing code block fences from the content and the frontmatter."""
    content = content.strip()

    # Case 1: The entire content is wrapped in a single outer code fence
    # e.g. starting with ```markdown and ending with ```
    if content.startswith("```") and content.endswith("```"):
        first_newline = content.find("\n")
        if first_newline != -1:
            content = content[first_newline:].strip()
        if content.endswith("```"):
            content = content[:-3].strip()

    # Case 2: Only the frontmatter is wrapped in code fences
    # e.g. ```yaml\n---\n...\n---\n```\n# Document Title
    # We strip the wrapping fences from around the frontmatter block
    content = re.sub(r"^```[a-zA-Z]*\s*\n(---[\s\S]*?\n---)\s*\n```", r"\1", content)

    return content


# Names the Phase 1 skill asks for; used to label extracted CSV blocks.
CSV_BLOCK_NAMES = ["selected_items", "related_items", "time_series", "top_line"]
_FENCE_RE = re.compile(r"```([a-zA-Z]*)[^\S\n]*\n(.*?)```", re.S)


def _looks_like_csv(body: str) -> bool:
    """Heuristic for a fenced block that is CSV but was not tagged ```csv."""
    lines = [l for l in body.strip().splitlines() if l.strip()]
    if len(lines) < 2:
        return False
    header_commas = lines[0].count(",")
    if header_commas < 1:
        return False
    # Most data rows should have roughly the header's column count.
    consistent = sum(1 for l in lines[1:] if abs(l.count(",") - header_commas) <= 2)
    return consistent >= max(1, int(0.6 * (len(lines) - 1)))


_ASCII_HEADER_RE = re.compile(r"^[A-Za-z0-9_,\s\-]+$")
_NON_CSV_PREFIXES = ("#", ">", "|", "*", "-", "`", "=")


def _label_from(preceding: str) -> Optional[str]:
    """The known block name mentioned closest to the end of `preceding`, if any."""
    name, best = None, -1
    for known in CSV_BLOCK_NAMES:
        idx = preceding.rfind(known)
        if idx > best:
            best, name = idx, known
    return name if best >= 0 else None


def _extract_unfenced_csv(text: str) -> List[tuple]:
    """Finds CSV that was emitted without code fences.

    Models routinely ignore the ```csv instruction and write a heading such as
    `### selected_items` followed by bare comma-separated lines. Guards against
    swallowing prose: a run needs >=3 columns, >=2 consistent lines, and either a
    preceding block-name heading or an ASCII identifier-style header row.
    """
    lines = text.splitlines()
    found: List[tuple] = []
    label: Optional[str] = None
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        # Track the most recent short line naming a known block (a heading).
        if stripped and len(stripped) <= 80:
            hit = _label_from(stripped)
            if hit:
                label = hit
                i += 1
                continue

        base = stripped.count(",")
        if base >= 2 and not stripped.startswith(_NON_CSV_PREFIXES):
            run = [lines[i]]
            j = i + 1
            while j < len(lines):
                nxt = lines[j].strip()
                if not nxt or nxt.startswith(_NON_CSV_PREFIXES):
                    break
                if abs(nxt.count(",") - base) > 3:
                    break
                run.append(lines[j])
                j += 1
            if len(run) >= 2 and (label or _ASCII_HEADER_RE.match(stripped)):
                found.append((label, "\n".join(r.rstrip() for r in run)))
                label = None
                i = j
                continue
        i += 1
    return found


def extract_csv_blocks(text: str) -> List[tuple]:
    """Pulls CSV blocks out of an agent's markdown answer.

    Returns [(name, csv_text), ...]. Handles three shapes, because models are
    inconsistent: fenced as ```csv, fenced untagged, and not fenced at all.
    The name comes from the nearest preceding mention of a known block name, so
    `selected_items` / `time_series` land in predictably named files.
    """
    text = text or ""
    candidates: List[tuple] = []

    for m in _FENCE_RE.finditer(text):
        lang, body = m.group(1).lower(), m.group(2).strip()
        if not body:
            continue
        if lang != "csv" and not (lang == "" and _looks_like_csv(body)):
            continue
        candidates.append((_label_from(text[max(0, m.start() - 300) : m.start()]), body))

    # Blank out fenced regions (preserving line count) so unfenced scanning cannot
    # re-capture the same rows, then look for bare CSV in what remains.
    masked = _FENCE_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    candidates.extend(_extract_unfenced_csv(masked))

    blocks: List[tuple] = []
    used: Dict[str, int] = {}
    for label, body in candidates:
        name = label or f"block_{len(blocks) + 1}"
        used[name] = used.get(name, 0) + 1
        if used[name] > 1:
            name = f"{name}_{used[name]}"
        blocks.append((name, body))
    return blocks


def _csv_field_issues(body: str) -> tuple:
    """Returns (column_count, [line numbers whose field count differs from the header]).

    An unquoted comma inside a Hebrew title shifts every later column, so the row
    parses but is silently wrong. Comparing models on such rows would be worthless,
    hence the check.
    """
    try:
        rows = list(csv.reader(_io.StringIO(body)))
    except Exception:
        return 0, []
    rows = [r for r in rows if r and any(c.strip() for c in r)]
    if not rows:
        return 0, []
    cols = len(rows[0])
    bad = [i + 2 for i, r in enumerate(rows[1:]) if len(r) != cols]
    return cols, bad


def write_csv_blocks(text: str, out_dir: str, warn_if_empty: bool = True) -> List[Dict[str, Any]]:
    """Writes every CSV block found in `text` into out_dir. Returns file metadata."""
    written: List[Dict[str, Any]] = []
    for name, body in extract_csv_blocks(text):
        path = os.path.join(out_dir, f"{name}.csv")
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(body if body.endswith("\n") else body + "\n")
            rows = max(0, len([l for l in body.splitlines() if l.strip()]) - 1)
            cols, malformed = _csv_field_issues(body)
            written.append({
                "name": name,
                "path": path,
                "rows": rows,
                "columns": cols,
                "malformed_rows": malformed,
            })
            print_agent(f"Wrote {rows} rows to {os.path.basename(path)}")
            if malformed:
                shown = ", ".join(str(n) for n in malformed[:10])
                more = "..." if len(malformed) > 10 else ""
                print_error(
                    f"  {os.path.basename(path)}: {len(malformed)} row(s) have the wrong "
                    f"field count (line {shown}{more}) - most likely an unquoted comma in a "
                    f"text column, which shifts every later value."
                )
        except Exception as e:
            print_error(f"Failed to write CSV {path}: {e}")
    if not written and warn_if_empty:
        print_error(
            "No CSV blocks found in the Phase 1 answer - check the raw answer file."
        )
    return written


SAVE_CSV_TOOL = ToolDefinition(
    name="SaveCSV",
    description=(
        "Run a SQL query and save its FULL result directly to a CSV file on disk.\n\n"
        "Use this instead of writing table rows out yourself. The rows go straight from the "
        "database to the file without passing through you, so nothing can be mistyped, "
        "truncated, or invented, and large results cost you almost no context.\n\n"
        "Paging is automatic - you get every matching row, not the first 50.\n"
        "You receive back only the row count, the column names and two sample rows.\n\n"
        "Rules:\n"
        "- Same SQL rules as DatasetDBQuery (never use '%' in a code filter; always pin `level`).\n"
        "- Do not add LIMIT unless you genuinely want fewer rows.\n"
        "- Check the returned `warnings` field; if it is not null, fix the query and save again."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "filename": {
                "type": "string",
                "description": "Base name without extension, e.g. 'selected_items' or 'item_budgets'.",
            },
            "query": {
                "type": "string",
                "description": "PostgreSQL SELECT whose full result becomes the CSV.",
            },
            "dataset": {
                "type": "string",
                "description": "Dataset id. Defaults to budget_items_data.",
            },
        },
        "required": ["filename", "query"],
    },
)

_QUERY_PAGE = 1000  # server caps page_size at 1000


def _run_query_paged(mcp_client, dataset: str, query: str, max_rows: int = 20000) -> tuple:
    """Fetches every row of `query` by wrapping it in LIMIT/OFFSET pages."""
    inner = query.strip().rstrip(";")
    rows: List[Dict[str, Any]] = []
    warnings = None
    offset = 0
    while True:
        wrapped = f"SELECT * FROM ({inner}) AS _sub LIMIT {_QUERY_PAGE} OFFSET {offset}"
        data = json.loads(
            mcp_client.call_tool(
                "DatasetDBQuery",
                {"dataset": dataset, "query": wrapped, "page_size": _QUERY_PAGE},
            )
        )
        if warnings is None:
            warnings = data.get("warnings")
        batch = data.get("rows") or []
        rows.extend(batch)
        if len(batch) < _QUERY_PAGE or len(rows) >= max_rows:
            break
        offset += _QUERY_PAGE
    return rows, warnings


def _csv_value(v: Any) -> Any:
    """Renders a DB value for CSV: NULL -> empty, whole floats -> ints."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def handle_save_csv(mcp_client, args: Dict[str, Any], csv_dir: str) -> str:
    """Executes the SaveCSV tool. Returns a compact JSON receipt for the model."""
    raw_name = str(args.get("filename") or "output")
    name = re.sub(r"[^\w\-.]", "_", os.path.basename(raw_name)).strip("._") or "output"
    if name.lower().endswith(".csv"):
        name = name[:-4]
    query = args.get("query") or ""
    dataset = args.get("dataset") or "budget_items_data"

    rows, warnings = _run_query_paged(mcp_client, dataset, query)
    if not rows:
        return json.dumps(
            {"saved": None, "rows": 0, "warnings": warnings,
             "note": "Query returned no rows - nothing was written."},
            ensure_ascii=False,
        )

    columns = list(rows[0].keys())
    path = os.path.join(csv_dir, f"{name}.csv")
    os.makedirs(csv_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: _csv_value(r.get(k)) for k in columns})

    print_agent(f"SaveCSV wrote {len(rows)} rows x {len(columns)} cols to {name}.csv")
    return json.dumps(
        {
            "saved": f"{name}.csv",
            "rows": len(rows),
            "columns": columns,
            "warnings": warnings,
            "sample": rows[:2],
        },
        ensure_ascii=False,
    )


def inspect_csv_dir(out_dir: str) -> List[Dict[str, Any]]:
    """Describes every CSV in out_dir, regardless of whether SaveCSV or the
    answer-text parser produced it, so the run summary covers both paths."""
    found: List[Dict[str, Any]] = []
    for fname in sorted(os.listdir(out_dir)):
        if not fname.lower().endswith(".csv"):
            continue
        path = os.path.join(out_dir, fname)
        try:
            body = _io.open(path, encoding="utf-8-sig").read()
        except Exception as e:
            print_error(f"Could not read {fname}: {e}")
            continue
        cols, malformed = _csv_field_issues(body)
        rows = max(0, len([l for l in body.splitlines() if l.strip()]) - 1)
        found.append({"name": fname[:-4], "path": path, "rows": rows,
                      "columns": cols, "malformed_rows": malformed})
        if malformed:
            shown = ", ".join(str(n) for n in malformed[:10])
            more = "..." if len(malformed) > 10 else ""
            print_error(
                f"  {fname}: {len(malformed)} row(s) have the wrong field count "
                f"(line {shown}{more}) - likely an unquoted comma in a text column."
            )
    return found


def _read_csv_rows(path: str) -> List[Dict[str, str]]:
    """Reads a CSV written by the pipeline (utf-8-sig)."""
    with _io.open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _max_turn(trace: List[Dict[str, Any]]) -> int:
    """Highest turn number in a trace (the trace has one entry per tool call)."""
    return max((e.get("turn") or 0 for e in trace), default=0)


def get_default_output_path(subject: Optional[str], label: Optional[str] = None) -> str:
    """Generates a default output path under output_examples with subject-timestamp."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if subject:
        # Normalize/clean subject slug
        slug = re.sub(r"[^\w\-_]", "_", subject)
        run_name = f"{slug}-{timestamp}"
        filename = f"{slug}.md"
    else:
        run_name = f"report-{timestamp}"
        filename = "report.md"
    if label:
        # Keeps parallel runs of different models from colliding.
        label_slug = re.sub(r"[^\w\-.]", "_", label)
        run_name = f"{run_name}-{label_slug}"

    base_dir = os.path.join(os.path.dirname(__file__), "output_examples")
    output_dir = os.path.join(base_dir, run_name)
    os.makedirs(output_dir, exist_ok=True)
    return os.path.join(output_dir, filename)


def load_skill_file(filename: str, today_str: str, model_name: str) -> str:
    """Reads a phase-specific skill instruction file and injects placeholders."""
    path = os.path.join(os.path.dirname(__file__), "instructions", filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        return content.replace("{TODAY}", today_str).replace("{MODEL}", model_name)
    except Exception as e:
        print_error(f"Failed to load skill instruction from {path}: {e}")
        sys.exit(1)


def setup_mcp(mcp_url: str) -> MCPClient:
    """Connects to and initializes the MCP server session."""
    print_agent(f"Connecting to BudgetKey MCP server at {mcp_url}...")
    mcp_client = MCPClient(mcp_url)
    mcp_client.connect()
    print_agent("Initializing session...")
    mcp_client.initialize()
    return mcp_client


def setup_llm_provider(
    provider_name: str, model_override: Optional[str] = None
) -> LLMProvider:
    """Instantiates the selected LLM provider adapter."""
    print_agent(f"Setting up provider: {provider_name}...")
    if provider_name == "gemini":
        model = model_override or "gemini-3.5-flash"
        return GeminiStudioProvider(model=model)
    elif provider_name == "anthropic":
        model = model_override or "claude-3-5-sonnet-latest"
        return AnthropicProvider(model=model)
    elif provider_name == "vertex":
        model = model_override or "gemini-2.5-flash"
        return VertexAIProvider(model=model)
    elif provider_name == "cli-claude":
        cmd_name = model_override or "claude"
        return CLIClaudeProvider(cmd=cmd_name)
    else:
        raise ValueError(f"Unknown provider: {provider_name}")


def _flush_trace(trace_path: Optional[str], trace_data: List[Dict[str, Any]]):
    if trace_path:
        try:
            with open(trace_path, "w", encoding="utf-8") as f:
                json.dump(trace_data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass


def run_agent_loop(
    provider: LLMProvider,
    mcp_client: MCPClient,
    tools: List[Any],
    prompt: str,
    system_instruction: str,
    max_turns: int = 10,
    trace_path: Optional[str] = None,
    silent: bool = False,
    agent_name: str = "Agent",
    agent_color: str = Colors.BLUE,
    csv_dir: Optional[str] = None,
) -> tuple[str, List[Dict[str, Any]]]:
    """Runs the autonomous ReAct reasoning-action loop to answer the prompt.

    Returns:
        (final_response, execution_trace)
    """

    def log_agent(msg):
        if not silent:
            emit(
                f"{agent_color}{Colors.BOLD}[Agent - {agent_name}]{Colors.RESET} {fix_bidi(msg)}"
            )

    def log_mcp(msg):
        if not silent:
            emit(
                f"{agent_color}{Colors.BOLD}[MCP - {agent_name}]{Colors.RESET} {fix_bidi(msg)}"
            )

    def log_llm(msg):
        if not silent:
            emit(
                f"{agent_color}{Colors.BOLD}[LLM - {agent_name}]{Colors.RESET} {fix_bidi(msg)}"
            )

    def log_error(msg):
        # Always print errors so failures in background threads are visible
        print_error(f"[Agent - {agent_name}] {msg}")

    # Expose the local SaveCSV tool only when an output directory is configured.
    # `tools` is shared across the parallel phases, so never mutate it in place.
    if csv_dir:
        tools = list(tools) + [SAVE_CSV_TOOL]

    # 1. Initialize History with instructions & user query
    history: List[Message] = [
        Message(role="user", content=f"System Instruction:\n{system_instruction}"),
        Message(
            role="assistant",
            content="הבנתי. אני מוכן לעזור לך למצוא מידע בתקציב המדינה. כיצד אוכל לסייע?",
        ),
    ]
    history.append(Message(role="user", content=prompt))
    log_agent(f"User Query: {prompt.splitlines()[0]}")

    # 2. Loop Execution
    turn = 0
    response = None
    final_response = ""
    execution_trace: List[Dict[str, Any]] = []

    while turn < max_turns:
        turn += 1
        log_agent(f"Turn {turn}: Thinking...")

        # Call LLM reasoning
        try:
            response = provider.generate(history, tools)
        except Exception as e:
            log_error(f"LLM generation error: {e}")
            break

        # Termination check (synthesis/final response without tools)
        if not response.tool_calls:
            execution_trace.append(
                {
                    "turn": turn,
                    "tool_name": None,
                    "arguments": None,
                    "reasoning": response.content or "",
                    "output": None,
                    "raw_payload": getattr(provider, "last_payload", None),
                }
            )
            _flush_trace(trace_path, execution_trace)

            if response.content:
                preview = response.content.splitlines()[0] if response.content else ""
                if len(response.content) > 150:
                    preview = response.content[:150].replace("\n", " ") + "..."
                log_llm(f"Response Preview:\n{preview}")
                final_response = response.content
            log_agent("Task completed. No more tool calls requested.")
            break

        if response.content:
            preview = response.content.splitlines()[0] if response.content else ""
            if len(response.content) > 150:
                preview = response.content[:150].replace("\n", " ") + "..."
            log_llm(f"Response Preview:\n{preview}")
            final_response = response.content

        history.append(response)

        # Action execution phase
        log_agent(f"Model requested {len(response.tool_calls)} tool call(s):")
        # The payload is identical for every call in a turn (it is the request that produced
        # them all), so record it only once per turn instead of once per call.
        turn_payload = getattr(provider, "last_payload", None)
        for tc in response.tool_calls:
            name = tc["name"]
            args_data = tc["arguments"]
            call_id = tc.get("id") or str(uuid.uuid4())

            log_mcp(
                f"Executing tool {Colors.BOLD}{name}{Colors.RESET} with arguments: {args_data}"
            )

            try:
                # Call tool on MCP server
                if name == SAVE_CSV_TOOL.name and csv_dir:
                    tool_output = handle_save_csv(mcp_client, args_data, csv_dir)
                else:
                    tool_output = mcp_client.call_tool(name, args_data)

                # Console output preview formatting (short, non-noisy)
                try:
                    parsed_output = json.loads(tool_output)
                    pretty_output = json.dumps(
                        parsed_output, indent=2, ensure_ascii=False
                    )
                    preview = (
                        pretty_output[:150] + "..."
                        if len(pretty_output) > 150
                        else pretty_output
                    )
                except Exception:
                    preview = (
                        tool_output[:100] + "..."
                        if len(tool_output) > 100
                        else tool_output
                    )
                log_mcp(f"Output Preview:\n{preview}")

                # Capture to execution trace in memory (complete raw payload & output)
                execution_trace.append(
                    {
                        "turn": turn,
                        "tool_name": name,
                        "arguments": args_data,
                        "reasoning": response.content or "",
                        "output": tool_output,
                        "raw_payload": turn_payload,
                    }
                )
                turn_payload = None
                _flush_trace(trace_path, execution_trace)

                # Feed output back to history
                history.append(
                    Message(
                        role="tool",
                        content=tool_output,
                        tool_response_id=call_id,
                        name=name,
                    )
                )
            except Exception as e:
                log_error(f"Tool execution failed: {e}")

                execution_trace.append(
                    {
                        "turn": turn,
                        "tool_name": name,
                        "arguments": args_data,
                        "reasoning": response.content or "",
                        "output": f"Error: {e}",
                        "raw_payload": turn_payload,
                    }
                )
                turn_payload = None
                _flush_trace(trace_path, execution_trace)

                history.append(
                    Message(
                        role="tool",
                        content=f"Error executing tool {name}: {e}",
                        tool_response_id=call_id,
                        name=name,
                    )
                )

    # A final synthesis turn is forced in two cases:
    #  1. The turn limit was hit while the model still wanted to call tools.
    #  2. The model stopped without emitting any text. Without this, `final_response`
    #     silently keeps the last turn's *reasoning* - narration like "now I will run
    #     two queries" - and that gets passed downstream as if it were the answer.
    hit_limit = bool(response) and turn >= max_turns and bool(response.tool_calls)
    stopped_empty = (
        bool(response)
        and not response.tool_calls
        and not (response.content or "").strip()
    )
    if hit_limit or stopped_empty:
        why = (
            "Reached maximum iteration limit"
            if hit_limit
            else "Model stopped without producing an answer"
        )
        print_error(
            f"{why}. Forcing final synthesis turn for query: '{prompt[:60]}...'"
        )
        history.append(
            Message(
                role="user",
                content=(
                    "Stop searching. Produce your final answer now, in exactly the output format "
                    "your instructions specify, and do not describe what you are about to do.\n\n"
                    "Use ONLY values that a tool actually returned in this conversation. If you "
                    "never retrieved a figure, you do not know it: leave the field empty rather "
                    "than estimating, recalling, or writing a placeholder such as 0. If a table "
                    "needs data you never fetched, you may still save it with the tools you have; "
                    "otherwise omit it and say so. Never invent its contents."
                ),
            )
        )
        try:
            log_agent("Final Turn: Thinking...")
            response = provider.generate(history, tools)
            if response.content:
                preview = response.content.splitlines()[0] if response.content else ""
                if len(response.content) > 150:
                    preview = response.content[:150].replace("\n", " ") + "..."
                log_llm(f"Response Preview:\n{preview}")
                final_response = response.content

            execution_trace.append(
                {
                    "turn": turn + 1,
                    "tool_name": "forced_synthesis",
                    "arguments": None,
                    "reasoning": "Forced final synthesis turn due to turn limit.",
                    "output": response.content or "",
                    "raw_payload": getattr(provider, "last_payload", None),
                }
            )
            _flush_trace(trace_path, execution_trace)
        except Exception as e:
            log_error(f"LLM final synthesis generation error: {e}")

    return final_response, execution_trace


def main():
    import pipeline  # local import: pipeline imports agent's log helpers

    parser = argparse.ArgumentParser(description="BudgetKey MCP Autonomous Agent")
    parser.add_argument(
        "--subject",
        type=str,
        default=None,
        help="The subject to query budget information for",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default="gemini",
        choices=["gemini", "anthropic", "vertex", "cli-claude"],
        help="LLM Provider to use (gemini, anthropic, vertex, cli-claude)",
    )
    parser.add_argument("--model", type=str, default=None, help="Model name override")
    parser.add_argument(
        "--mcp-url",
        type=str,
        default="https://next.obudget.org/mcp",
        help="BudgetKey MCP server URL",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Output Markdown file path (defaults to /tmp/<subject>-<timestamp>.md)",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="List all available tools from the MCP server and exit",
    )
    parser.add_argument(
        "--test",
        "-t",
        action="store_true",
        help="Run in test mode (limits execution to 1 loop turn)",
    )
    parser.add_argument(
        "--phase1-only",
        action="store_true",
        help="Run only Phase 1 (budget items), write its CSV blocks and stop. "
        "Intended for evaluating item-discovery completeness across models.",
    )
    parser.add_argument(
        "--label",
        type=str,
        default=None,
        help="Extra tag appended to the output directory name. Defaults to the "
        "model name when --phase1-only is set, so parallel model runs do not collide.",
    )
    args = parser.parse_args()

    if not args.list_tools and not args.subject:
        parser.error("either --subject or --list-tools flag must be set")

    # Inject today's date and model name into instruction variables later
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Determine prompt text
    prompt_text = ""
    if args.subject:
        subject_prompt_path = os.path.join(
            os.path.dirname(__file__), "instructions", "subject_prompt.txt"
        )
        try:
            with open(subject_prompt_path, "r", encoding="utf-8") as f:
                template = f.read().strip()
            prompt_text = template.replace("{SUBJECT}", args.subject)
        except Exception as e:
            print_error(
                f"Failed to load subject prompt template from {subject_prompt_path}: {e}"
            )
            sys.exit(1)

    # Output path is resolved after the provider is known, so the model name can tag the run dir.
    output_path = args.output

    # Initialize MCP Client
    try:
        mcp_client = setup_mcp(args.mcp_url)
    except Exception as e:
        print_error(f"Failed to connect/initialize MCP server: {e}")
        sys.exit(1)

    try:
        print_agent("Fetching available tools...")
        tools = mcp_client.list_tools()
        print_agent(f"Successfully loaded {len(tools)} tools from MCP:")
        for t in tools:
            print_mcp(f"  - {t.name}: {t.description.splitlines()[0]}")

        if args.list_tools:
            return

        # Setup LLM Provider
        try:
            provider = setup_llm_provider(args.provider, args.model)
        except Exception as e:
            print_error(f"Failed to initialize LLM provider: {e}")
            sys.exit(1)

        model_name = (
            getattr(provider, "model", None)
            or getattr(provider, "cmd", None)
            or args.model
            or "unknown-model"
        )

        # Resolve the output path now that the model is known.
        if not output_path:
            label = args.label or (model_name if args.phase1_only else None)
            output_path = get_default_output_path(args.subject, label)

        # Determine trace path first
        trace_path = None
        if output_path:
            parent_dir = os.path.dirname(output_path)
            if parent_dir:
                trace_path = os.path.join(parent_dir, "trace.json")

        # Run structured pipeline
        if args.test:
            print_agent("Running in test mode (Phase 1 budget lookup only, 1 turn)...")
            skill_p1 = load_skill_file("skill_phase1_budget.md", today_str, model_name)
            final_ans, combined_trace = run_agent_loop(
                provider,
                mcp_client,
                tools,
                f"Gather budget items data for the subject: {args.subject}",
                skill_p1,
                max_turns=1,
                trace_path=trace_path,
            )
        else:
            print_agent("Starting Parallel Multi-Step Research Pipeline...")
            combined_trace = []
            subject = args.subject

            # Step 1: Budget Analysis (Sequential, runs in main thread first)
            print_agent("=== Step 1: Budget items analysis (deterministic pipeline) ===")
            # Phase 1 is no longer a ReAct loop. Retrieval and aggregation are plain SQL,
            # so every figure below comes from the database rather than from the model,
            # and recall is bounded by the query rather than by a turn budget.
            run_dir = os.path.dirname(output_path) or "."
            os.makedirs(run_dir, exist_ok=True)
            trace_p1 = []
            try:
                pipeline.run_pipeline(subject, run_dir, provider, mcp_client)
                phase1_ans = pipeline.build_digest(run_dir, subject)
            except Exception as e:
                print_error(f"Phase 1 pipeline failed: {e}")
                phase1_ans = f"Phase 1 failed for '{subject}': {e}"

            if args.phase1_only:
                print_agent("=== Phase 1 only: writing CSV output and stopping ===")
                run_dir = os.path.dirname(output_path) or "."
                os.makedirs(run_dir, exist_ok=True)

                # The pipeline writes the CSVs itself. The text-scraping fallback stays
                # only for answers produced by the old ReAct path.
                pre_existing = [f for f in os.listdir(run_dir) if f.lower().endswith(".csv")]
                write_csv_blocks(phase1_ans, run_dir, warn_if_empty=not pre_existing)
                csv_files = inspect_csv_dir(run_dir)
                if not csv_files:
                    print_error("No CSV output at all - the pipeline produced nothing.")

                # The digest that downstream phases would have received.
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(phase1_ans or "")
                print_agent(f"Saved Phase 1 digest to: {output_path}")

                summary = {
                    "subject": subject,
                    "provider": args.provider,
                    "model": model_name,
                    "digest_chars": len(phase1_ans or ""),
                    "note": "cost and selection counts are in run_summary.json",
                    "csv_files": [
                        {
                            "name": c["name"],
                            "rows": c["rows"],
                            "columns": c.get("columns"),
                            "malformed_rows": len(c.get("malformed_rows") or []),
                        }
                        for c in csv_files
                    ],
                    "malformed_rows_total": sum(
                        len(c.get("malformed_rows") or []) for c in csv_files
                    ),
                }
                summary_path = os.path.join(run_dir, "phase1_summary.json")
                with open(summary_path, "w", encoding="utf-8") as f:
                    json.dump(summary, f, indent=2, ensure_ascii=False)
                print_agent(f"Saved run summary to: {summary_path}")
                print_agent(
                    f"Phase 1 done: {summary['turns_used']}/{summary['turns_allowed']} turns, "
                    f"{summary['tool_calls']} tool calls ({summary['tool_errors']} errored), "
                    f"{sum(c['rows'] for c in csv_files)} CSV rows total."
                )
                return

            # Step 2, 3 & 5: Contracts, Decisions & Hierarchy in Parallel
            def run_contracts():
                print_agent(
                    "  - Initiating Contracts and Suppliers Analysis (Thread 1)"
                )
                local_provider = setup_llm_provider(args.provider, args.model)
                skill_p2 = load_skill_file(
                    "skill_phase2_contracts.md", today_str, model_name
                )
                # Pass Step 1 output to Step 2 so it has the budget codes
                phase2_prompt = (
                    f"Please collect procurement contracts and supplier aggregate totals for the subject: '{subject}'.\n"
                    f"Here is the budget items data collected in Step 1:\n{phase1_ans}"
                )
                local_mcp = setup_mcp(args.mcp_url)
                try:
                    return run_agent_loop(
                        local_provider,
                        local_mcp,
                        tools,
                        phase2_prompt,
                        skill_p2,
                        max_turns=6,
                        trace_path=None,
                        silent=False,
                        agent_name="Contracts",
                        agent_color=Colors.MAGENTA,
                    )
                finally:
                    local_mcp.close()

            def run_decisions():
                print_agent("  - Initiating Government Decisions Analysis (Thread 2)")
                local_provider = setup_llm_provider(args.provider, args.model)
                skill_p3 = load_skill_file(
                    "skill_phase3_decisions.md", today_str, model_name
                )
                # Pass Step 1 output to Step 3 for extra context
                phase3_prompt = (
                    f"Please query and extract government decisions related to the subject: '{subject}'.\n"
                    f"Here is the budget items data collected in Step 1:\n{phase1_ans}"
                )
                local_mcp = setup_mcp(args.mcp_url)
                try:
                    return run_agent_loop(
                        local_provider,
                        local_mcp,
                        tools,
                        phase3_prompt,
                        skill_p3,
                        max_turns=6,
                        trace_path=None,
                        silent=False,
                        agent_name="Decisions",
                        agent_color=Colors.YELLOW,
                    )
                finally:
                    local_mcp.close()

            def run_hierarchy():
                # Deterministic. The old skill_phase5_hierarchy.md told the model to
                # query `code LIKE '24%'`, which mixes budget levels and double-counts
                # a ministry with all of its children - the server flags it as a
                # mistake. The pipeline already wrote hierarchy.csv from the same
                # office list, so this just renders it.
                print_agent("  - Building budget hierarchy from pipeline output")
                path = os.path.join(run_dir, "hierarchy.csv")
                if not os.path.exists(path):
                    return ("No hierarchy data available for this subject.", [])
                rows = [r for r in _read_csv_rows(path) if r.get("code")]

                def amount(r):
                    try:
                        return float(r.get("amount_allocated") or 0)
                    except ValueError:
                        return 0.0

                rows = [r for r in rows if amount(r) > 0]
                rows.sort(key=lambda r: amount(r), reverse=True)
                lines = [
                    "Budget hierarchy (levels 1-3) for the ministries funding this subject, "
                    "latest budget year. Amounts in shekels; a parent already includes its "
                    "children, so these must not be summed together.",
                    "code | title | level | amount_allocated | item_url",
                ]
                for r in rows[:40]:
                    lines.append("%s | %s | %s | %d | %s" % (
                        r["code"], r.get("title", ""), r.get("level", ""),
                        amount(r), r.get("item_url", "")))
                return ("\n".join(lines), [])

            print_agent(
                "Firing research threads (Contracts, Decisions, and Hierarchy) concurrently..."
            )
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                future_p2 = executor.submit(run_contracts)
                future_p3 = executor.submit(run_decisions)
                future_p5 = executor.submit(run_hierarchy)

                print_agent(
                    "Waiting for parallel data collection threads to complete..."
                )
                phase2_ans, trace_p2 = future_p2.result()
                phase3_ans, trace_p3 = future_p3.result()
                phase5_ans, trace_p5 = future_p5.result()

            print_agent("All research threads completed! Collating data...")

            # Check if workers reached the turn limit
            if len(trace_p2) >= 6:
                print_error(
                    "Warning: Step 2 (Contracts) reached the maximum turn limit of 6!"
                )
            if len(trace_p3) >= 6:
                print_error(
                    "Warning: Step 3 (Decisions) reached the maximum turn limit of 6!"
                )
            if len(trace_p5) >= 6:
                print_error(
                    "Warning: Step 5 (Hierarchy) reached the maximum turn limit of 6!"
                )

            combined_trace.extend(trace_p2)
            combined_trace.extend(trace_p3)
            combined_trace.extend(trace_p5)
            _flush_trace(trace_path, combined_trace)

            # Step 4: Dashboard Synthesis (runs in main thread, not silent)
            print_agent("=== Step 4: Final dashboard synthesis ===")
            skill_p4 = load_skill_file(
                "skill_phase_final_synthesis.md", today_str, model_name
            )
            template_content = load_skill_file(
                "synthesis_template.md", today_str, model_name
            )

            # Pre-populate static metadata fields
            template_content = template_content.replace("{TODAY}", today_str).replace(
                "{MODEL}", model_name
            )

            phase4_prompt = (
                f"Compile the final Hebrew Markdown dashboard document for the subject '{subject}' by filling the template below.\n\n"
                f"--- PHASE 1 DATA (BUDGET TIME-SERIES) ---\n{phase1_ans}\n\n"
                f"--- PHASE 5 DATA (BUDGET HIERARCHY FLOW) ---\n{phase5_ans}\n\n"
                f"--- PHASE 2 DATA (CONTRACTS & SUPPLIERS) ---\n{phase2_ans}\n\n"
                f"--- PHASE 3 DATA (GOVERNMENT DECISIONS) ---\n{phase3_ans}\n\n"
                f"Here is the template format you MUST fill. Fill all remaining placeholders (like {{SUBJECT_HEBREW}}, {{SUMMARY}}, etc.) using the data above. "
                f"Respond ONLY with the filled template starting immediately with '---'. Do NOT wrap your entire response in code blocks:\n\n"
                f"{template_content}"
            )
            final_ans, trace_p4 = run_agent_loop(
                provider,
                mcp_client,
                tools,
                phase4_prompt,
                skill_p4,
                max_turns=1,
                trace_path=trace_path,
                agent_name="Synthesis",
                agent_color=Colors.BLUE,
            )
            combined_trace.extend(trace_p4)
            _flush_trace(trace_path, combined_trace)

        # Save output to Markdown file
        if final_ans and output_path:
            parent_dir = os.path.dirname(output_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            try:
                cleaned_ans = clean_markdown_fences(final_ans)
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(cleaned_ans)
                print_agent(f"Saved final markdown response to: {output_path}")

                # Save execution trace to JSON file in same directory
                trace_path = os.path.join(parent_dir, "trace.json")
                with open(trace_path, "w", encoding="utf-8") as f:
                    json.dump(combined_trace, f, indent=2, ensure_ascii=False)
                print_agent(f"Saved execution trace to: {trace_path}")

            except Exception as e:
                print_error(f"Failed to save outputs to {parent_dir}: {e}")

    finally:
        print_agent("Closing MCP connection...")
        mcp_client.close()


if __name__ == "__main__":
    main()
