"""
Batch runner for main.py, parallel to talpihackathon/main_agent/orchestrator.py.

That orchestrator drives agent.py (which slugifies its own `--subject` and
accepts `--provider`/`--output`/`--mcp-url`) as a subprocess per configured
subject, tracks state so a run can resume, and syncs "related reports" links
across a category once everything's done.

main.py is the same shape of worker but a different interface: it takes the
slug as its own required `--slug` flag rather than deriving one, has no
`--provider` (this pipeline only ever talks to Gemini via Vertex - see
config.py), and always writes to `reports/<slug>.md` + `reports/<slug>/`
rather than an orchestrator-supplied `--output` path. So this file keeps the
same shape - JSON config of category -> subjects, a state file for resuming,
one subprocess per subject, dry-run, category/subject filters, related-report
link sync - but adapts each of those points to main.py's actual CLI and fixed
output layout instead of copying agent.py's.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Subjects and titles are Hebrew; Windows consoles default to cp1252 and raise
# UnicodeEncodeError on them mid-run. Same fix as main_agent/agent.py.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_PATH = os.path.join(SCRIPT_DIR, "main.py")
DEFAULT_CONFIG_PATH = os.path.join(SCRIPT_DIR, "orchestrator-config.json")
DEFAULT_STATE_PATH = os.path.join(SCRIPT_DIR, "orchestrator-state.json")
DEFAULT_REPORTS_DIR = os.path.join(SCRIPT_DIR, "reports")  # matches config.py's OUTPUT_DIR
DEFAULT_TIMEOUT_SECONDS = 1200


def slugify(subject: str) -> str:
    """Fallback only: langgraph-module's existing reports (gynecology, tipathalav, ...)
    are hand-picked English slugs, not mechanically derived from the Hebrew subject like
    agent.py's are. Config entries should set "slug" explicitly; this only covers an
    entry that omits it."""
    return re.sub(r"[^\w\-_]", "_", subject).strip("_") or "subject"


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
TITLE_RE = re.compile(r"^title:\s*(.+?)\s*$", re.MULTILINE)

RELATED_START = "<!-- orchestrator:related-reports:start -->"
RELATED_END = "<!-- orchestrator:related-reports:end -->"
RELATED_BLOCK_RE = re.compile(re.escape(RELATED_START) + r".*?" + re.escape(RELATED_END) + r"\n?", re.DOTALL)


def parse_frontmatter_title(md_path: str) -> Optional[str]:
    with open(md_path, "r", encoding="utf-8") as f:
        head = f.read(4000)
    frontmatter_match = FRONTMATTER_RE.match(head)
    if not frontmatter_match:
        return None
    title_match = TITLE_RE.search(frontmatter_match.group(1))
    if not title_match:
        return None
    return title_match.group(1).strip().strip('"').strip("'")


def discover_category_reports(category: str, entries: List[Dict[str, str]], reports_dir: str) -> List[Dict[str, str]]:
    """Finds every successfully-produced report (reports/{slug}.md) for a category's
    configured slugs. Unlike main_agent's version, there's no category subdirectory to
    list - reports/ is flat - so membership comes from the config, not the filesystem."""
    reports: List[Dict[str, str]] = []
    for entry in entries:
        slug = entry["slug"]
        md_path = os.path.join(reports_dir, f"{slug}.md")
        if os.path.exists(md_path) and os.path.getsize(md_path) > 0:
            title = parse_frontmatter_title(md_path) or entry["subject"]
            reports.append({"slug": slug, "title": title, "md_path": md_path})
    return reports


def build_related_block(current_slug: str, reports: List[Dict[str, str]]) -> Optional[str]:
    others = [r for r in reports if r["slug"] != current_slug]
    if not others:
        return None
    lines = [RELATED_START, "", "## דוחות קשורים", ""]
    for r in others:
        # Flat reports/ dir, so siblings are same-directory links, not "../{slug}/{slug}.md".
        rel_link = os.path.basename(r["md_path"])
        lines.append(f"- [{r['title']}]({rel_link})")
    lines.append("")
    lines.append(RELATED_END)
    return "\n".join(lines)


def sync_related_links(category: str, entries: List[Dict[str, str]], reports_dir: str) -> None:
    """Rewrites the cross-link section in every report of a category so they all
    link to their siblings. Idempotent: replaces any previously-injected block."""
    reports = discover_category_reports(category, entries, reports_dir)
    for r in reports:
        with open(r["md_path"], "r", encoding="utf-8") as f:
            content = f.read()
        stripped = RELATED_BLOCK_RE.sub("", content).rstrip()
        block = build_related_block(r["slug"], reports)
        new_content = f"{stripped}\n\n{block}\n" if block else f"{stripped}\n"
        if new_content != content:
            with open(r["md_path"], "w", encoding="utf-8") as f:
                f.write(new_content)


def load_config(config_path: str) -> Dict[str, List[Dict[str, str]]]:
    """Config shape: {category: [{"subject": "...", "slug": "..."}, ...]}.
    A bare string entry is accepted too, with its slug derived via slugify() -
    but langgraph-module's convention is curated slugs, so prefer the explicit form."""
    with open(config_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"Config at {config_path} must be a JSON object of category -> [subjects]")

    config: Dict[str, List[Dict[str, str]]] = {}
    for category, entries in raw.items():
        if not isinstance(entries, list):
            raise ValueError(f"Config category '{category}' must map to a list of subjects")
        normalized = []
        for entry in entries:
            if isinstance(entry, str):
                normalized.append({"subject": entry, "slug": slugify(entry)})
            elif isinstance(entry, dict) and "subject" in entry:
                slug = entry.get("slug") or slugify(entry["subject"])
                normalized.append({"subject": entry["subject"], "slug": slug})
            else:
                raise ValueError(
                    f"Config category '{category}' has an invalid entry: {entry!r} "
                    '(expected a string or {"subject": ..., "slug": ...})'
                )
        config[category] = normalized
    return config


def load_state(state_path: str) -> Dict[str, Any]:
    if not os.path.exists(state_path):
        return {}
    with open(state_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state_path: str, state: Dict[str, Any]) -> None:
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def build_run_list(
    config: Dict[str, List[Dict[str, str]]],
    category_filter: Optional[str],
    subject_filter: Optional[str],
) -> List[Tuple[str, Dict[str, str]]]:
    runs: List[Tuple[str, Dict[str, str]]] = []
    for category, entries in config.items():
        if category_filter and category != category_filter:
            continue
        for entry in entries:
            if subject_filter and subject_filter not in (entry["subject"], entry["slug"]):
                continue
            runs.append((category, entry))
    return runs


def run_subject(
    category: str,
    entry: Dict[str, str],
    reports_dir: str,
    model: Optional[str],
    mcp_url: Optional[str],
    timeout: int,
) -> Dict[str, Any]:
    subject, slug = entry["subject"], entry["slug"]
    output_path = os.path.join(reports_dir, f"{slug}.md")
    run_dir = os.path.join(reports_dir, slug)  # same dir main.py itself writes phase-1 CSVs into
    log_path = os.path.join(run_dir, "run.log")
    os.makedirs(run_dir, exist_ok=True)

    cmd = [sys.executable, MAIN_PATH, subject, "--slug", slug]

    # main.py has no --model/--mcp-url flags of its own; config.py reads these from
    # the environment, so that's the only way an orchestrator run can override them.
    env = os.environ.copy()
    if model:
        env["WIKI_HARNESS_GEMINI_MODEL"] = model
    if mcp_url:
        env["WIKI_HARNESS_MCP_URL"] = mcp_url

    result: Dict[str, Any] = {
        "category": category,
        "subject": subject,
        "slug": slug,
        "status": "error",
        "timestamp": datetime.now().isoformat(),
        "output_path": output_path,
        "log_path": log_path,
        "error": None,
    }

    try:
        proc = subprocess.run(
            cmd,
            cwd=SCRIPT_DIR,
            env=env,
            timeout=timeout,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(proc.stdout or "")
            f.write(proc.stderr or "")

        output_ready = os.path.exists(output_path) and os.path.getsize(output_path) > 0
        if proc.returncode == 0 and output_ready:
            result["status"] = "success"
        else:
            result["status"] = "failed"
            reason = f"exit code {proc.returncode}"
            if not output_ready:
                reason += ", output file missing or empty"
            result["error"] = reason
    except subprocess.TimeoutExpired as e:
        result["status"] = "timeout"
        result["error"] = f"exceeded {timeout}s timeout"
        with open(log_path, "w", encoding="utf-8") as f:
            f.write((e.stdout or "") if isinstance(e.stdout, str) else "")
            f.write((e.stderr or "") if isinstance(e.stderr, str) else "")
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Runs main.py once per configured subject and writes results under reports/{slug}.md + reports/{slug}/"
    )
    parser.add_argument("--config", type=str, default=DEFAULT_CONFIG_PATH, help="Path to orchestrator-config.json")
    parser.add_argument("--state", type=str, default=DEFAULT_STATE_PATH, help="Path to the orchestrator state file")
    parser.add_argument("--model", type=str, default=None, help="Overrides WIKI_HARNESS_GEMINI_MODEL for every run")
    parser.add_argument("--mcp-url", type=str, default=None, help="Overrides WIKI_HARNESS_MCP_URL for every run")
    parser.add_argument("--reports-dir", type=str, default=DEFAULT_REPORTS_DIR, help="Root directory for reports (must match config.py's OUTPUT_DIR)")
    parser.add_argument("--category", type=str, default=None, help="Only run subjects in this category")
    parser.add_argument("--subject", type=str, default=None, help="Only run this subject (matches subject text or slug; use with --category)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="Per-subject subprocess timeout in seconds")
    parser.add_argument("--dry-run", action="store_true", help="Print the planned run list and exit without launching main.py")
    args = parser.parse_args()

    config = load_config(args.config)
    runs = build_run_list(config, args.category, args.subject)

    if not runs:
        print("[Orchestrator] No subjects matched the given filters. Nothing to do.", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print(f"[Orchestrator] Dry run: {len(runs)} subject(s) would be processed:")
        for category, entry in runs:
            output_path = os.path.join(args.reports_dir, f"{entry['slug']}.md")
            print(f"  - [{category}] {entry['subject']!r} (slug: {entry['slug']}) -> {output_path}")
        return

    state = load_state(args.state)

    succeeded = 0
    failed = 0
    for category, entry in runs:
        key = f"{category}/{entry['slug']}"
        print(f"[Orchestrator] [{key}] running...")
        result = run_subject(category, entry, args.reports_dir, args.model, args.mcp_url, args.timeout)
        state[key] = result
        save_state(args.state, state)

        if result["status"] == "success":
            succeeded += 1
            print(f"[Orchestrator] [{key}] SUCCESS")
        else:
            failed += 1
            print(f"[Orchestrator] [{key}] {result['status'].upper()}: {result['error']}", file=sys.stderr)

    touched_categories = sorted({category for category, _ in runs})
    for category in touched_categories:
        sync_related_links(category, config[category], args.reports_dir)
    print(f"[Orchestrator] Synced related-report links for: {', '.join(touched_categories)}")

    print(f"[Orchestrator] Done: {succeeded} succeeded, {failed} failed out of {len(runs)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
