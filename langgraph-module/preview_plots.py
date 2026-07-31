"""Validate and preview ```plotly blocks in a generated report.

Extracts every fenced ```plotly block from a markdown report, parses it as
JSON (this parse *is* the validation — malformed blocks fail here instead of
silently in whatever viewer eventually renders them), and writes a
standalone HTML page rendering each chart with Plotly, using the same
plotly-basic bundle and newPlot()/layout merge described in
PLOTLY_BLOCK_SPEC.md.

Usage:
    python preview_plots.py reports/gynecology.md
    python preview_plots.py --all
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import webbrowser
from pathlib import Path

# Report paths and headings are Hebrew; a Windows console pipe defaults to
# cp1252 and would abort on the first Hebrew character it prints.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

FENCE_RE = re.compile(r"```plotly\s*\n(.*?)\n```", re.DOTALL)
HEADING_RE = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)

PLOTLY_JS_URL = "https://cdn.plot.ly/plotly-basic-2.26.1.min.js"

PAGE_TEMPLATE = """<!doctype html>
<html dir="rtl" lang="he">
<head>
<meta charset="utf-8">
<title>{title}</title>
<script src="{plotly_js_url}"></script>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }}
  h2 {{ border-bottom: 1px solid #ccc; padding-bottom: .3rem; margin-top: 2.5rem; }}
  .chart {{ direction: ltr; border: 1px solid #ddd; border-radius: 6px; padding: .5rem; }}
</style>
</head>
<body>
<h1>{title}</h1>
{charts}
<script>
const specs = {specs_json};
specs.forEach(({{ id, spec }}) => {{
  const layout = Object.assign({{ height: 600, font: {{ size: 10 }} }}, spec.layout || {{}});
  const config = Object.assign({{ responsive: true }}, spec.config || {{}});
  Plotly.newPlot(id, spec.data || spec.chart, layout, config);
}});
</script>
</body>
</html>
"""


def preceding_heading(content: str, fence_start: int) -> str | None:
    headings = [m for m in HEADING_RE.finditer(content) if m.start() < fence_start]
    if not headings:
        return None
    return headings[-1].group().lstrip("#").strip()


def extract_specs(content: str) -> list[tuple[str | None, str]]:
    """Returns a list of (heading, raw_json_text) for each ```plotly fence."""
    results = []
    for m in FENCE_RE.finditer(content):
        heading = preceding_heading(content, m.start())
        results.append((heading, m.group(1)))
    return results


def build_preview(md_path: Path) -> int:
    content = md_path.read_text(encoding="utf-8")
    raw_blocks = extract_specs(content)

    if not raw_blocks:
        print(f"{md_path}: no ```plotly blocks found")
        return 0

    specs = []
    charts_html = []
    for i, (heading, raw) in enumerate(raw_blocks):
        try:
            spec = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"{md_path}: block {i} ({heading or 'untitled'}) is not valid JSON: {e}", file=sys.stderr)
            return 1
        div_id = f"chart-{i}"
        specs.append({"id": div_id, "spec": spec})
        label = heading or f"Chart {i + 1}"
        charts_html.append(f'<h2>{label}</h2>\n<div id="{div_id}" class="chart"></div>')

    html = PAGE_TEMPLATE.format(
        title=md_path.name,
        plotly_js_url=PLOTLY_JS_URL,
        charts="\n".join(charts_html),
        specs_json=json.dumps(specs, ensure_ascii=False),
    )
    out_path = md_path.with_suffix(".preview.html")
    out_path.write_text(html, encoding="utf-8")
    print(f"{md_path}: {len(raw_blocks)} block(s) OK -> {out_path.as_uri()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="markdown report(s) to preview")
    parser.add_argument("--all", action="store_true", help="preview every reports/*.md file")
    parser.add_argument("--open", action="store_true", help="open the generated preview(s) in a browser")
    args = parser.parse_args()

    if args.all:
        paths = sorted(Path(__file__).parent.joinpath("reports").glob("*.md"))
    else:
        paths = args.paths

    if not paths:
        parser.error("pass one or more markdown files, or --all")

    exit_code = 0
    for path in paths:
        result = build_preview(path)
        exit_code = exit_code or result
        if result == 0 and args.open:
            out_path = path.with_suffix(".preview.html")
            if out_path.exists():
                webbrowser.open(out_path.as_uri())

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
