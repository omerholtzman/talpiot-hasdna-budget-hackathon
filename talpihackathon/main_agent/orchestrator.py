import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_PATH = os.path.join(SCRIPT_DIR, "agent.py")
DEFAULT_CONFIG_PATH = os.path.join(SCRIPT_DIR, "orchestrator-config.json")
DEFAULT_STATE_PATH = os.path.join(SCRIPT_DIR, "orchestrator-state.json")
DEFAULT_OUTPUT_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "structured_report"))
DEFAULT_TIMEOUT_SECONDS = 1200


def slugify(subject: str) -> str:
    """Matches agent.py's own subject -> filename slug rule (get_default_output_path)."""
    return re.sub(r"[^\w\-_]", "_", subject)


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


def discover_category_reports(category_dir: str) -> List[Dict[str, str]]:
    """Finds every successfully-produced report ({slug}/{slug}.md) under a category directory."""
    reports: List[Dict[str, str]] = []
    if not os.path.isdir(category_dir):
        return reports
    for entry in sorted(os.listdir(category_dir)):
        subject_dir = os.path.join(category_dir, entry)
        md_path = os.path.join(subject_dir, f"{entry}.md")
        if os.path.isdir(subject_dir) and os.path.exists(md_path) and os.path.getsize(md_path) > 0:
            title = parse_frontmatter_title(md_path) or entry
            reports.append({"slug": entry, "title": title, "md_path": md_path})
    return reports


def build_related_block(current_slug: str, reports: List[Dict[str, str]]) -> Optional[str]:
    others = [r for r in reports if r["slug"] != current_slug]
    if not others:
        return None
    lines = [RELATED_START, "", "## דוחות קשורים", ""]
    for r in others:
        rel_link = f"../{r['slug']}/{os.path.basename(r['md_path'])}"
        lines.append(f"- [{r['title']}]({rel_link})")
    lines.append("")
    lines.append(RELATED_END)
    return "\n".join(lines)


def sync_related_links(category: str, output_root: str) -> None:
    """Rewrites the cross-link section in every report of a category so they all
    link to their siblings. Idempotent: replaces any previously-injected block."""
    category_dir = os.path.join(output_root, category)
    reports = discover_category_reports(category_dir)
    for r in reports:
        with open(r["md_path"], "r", encoding="utf-8") as f:
            content = f.read()
        stripped = RELATED_BLOCK_RE.sub("", content).rstrip()
        block = build_related_block(r["slug"], reports)
        new_content = f"{stripped}\n\n{block}\n" if block else f"{stripped}\n"
        if new_content != content:
            with open(r["md_path"], "w", encoding="utf-8") as f:
                f.write(new_content)


def load_config(config_path: str) -> Dict[str, List[str]]:
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    if not isinstance(config, dict):
        raise ValueError(f"Config at {config_path} must be a JSON object of category -> [subjects]")
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
    config: Dict[str, List[str]],
    category_filter: Optional[str],
    subject_filter: Optional[str],
) -> List[Tuple[str, str]]:
    runs: List[Tuple[str, str]] = []
    for category, subjects in config.items():
        if category_filter and category != category_filter:
            continue
        for subject in subjects:
            if subject_filter and subject != subject_filter:
                continue
            runs.append((category, subject))
    return runs


def run_subject(
    category: str,
    subject: str,
    output_root: str,
    provider: str,
    model: Optional[str],
    mcp_url: Optional[str],
    timeout: int,
) -> Dict[str, Any]:
    slug = slugify(subject)
    output_dir = os.path.join(output_root, category, slug)
    output_path = os.path.join(output_dir, f"{slug}.md")
    log_path = os.path.join(output_dir, "run.log")
    os.makedirs(output_dir, exist_ok=True)

    cmd = [
        sys.executable,
        AGENT_PATH,
        "--subject",
        subject,
        "--provider",
        provider,
        "--output",
        output_path,
    ]
    if model:
        cmd.extend(["--model", model])
    if mcp_url:
        cmd.extend(["--mcp-url", mcp_url])

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
        description="Runs agent.py once per configured subject and writes results under structured_report/{category}/{subject}/"
    )
    parser.add_argument("--config", type=str, default=DEFAULT_CONFIG_PATH, help="Path to orchestrator-config.json")
    parser.add_argument("--state", type=str, default=DEFAULT_STATE_PATH, help="Path to the orchestrator state file")
    parser.add_argument(
        "--provider", type=str, default="vertex", choices=["gemini", "anthropic", "vertex", "cli-claude"]
    )
    parser.add_argument("--model", type=str, default=None, help="Model name override, passed through to agent.py")
    parser.add_argument("--mcp-url", type=str, default=None, help="BudgetKey MCP server URL, passed through to agent.py")
    parser.add_argument("--output-root", type=str, default=DEFAULT_OUTPUT_ROOT, help="Root directory for structured reports")
    parser.add_argument("--category", type=str, default=None, help="Only run subjects in this category")
    parser.add_argument("--subject", type=str, default=None, help="Only run this subject (use with --category)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="Per-subject subprocess timeout in seconds")
    parser.add_argument("--dry-run", action="store_true", help="Print the planned run list and exit without launching agent.py")
    args = parser.parse_args()

    config = load_config(args.config)
    runs = build_run_list(config, args.category, args.subject)

    if not runs:
        print("[Orchestrator] No subjects matched the given filters. Nothing to do.", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print(f"[Orchestrator] Dry run: {len(runs)} subject(s) would be processed:")
        for category, subject in runs:
            slug = slugify(subject)
            output_path = os.path.join(args.output_root, category, slug, f"{slug}.md")
            print(f"  - [{category}] {subject!r} -> {output_path}")
        return

    state = load_state(args.state)

    succeeded = 0
    failed = 0
    for category, subject in runs:
        key = f"{category}/{slugify(subject)}"
        print(f"[Orchestrator] [{key}] running...")
        result = run_subject(
            category, subject, args.output_root, args.provider, args.model, args.mcp_url, args.timeout
        )
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
        sync_related_links(category, args.output_root)
    print(f"[Orchestrator] Synced related-report links for: {', '.join(touched_categories)}")

    print(f"[Orchestrator] Done: {succeeded} succeeded, {failed} failed out of {len(runs)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
