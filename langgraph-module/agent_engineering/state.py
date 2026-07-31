"""
The shared state that flows through the LangGraph pipeline.

Every node reads from this TypedDict and returns a partial dict of
updates to it — nodes never mutate the state object they're given
in-place; LangGraph merges whatever they return into the shared state.

Why `errors` needs an `Annotated[..., operator.add]` reducer, but the
`*_result` fields don't:

    Phases 2, 3 and 4 run CONCURRENTLY (see graph.py). If two of them
    finished in the same "superstep" and both tried to write to the same
    plain field, LangGraph wouldn't know whether to keep the first
    value, the second, or raise an error — so it raises one, by design.
    That's fine for `contracts_result` / `decisions_result` /
    `hierarchy_result`, because only ONE node ever writes to each of
    those keys. But `errors` is a key that phases 2, 3 AND 4 might all
    want to append to at once. The `operator.add` reducer tells
    LangGraph "when multiple nodes write to this key in the same step,
    concatenate the lists" — turning a potential crash into the
    behavior we actually want.
"""
import operator
from typing import Annotated, TypedDict


class WikiState(TypedDict):
    # --- inputs, set once before the graph runs ---
    subject: str          # the Hebrew subject to research, e.g. "בריאות"
    subject_slug: str     # filesystem/URL-safe slug, e.g. "health"
    today: str            # ISO date (YYYY-MM-DD), injected into every prompt
    model: str            # model name recorded in the report's frontmatter
    run_dir: str          # directory phase 1 writes its CSVs into; phase 4 reads them back
    skip_phase1: bool     # reuse existing phase-1 CSVs instead of running the pipeline
    stop_after_research: bool  # skip the final synthesis model call after phases 2-4

    # --- filled in by phases 1-3 (run in parallel, one writer each) ---
    budget_result: str
    contracts_result: str
    decisions_result: str
    hierarchy_result: str

    # --- filled in by phase 4 ---
    final_report: str

    # --- diagnostics; multiple phases may write to this concurrently ---
    errors: Annotated[list[str], operator.add]
