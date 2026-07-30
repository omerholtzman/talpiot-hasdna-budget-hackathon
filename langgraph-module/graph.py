"""
Wires the four phases into a single LangGraph pipeline.

                  ┌───────────────────┐
            ┌────►│ phase1_budget     ├────┐
            │     └───────────────────┘    │
      START ├────►┌───────────────────┐    ▼
            │     │ phase2_contracts  ├──► phase4_synthesis ──► END
            │     └───────────────────┘    ▲
            └────►┌───────────────────┐    │
                  │ phase3_decisions  ├────┘
                  └───────────────────┘

Phases 1-3 have no dependency on each other, so they all fan out directly
from START and run concurrently. LangGraph only runs phase4_synthesis
once ALL THREE of its incoming edges have fired (its default behavior for
a node with multiple predecessors) — that's the fan-in, and it's free:
no manual "wait for every phase to finish" bookkeeping required on our end.
"""
from langgraph.graph import END, START, StateGraph

from agents import (
    phase1_budget_node,
    phase2_contracts_node,
    phase3_decisions_node,
    phase4_hierarchy_node,
    final_phase_synthesis_node,
)
from state import WikiState


def build_graph():
    """Assemble and compile the pipeline. Call this once per run (or cache the compiled app)."""
    graph = StateGraph(WikiState)

    graph.add_node("phase1_budget", phase1_budget_node)
    graph.add_node("phase2_contracts", phase2_contracts_node)
    graph.add_node("phase3_decisions", phase3_decisions_node)
    graph.add_node("phase4_hierarchy", phase4_hierarchy_node)
    graph.add_node("final_phase_synthesis", final_phase_synthesis_node)

    # Fan-out: all three research phases start immediately, in parallel.
    graph.add_edge(START, "phase1_budget")
    graph.add_edge("phase1_budget", "phase2_contracts")
    graph.add_edge("phase1_budget", "phase3_decisions")
    graph.add_edge("phase1_budget", "phase4_hierarchy")

    # Fan-in: synthesis waits for all three research phases to finish.
    graph.add_edge("phase2_contracts", "final_phase_synthesis")
    graph.add_edge("phase3_decisions", "final_phase_synthesis")
    graph.add_edge("phase4_hierarchy", "final_phase_synthesis")

    graph.add_edge("final_phase_synthesis", END)

    return graph.compile()
