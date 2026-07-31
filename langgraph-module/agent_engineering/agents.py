"""
The actual "workers" behind each of the five LangGraph nodes.

Phase 1 is NOT an agent. It is a deterministic pipeline (step1_pipeline.py):
plain SQL for retrieval and aggregation, with the model used only to
classify — which ministries, which programs, which lines. Every budget
figure it produces comes from the database, so none can be invented, and
its recall is bounded by the query rather than by a turn budget. It runs
in a worker thread and writes its output as CSVs into `state["run_dir"]`;
what flows on through the graph is a short digest of those files.

Phases 2 and 3 are classic ReAct-style tool-calling agents: each is
handed a system prompt (loaded from prompts/skill_phase*.md) plus the MCP
tools, and loops model -> tool call -> model until it produces a final
answer. LangGraph's prebuilt `create_react_agent` implements that loop
for us, so this module stays focused on *wiring things together*, not on
re-implementing an agent loop from scratch.

Phase 4 (hierarchy) is deterministic too: the pipeline already wrote
hierarchy.csv correctly, so the node just renders it.

Final synthesis is one call to the model with the four research results
pasted in as context, no tool loop at all — matching its skill file's
"no database tools in this phase".
"""
import asyncio
import logging
from datetime import date, datetime

from langchain_google_genai import ChatGoogleGenerativeAI

from langgraph.prebuilt import create_react_agent

from config import (AGENT_MAX_STEPS, GEMINI_MODEL, GOOGLE_PROJECT, GOOGLE_LOCATION,
                    PHASE1, PHASE2, PHASE3, PHASE4, PHASE5, TEMPLATE, PHASE_LABELS)

import agent_engineering.step1_pipeline as pipeline
from agent_engineering import blocks
from agent_engineering.llm_json import JSONLLM
from agent_engineering.mcp_tools import SyncMCPBridge, get_mcp_tools
from agent_engineering.prompt_loader import load_prompt
from agent_engineering.state import WikiState

# Phases 2/3 hand the MCP tools' raw JSON schemas (which set additionalProperties,
# per the MCP spec) to create_react_agent. langchain_google_genai's tool-schema
# converter logs a warning for every schema keyword Gemini's function-calling
# format doesn't support, which is nearly every call since the key is simply
# unsupported and gets dropped -- not an error. See llm_json.py's
# sanitize_gemini_schema for the same limitation on the response_schema side.
logging.getLogger("langchain_google_genai._function_utils").addFilter(
    lambda record: "is not supported in schema, ignoring" not in record.getMessage()
)


def _log(label: str, message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{label}] {message}", flush=True)


def _truncate(text: str, limit: int = 500) -> str:
    text = str(text)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... [truncated, {len(text)} chars total]"


def _log_agent_transcript(label: str, messages: list) -> None:
    """
    Walk the full ReAct message history returned by `create_react_agent` and
    print every tool call and tool response along the way, not just the
    final answer. `create_react_agent` returns the entire transcript in
    `result["messages"]`, so this is a post-hoc replay rather than a live
    stream - simpler than hooking into astream_events, and sufficient since
    each phase's own transcript is self-contained.
    """
    step = 0
    for msg in messages:
        msg_type = msg.__class__.__name__
        if msg_type == "HumanMessage":
            continue  # already logged as the phase's starting question
        elif msg_type == "AIMessage":
            tool_calls = getattr(msg, "tool_calls", None) or []
            if tool_calls:
                step += 1
                for call in tool_calls:
                    _log(label, f"  -> step {step}: calling tool '{call['name']}' with args {call['args']}")
            elif msg.content:
                _log(label, f"  <- model: {_truncate(msg.content)}")
        elif msg_type == "ToolMessage":
            _log(label, f"  <- tool '{msg.name}' response: {_truncate(msg.content)}")


def _llm() -> ChatGoogleGenerativeAI:
    """One shared factory for the Claude client, so model/key config lives in one place."""
    return ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,   # Vertex model IDs often use an @date suffix, e.g. claude-haiku-4-5@20251001
        project=GOOGLE_PROJECT,
        location=GOOGLE_LOCATION,              # Claude's Vertex region, not necessarily your default GCP region
    )

def today_str() -> str:
    """Today's date as YYYY-MM-DD, used to seed state['today'] in main.py."""
    return date.today().isoformat()


async def _run_research_phase(phase_name: str, state: WikiState) -> tuple[str, list[str]]:
    """
    Shared implementation for phases 2 and 3: build the system prompt,
    attach the MCP tools, run the ReAct loop, and return (result_text, errors).

    Any failure (bad SQL, a tool erroring out, the model giving up, etc.)
    is caught HERE rather than allowed to crash the whole pipeline. That
    matters because phases 2, 3 and 4 run concurrently: a failure in the
    "contracts" phase shouldn't take down the "decisions" and "hierarchy"
    phases too. A failed phase becomes a short placeholder string instead,
    and the synthesis phase will simply note that section had no data.
    """
    label = PHASE_LABELS.get(phase_name, phase_name)
    _log(label, f"Starting - subject: '{state['subject']}'")

    system_prompt = load_prompt(phase_name, TODAY=state["today"])
    tools = await get_mcp_tools()
    _log(label, f"Loaded {len(tools)} MCP tool(s): {', '.join(t.name for t in tools)}")

    agent = create_react_agent(_llm(), tools=tools, prompt=system_prompt)

    # Phase 1's findings go in as context: these phases need the budget codes
    # and the ministries to filter the contracts and decisions datasets by, and
    # rediscovering them per phase would be both slower and inconsistent.
    user_message = (
        f"Research the subject: {state['subject']}\n\n"
        f"Here are the budget items collected in Phase 1:\n{state['budget_result']}"
    )
    _log(label, f"Sending initial message ({len(user_message)} chars, incl. phase 1 digest)")
    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": user_message}]},
            config={"recursion_limit": AGENT_MAX_STEPS},
        )
        _log_agent_transcript(label, result["messages"])
        final_text = result["messages"][-1].content
        _log(label, f"Done - produced {len(final_text)} chars")
        return final_text, []
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any phase may fail independently
        _log(label, f"FAILED: {exc}")
        placeholder = f"_No data could be retrieved for this section ({exc})._"
        return placeholder, [f"{phase_name} failed: {exc}"]


# --- One thin LangGraph node per phase --------------------------------------------
# Each just calls the shared helper above and shapes the result into the
# partial-state-update dict LangGraph expects back from a node.

async def phase1_budget_node(state: WikiState) -> dict:
    """LangGraph node: find every budget item for the subject, deterministically.

    Runs step1_pipeline.run_pipeline in a worker thread — it is synchronous code
    that fans its own model calls out over a thread pool, and the SQL it
    issues reaches the MCP server through SyncMCPBridge, which posts each
    call back to this event loop. What lands in the state is the digest;
    the full item list and its per-year budgets stay in `run_dir` as CSVs.
    """
    label = PHASE_LABELS[PHASE1]
    subject, run_dir = state["subject"], state["run_dir"]
    _log(label, f"Starting - subject: '{subject}' (deterministic pipeline, not an agent)")
    _log(label, f"Writing CSVs to {run_dir}")

    tools = await get_mcp_tools()
    bridge = SyncMCPBridge(tools, asyncio.get_running_loop())
    provider = JSONLLM()

    try:
        summary = await asyncio.to_thread(
            pipeline.run_pipeline, subject, run_dir, provider, bridge
        )
        digest = await asyncio.to_thread(pipeline.build_digest, run_dir, subject)
    except Exception as exc:  # noqa: BLE001 - a failed phase 1 must not kill the run
        _log(label, f"FAILED: {exc}")
        placeholder = f"_No budget data could be retrieved for this subject ({exc})._"
        return {"budget_result": placeholder, "errors": [f"{PHASE1} failed: {exc}"]}

    print("type(summary)", type(summary))
    counts = summary.get("counts", {})
    print("type(counts)", type(counts))
    _log(label, f"Done - {counts.get('selected', 0)} item(s) selected, "
                f"{counts.get('dropped', 0)} dropped; digest is {len(digest)} chars")
    return {"budget_result": digest, "errors": []}


async def phase2_contracts_node(state: WikiState) -> dict:
    """LangGraph node: top contracts by volume + supplier totals for the subject."""
    text, errors = await _run_research_phase(PHASE2, state)
    return {"contracts_result": text, "errors": errors}


async def phase3_decisions_node(state: WikiState) -> dict:
    """LangGraph node: government decisions/resolutions mentioning the subject."""
    text, errors = await _run_research_phase(PHASE3, state)
    return {"decisions_result": text, "errors": errors}


async def phase4_hierarchy_node(state: WikiState) -> dict:
    """LangGraph node: the ministries' level 1-3 budget tree, for the flow diagram.

    No model and no tools. The old skill file had an agent query this itself
    with `code LIKE '24%'`, which mixes budget levels and double-counts a
    ministry with all of its children — the server flags it as a mistake.
    Phase 1's pipeline has already written the same rows correctly to
    hierarchy.csv, so this only formats them.
    """
    label = PHASE_LABELS[PHASE4]
    _log(label, "Starting - rendering hierarchy.csv from the phase 1 run")
    try:
        text = pipeline.render_hierarchy(state["run_dir"])
    except Exception as exc:  # noqa: BLE001 - consistent with the other phases
        _log(label, f"FAILED: {exc}")
        return {"hierarchy_result": f"_No hierarchy data ({exc})._",
                "errors": [f"{PHASE4} failed: {exc}"]}
    _log(label, f"Done - produced {len(text)} chars")
    return {"hierarchy_result": text, "errors": []}


async def final_phase_synthesis_node(state: WikiState) -> dict:
    """
    LangGraph node: combine phases 1-3 into the final Hebrew markdown dashboard.

    Runs only once phase1/2/3 have all completed — see graph.py for how
    the fan-in is wired. No tools are attached here, matching the skill
    file's explicit "no database tools in this phase" instruction.

    The model writes the prose and the phase 2/3 tables only. The four blocks
    that phase 1's CSVs fully determine — the trend chart, the top-10 pie, the
    sources pie and the nested item list — are computed in blocks.py and
    substituted into the reply afterwards; the model just carries their
    `{{TOKEN}}` through. See blocks.py for why that is worth the indirection.
    """
    label = "Final Synthesis"
    _log(label, f"Starting - subject: '{state['subject']}'")

    system_prompt = load_prompt(PHASE5, TODAY=state["today"], MODEL=state["model"])
    # The template's own frontmatter carries {TODAY}/{MODEL} too; without these the
    # model is left to guess them, and it has shipped a literal "model: {MODEL}".
    template = load_prompt(TEMPLATE, TODAY=state["today"], MODEL=state["model"])

    combined_data = (
        f"## Budget data (Phase 1)\n{state['budget_result']}\n\n"
        f"## Contracts & suppliers data (Phase 2)\n{state['contracts_result']}\n\n"
        f"## Government decisions data (Phase 3)\n{state['decisions_result']}\n"
        f"## Budget hierarchy (Phase 4)\n{state['hierarchy_result']}\n"
    )
    user_message = (
        f"Subject: {state['subject']}\n"
        f"Subject slug (use as the frontmatter 'path' field, under reports/): {state['subject_slug']}\n\n"
        f"{combined_data}"
    )
    _log(label, "Combined phase 1-4 results into synthesis input:")
    _log(label, f"  budget_result: {_truncate(state['budget_result'])}")
    _log(label, f"  contracts_result: {_truncate(state['contracts_result'])}")
    _log(label, f"  decisions_result: {_truncate(state['decisions_result'])}")
    _log(label, f"  hierarchy_result: {_truncate(state['hierarchy_result'])}")
    _log(label, "No tools attached for this phase (skill file: no DB tools in phase 5)")

    response = await _llm().ainvoke(
        [
            {"role": "system", "content": system_prompt + template},
            {"role": "user", "content": user_message},
        ]
    )
    _log(label, f"Model produced {len(response.content)} chars")

    computed = blocks.deterministic_blocks(state["run_dir"], state["subject"])
    report, unplaced = blocks.apply_blocks(response.content, computed)
    if unplaced:
        # The block itself is fine; the model deleted both its token and the
        # heading it lived under, so there is nowhere to put it back. Worth a
        # loud line — the section is simply missing from the page.
        _log(label, f"WARNING: no place found in the reply for: {', '.join(unplaced)}")
    _log(label, f"Done - {len(computed)} computed block(s) substituted, "
                f"{len(report)} chars")
    return {"final_report": report}
