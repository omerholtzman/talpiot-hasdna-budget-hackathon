"""
The actual "workers" behind each of the four LangGraph nodes.

Phases 1-3 are classic ReAct-style tool-calling agents: each is handed a
system prompt (loaded from prompts/skill_phase*.md) plus the MCP tools,
and loops model -> tool call -> model until it produces a final answer.
LangGraph's prebuilt `create_react_agent` implements that loop for us, so
this module stays focused on *wiring things together*, not on
re-implementing an agent loop from scratch.

Phase 4 is deliberately simpler: its skill file says "No database tools
are available in this phase" — so it's just one call to the model with
the three research results pasted in as context, no tool loop at all.
"""
from datetime import date, datetime

from langchain_google_genai import ChatGoogleGenerativeAI

from langgraph.prebuilt import create_react_agent

from config import AGENT_MAX_STEPS
from constants import PHASE1, PHASE2, PHASE3, PHASE4, PHASE5, TEMPLATE

from mcp_tools import get_mcp_tools
from prompt_loader import load_prompt
from state import WikiState


# --- Verbose stage logging ---------------------------------------------------------
# Phases 1-4 run concurrently, so every log line is tagged with a phase label
# to keep the interleaved output readable. This is plain stdout logging (not
# the `logging` module) to keep the change minimal and dependency-free.

PHASE_LABELS = {
    PHASE1: "Phase 1: Budget",
    PHASE2: "Phase 2: Contracts",
    PHASE3: "Phase 3: Decisions",
    PHASE4: "Phase 4: Hierarchy",
}


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
        model="gemini-2.5-flash",   # Vertex model IDs often use an @date suffix, e.g. claude-haiku-4-5@20251001
        project="qwiklabs-gcp-01-1436437a2cf1",
        location="europe-southwest1",              # Claude's Vertex region, not necessarily your default GCP region
    )

def today_str() -> str:
    """Today's date as YYYY-MM-DD, used to seed state['today'] in main.py."""
    return date.today().isoformat()


async def _run_research_phase(phase_name: str, state: WikiState) -> tuple[str, list[str]]:
    """
    Shared implementation for phases 1-3: build the system prompt, attach
    the MCP tools, run the ReAct loop, and return (result_text, errors).

    Any failure (bad SQL, a tool erroring out, the model giving up, etc.)
    is caught HERE rather than allowed to crash the whole pipeline. That
    matters because phases 1, 2, and 3 run concurrently: a failure in the
    "contracts" phase shouldn't take down the "budget" and "decisions"
    phases too. A failed phase becomes a short placeholder string instead,
    and phase 4 (synthesis) will simply note that section had no data.
    """
    label = PHASE_LABELS.get(phase_name, phase_name)
    _log(label, f"Starting - subject: '{state['subject']}'")

    system_prompt = load_prompt(phase_name, TODAY=state["today"])
    tools = await get_mcp_tools()
    _log(label, f"Loaded {len(tools)} MCP tool(s): {', '.join(t.name for t in tools)}")

    agent = create_react_agent(_llm(), tools=tools, prompt=system_prompt)

    user_message = f"Research the subject: {state['subject']}"
    _log(label, f"Sending initial message: {user_message}")
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
    """LangGraph node: aggregate state budget data (amount_allocated/revised/used by year)."""
    text, errors = await _run_research_phase(PHASE1, state)
    return {"budget_result": text, "errors": errors}


async def phase2_contracts_node(state: WikiState) -> dict:
    """LangGraph node: top contracts by volume + supplier totals for the subject."""
    text, errors = await _run_research_phase(PHASE2, state)
    return {"contracts_result": text, "errors": errors}


async def phase3_decisions_node(state: WikiState) -> dict:
    """LangGraph node: government decisions/resolutions mentioning the subject."""
    text, errors = await _run_research_phase(PHASE3, state)
    return {"decisions_result": text, "errors": errors}


async def phase4_hierarchy_node(state: WikiState) -> dict:
    """LangGraph node: ..."""
    text, errors = await _run_research_phase(PHASE4, state)
    return {"hierarchy_result": text, "errors": errors}


async def final_phase_synthesis_node(state: WikiState) -> dict:
    """
    LangGraph node: combine phases 1-3 into the final Hebrew markdown dashboard.

    Runs only once phase1/2/3 have all completed — see graph.py for how
    the fan-in is wired. No tools are attached here, matching the skill
    file's explicit "no database tools in this phase" instruction.
    """
    label = "Final Synthesis"
    _log(label, f"Starting - subject: '{state['subject']}'")

    system_prompt = load_prompt(PHASE5, TODAY=state["today"], MODEL=state["model"])
    template = load_prompt(TEMPLATE)

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
    _log(label, f"Done - produced {len(response.content)} chars")
    return {"final_report": response.content}
