"""
Command-line entry point.

Usage:
    python main.py "בריאות" --slug health
    python main.py "חינוך" --slug education

This builds the LangGraph pipeline, runs all four phases (three in
parallel, then synthesis), and writes the finished Hebrew markdown
dashboard to reports/<slug>.md.
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
    initial_state: WikiState = {
        "subject": subject,
        "subject_slug": slug,
        "today": today_str(),
        "model": MODEL_NAME,
        "budget_result": "",
        "contracts_result": "",
        "decisions_result": "",
        "final_report": "",
        "errors": [],
    }

    app = build_graph()
    final_state = await app.ainvoke(initial_state)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
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
