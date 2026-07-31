"""
Command-line entry point.

Usage:
    python main.py "בריאות" --slug health
    python main.py "חינוך" --slug education

This builds the LangGraph pipeline, runs phase 1 (the deterministic
budget-item pipeline), then phases 2-4 in parallel, then synthesis, and
writes the finished Hebrew markdown dashboard to reports/<slug>.md.

Phase 1's working files — the selected items, their per-year budgets, and
the audit trail of what was considered and discarded — are written to
reports/<slug>/ as CSVs. The markdown report is a summary of them; those
files are the data.
"""
import argparse
import asyncio

from agents import today_str
from config import MODEL_NAME, OUTPUT_DIR
from graph import build_graph
from state import WikiState


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a wiki-style government budget/contracts/decisions dashboard for a subject."
    )
    parser.add_argument("subject", help="Subject in Hebrew, e.g. בריאות, חינוך")
    parser.add_argument(
        "--slug",
        required=True,
        help="URL/filesystem-safe slug for the subject (e.g. 'health'). "
        "Used for the output filename and the report's frontmatter 'path' field.",
    )
    return parser.parse_args()


async def run(subject: str, slug: str) -> str:
    """Run the full pipeline for one subject and return the path to the written report."""
    # Created up front rather than inside the node: the graph is easier to reason
    # about when every node is handed the paths it needs instead of deriving them.
    run_dir = OUTPUT_DIR / slug
    run_dir.mkdir(parents=True, exist_ok=True)

    initial_state: WikiState = {
        "subject": subject,
        "subject_slug": slug,
        "today": today_str(),
        "model": MODEL_NAME,
        "run_dir": str(run_dir),
        "budget_result": "",
        "contracts_result": "",
        "decisions_result": "",
        "hierarchy_result": "",
        "final_report": "",
        "errors": [],
    }

    print(f"=== Starting pipeline for subject '{subject}' (slug: {slug}) ===")
    print("Stages: Phase 1 (Budget, deterministic) -> Phase 2 (Contracts), "
          "Phase 3 (Decisions), Phase 4 (Hierarchy) in parallel -> Final Synthesis")
    print(f"Phase 1 data files: {run_dir}")

    app = build_graph()
    final_state = await app.ainvoke(initial_state)

    print("=== All stages complete ===")

    output_path = OUTPUT_DIR / f"{slug}.md"
    output_path.write_text(final_state["final_report"], encoding="utf-8")

    if final_state["errors"]:
        print("Completed with some phase-level errors (their sections may be thin or empty):")
        for err in final_state["errors"]:
            print(f"  - {err}")

    print(f"Report written to {output_path}")
    return str(output_path)


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run(args.subject, args.slug))
