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
from datetime import date

from langchain_google_genai import ChatGoogleGenerativeAI

from langgraph.prebuilt import create_react_agent

from config import AGENT_MAX_STEPS, ANTHROPIC_API_KEY, MODEL_NAME
from mcp_tools import get_mcp_tools
from prompt_loader import load_prompt
from state import WikiState


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
    system_prompt = load_prompt(phase_name, TODAY=state["today"])
    tools = await get_mcp_tools()

    # NOTE: `create_react_agent`'s system-prompt argument name has changed
    # across langgraph versions (`prompt=`, `state_modifier=`,
    # `messages_modifier=` all exist in the wild). Check this against your
    # installed version if you hit a TypeError here.
    agent = create_react_agent(_llm(), tools=tools, prompt=system_prompt)

    user_message = f"Research the subject: {state['subject']}"
    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": user_message}]},
            config={"recursion_limit": AGENT_MAX_STEPS},
        )
        final_text = result["messages"][-1].content
        return final_text, []
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any phase may fail independently
        placeholder = f"_No data could be retrieved for this section ({exc})._"
        return placeholder, [f"{phase_name} failed: {exc}"]


# --- One thin LangGraph node per phase --------------------------------------------
# Each just calls the shared helper above and shapes the result into the
# partial-state-update dict LangGraph expects back from a node.

async def phase1_budget_node(state: WikiState) -> dict:
    """LangGraph node: aggregate state budget data (amount_allocated/revised/used by year)."""
    text, errors = await _run_research_phase("phase1_budget", state)
    return {"budget_result": text, "errors": errors}


async def phase2_contracts_node(state: WikiState) -> dict:
    """LangGraph node: top contracts by volume + supplier totals for the subject."""
    text, errors = await _run_research_phase("phase2_contracts", state)
    return {"contracts_result": text, "errors": errors}


async def phase3_decisions_node(state: WikiState) -> dict:
    """LangGraph node: government decisions/resolutions mentioning the subject."""
    text, errors = await _run_research_phase("phase3_decisions", state)
    return {"decisions_result": text, "errors": errors}


async def phase4_hierarchy_node(state: WikiState) -> dict:
    """LangGraph node: government decisions/resolutions mentioning the subject."""
    text, errors = await _run_research_phase("phase4_hierarchy", state)
    return {"hierarchy_result": text, "errors": errors}


async def final_phase_synthesis_node(state: WikiState) -> dict:
    """
    LangGraph node: combine phases 1-3 into the final Hebrew markdown dashboard.

    Runs only once phase1/2/3 have all completed — see graph.py for how
    the fan-in is wired. No tools are attached here, matching the skill
    file's explicit "no database tools in this phase" instruction.
    """
    system_prompt = load_prompt("final_phase_synthesis", TODAY=state["today"], MODEL=state["model"])

    combined_data = (
        f"## Budget data (Phase 1)\n{state['budget_result']}\n\n"
        f"## Contracts & suppliers data (Phase 2)\n{state['contracts_result']}\n\n"
        f"## Government decisions data (Phase 3)\n{state['decisions_result']}\n"
    )
    user_message = (
        f"Subject: {state['subject']}\n"
        f"Subject slug (use as the frontmatter 'path' field, under reports/): {state['subject_slug']}\n\n"
        f"{combined_data}"
    )

    response = await _llm().ainvoke(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
    )
    return {"final_report": response.content}
