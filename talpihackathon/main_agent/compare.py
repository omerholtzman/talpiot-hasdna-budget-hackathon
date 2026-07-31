# -*- coding: utf-8 -*-
"""Compare budget-discovery runs, and score them against a ground-truth list.

    python compare.py runA/ runB/
    python compare.py runA/ --truth eval/truth_energy.csv
    python compare.py runA/ runB/ --truth eval/truth_energy.csv

For any truth code a run missed, this reports *where* it was lost - never retrieved,
dropped at program triage, or dropped at item judging - which is the difference
between "the pipeline is broken" and "one prompt needs tuning".

Works on pipeline runs and on the older agent.py runs, since both write
selected_items.csv.
"""

import argparse
import io
import os
import sys
from typing import Dict, List, Optional, Set

# Run directories and budget titles are Hebrew; a Windows console pipe defaults to
# cp1252 and would abort the whole report on the first line it prints.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from budget_api import csv_field_issues, read_csv

try:
    import json
except ImportError:  # pragma: no cover
    json = None


def _load_codes(path: str) -> Dict[str, Dict[str, str]]:
    if not os.path.exists(path):
        return {}
    return {r["code"].strip(): r for r in read_csv(path) if r.get("code", "").strip()}


def _load_run(run_dir: str) -> Dict:
    run = {
        "dir": run_dir,
        "name": os.path.basename(os.path.normpath(run_dir)),
        "selected": _load_codes(os.path.join(run_dir, "selected_items.csv")),
        "candidates": _load_codes(os.path.join(run_dir, "candidates.csv")),
        "excluded": _load_codes(os.path.join(run_dir, "excluded_items.csv")),
        "related": _load_codes(os.path.join(run_dir, "related_items.csv")),
        "programs": _load_codes(os.path.join(run_dir, "programs.csv")),
        "summary": None,
        "issues": [],
    }
    for name in ("run_summary.json", "phase1_summary.json"):
        p = os.path.join(run_dir, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                run["summary"] = json.load(f)
            break
    for fname in ("selected_items.csv", "item_budgets.csv"):
        p = os.path.join(run_dir, fname)
        if os.path.exists(p):
            with open(p, encoding="utf-8-sig") as f:
                cols, bad = csv_field_issues(f.read())
            if bad:
                run["issues"].append("%s: %d malformed row(s) at line %s"
                                     % (fname, len(bad), ", ".join(map(str, bad[:5]))))
        elif fname == "selected_items.csv":
            run["issues"].append("selected_items.csv is missing")
    return run


def _where_lost(run: Dict, code: str) -> str:
    """Attributes a miss to the step that caused it."""
    if code in run["related"]:
        return "classified as related (excluded from totals by design)"
    if code in run["excluded"]:
        reason = run["excluded"][code].get("reason", "")
        return "dropped at item judging" + (" - %s" % reason if reason else "")
    program = code[:8]
    prog_row = run["programs"].get(program)
    if prog_row and prog_row.get("verdict") == "drop":
        return 'program %s dropped at triage - "%s" (%s)' % (
            program, prog_row.get("title", ""), prog_row.get("reason", ""))
    if run["candidates"] and code not in run["candidates"]:
        return "never retrieved - not in candidates.csv (retrieval gap)"
    if not run["candidates"]:
        return "not selected (no candidates.csv - cannot attribute)"
    return "retrieved and judged but not selected"


def _fmt_code(run: Dict, code: str, titles: Optional[Dict[str, str]] = None) -> str:
    row = (run["selected"].get(code) or run["candidates"].get(code)
           or run["excluded"].get(code) or run["related"].get(code) or {})
    title = row.get("title") or (titles or {}).get(code, "")
    return "%-14s %s" % (code, title[:52])


def score(run: Dict, truth: Set[str]) -> Dict:
    got = set(run["selected"])
    hit = got & truth
    missed = truth - got
    extra = got - truth
    recall = len(hit) / len(truth) if truth else 0.0
    precision = len(hit) / len(got) if got else 0.0
    return {"hit": hit, "missed": missed, "extra": extra,
            "recall": recall, "precision": precision}


def main():
    ap = argparse.ArgumentParser(description="Compare budget-discovery runs")
    ap.add_argument("runs", nargs="+", help="One or two run directories")
    ap.add_argument("--truth", help="CSV with a `code` column of known-correct items")
    ap.add_argument("--limit", type=int, default=40, help="Max codes to list per section")
    args = ap.parse_args()

    runs = [_load_run(r) for r in args.runs[:2]]
    for r in runs:
        n_budget = os.path.join(r["dir"], "item_budgets.csv")
        budget_rows = len(read_csv(n_budget)) if os.path.exists(n_budget) else 0
        line = "%-34s selected=%-5d candidates=%-6d budget_rows=%d" % (
            r["name"][:34], len(r["selected"]), len(r["candidates"]), budget_rows)
        print(line)
        s = r["summary"] or {}
        if s.get("triage_split"):
            t = s["triage_split"]
            total = max(1, sum(t.values()))
            print("    triage: keep=%d ambiguous=%d drop=%d (ambiguous %.0f%%)"
                  % (t.get("keep", 0), t.get("ambiguous", 0), t.get("drop", 0),
                     100.0 * t.get("ambiguous", 0) / total))
        if s.get("cost"):
            c = s["cost"]
            print("    cost:   %s LLM calls, %s LLM chars, %s SQL queries"
                  % (c.get("llm_calls"), format(c.get("llm_request_chars", 0), ","),
                     c.get("sql_queries")))
        for issue in r["issues"]:
            print("    ISSUE:  %s" % issue)
    print()

    if len(runs) == 2:
        a, b = runs
        only_a = sorted(set(a["selected"]) - set(b["selected"]))
        only_b = sorted(set(b["selected"]) - set(a["selected"]))
        both = set(a["selected"]) & set(b["selected"])
        print("in both: %d" % len(both))
        print("only in %s: %d" % (a["name"][:28], len(only_a)))
        for c in only_a[:args.limit]:
            print("    %s" % _fmt_code(a, c))
            print("        %s says: %s" % (b["name"][:20], _where_lost(b, c)))
        print("only in %s: %d" % (b["name"][:28], len(only_b)))
        for c in only_b[:args.limit]:
            print("    %s" % _fmt_code(b, c))
            print("        %s says: %s" % (a["name"][:20], _where_lost(a, c)))
        print()

    if args.truth:
        truth_rows = read_csv(args.truth)
        truth = {r["code"].strip() for r in truth_rows if r.get("code", "").strip()}
        truth_titles = {r["code"].strip(): r.get("title", "") for r in truth_rows}
        print("ground truth: %d codes (%s)" % (len(truth), args.truth))
        for r in runs:
            res = score(r, truth)
            print("\n%s" % r["name"])
            print("    recall    %.2f  (%d/%d)" % (res["recall"], len(res["hit"]), len(truth)))
            print("    precision %.2f  (%d selected)" % (res["precision"], len(r["selected"])))
            if res["missed"]:
                print("    missed %d:" % len(res["missed"]))
                for c in sorted(res["missed"])[:args.limit]:
                    print("      %s" % _fmt_code(r, c, truth_titles))
                    print("          lost: %s" % _where_lost(r, c))
            if res["extra"]:
                print("    %d not in truth - review before calling these false positives:"
                      % len(res["extra"]))
                for c in sorted(res["extra"])[:args.limit]:
                    print("      %s" % _fmt_code(r, c))


if __name__ == "__main__":
    main()
