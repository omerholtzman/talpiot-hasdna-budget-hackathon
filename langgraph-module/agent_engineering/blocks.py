# -*- coding: utf-8 -*-
"""Deterministic rendering of the synthesis template's data-driven blocks.

Phase 1 already writes every number these blocks need — `selected_items.csv`,
`item_budgets.csv` and `hierarchy.csv` are the data, and the markdown report is
only a view of them. Until now the synthesis model was asked to read a text
digest of those CSVs and hand-write the Plotly JSON and the selected-item list
from it. That is a transcription job, and the model failed it in both
directions: it silently dropped whole charts (`reports/GreenEnergy.md` renders
"לא נמצא מידע" for the trend and top-10 sections even though phase 1 found the
items), and when it did emit numbers they were re-typed rather than summed.

So the four blocks below are computed here instead. The model never sees the
JSON — the template carries a `{{TOKEN}}` where each block goes, and
`apply_blocks()` substitutes the real content into the model's reply after the
fact. The model's remaining job is prose and the phase 2/3 tables, which are
genuinely its work.

One correctness rule the digest did not encode: `selected_items.counts_in_total`
marks reserves, internal transfers and earmarked revenue, which are real
findings but double-count if summed alongside ordinary lines. Every total below
is computed over `counts_in_total == "yes"` only; the excluded lines still
appear in the appendix table, flagged individually or counted in the note of
the row that stands in for them.
"""
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import helpers.prompts.budget_reference as ref
from helpers.prompts.budget_api import read_csv

# Token names as they appear in prompts/synthesis_template.md, written {{LIKE_THIS}}
# to keep them visually distinct from the {LIKE_THIS} placeholders the model fills.
TREND_CHART = "TREND_CHART"
TOP_ITEMS_CHART = "TOP_ITEMS_CHART"
SOURCES_CHART = "SOURCES_CHART"
HIERARCHY_LIST = "BUDGET_HIERARCHY_LIST"

# Where each block belongs, used only to repair a reply that dropped its token.
TOKEN_HEADINGS = {
    TREND_CHART: "## מגמה תקציבית לאורך זמן",
    TOP_ITEMS_CHART: "### סעיפים בולטים",
    SOURCES_CHART: "## מקורות תקציב",
    HIERARCHY_LIST: "## נספח: סעיפי התקציב הנבחרים",
}

NO_DATA = "לא נמצא מידע רלוונטי לנושא %s."

# The three series obudget itself plots for a budget line, in the order they are
# stacked on the chart: what was approved, what it became after in-year changes,
# and what was actually spent.
SERIES = [
    ("amount_allocated", "תקציב מקורי"),
    ("amount_revised", "תקציב אחרי שינויים"),
    ("amount_used", "ביצוע בפועל"),
]

# When the selected lines under a node reach this share of its own budget, the
# node *is* the subject and its children are noise. Not 1.0: the two sides come
# from different queries (item_budgets.csv per line, hierarchy.csv per node) and
# a rounded shekel should not keep 30 rows alive.
FULLY_COVERED = 0.995

# A row that stands in for a single line is strictly worse than that line — it
# costs the same space and loses the name and the link. So collapsing needs two.
COLLAPSE_MIN_LINES = 2


# --- small shared helpers -----------------------------------------------------

def _num(value: Any) -> Optional[float]:
    """A CSV cell as a float, or None for an empty/unparseable one.

    None and 0 are different here: a NULL `amount_used` means "not yet executed"
    and must leave a gap in the line, while a real 0 is a datapoint.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _read(run_dir: str, name: str) -> List[Dict[str, str]]:
    path = os.path.join(run_dir, name)
    return read_csv(path) if os.path.exists(path) else []


def _plotly(spec: Dict[str, Any]) -> str:
    """A ```plotly fence, per PLOTLY_BLOCK_SPEC.md.

    json.dumps handles the escaping the spec calls out (`ע"ר` -> `ע\\"ר`) and
    guarantees bare numerals, which is most of what the model kept getting wrong.
    Arrays of scalars are folded back onto one line afterwards: a 30-year trend
    is 120 lines of one-number-per-line otherwise, and the fence sits in a file
    people read and diff.
    """
    body = json.dumps(spec, ensure_ascii=False, indent=2)
    body = re.sub(r"\[[^\[\]{}]*\]",
                  lambda m: " ".join(m.group(0).split()), body)
    return "```plotly\n%s\n```" % body


def _shekels(amount: float) -> str:
    return "{:,.0f} ₪".format(amount)


def _link(label: str, url: str) -> str:
    label = " ".join(str(label).split())
    if not url:
        return label
    return "[%s](%s)" % (label, url)


def _cell(text: str) -> str:
    """A value safe to drop into a markdown table cell.

    A pipe in a budget title (they do occur, in titles that list alternatives)
    silently shifts every column to its right for that one row.
    """
    return " ".join(str(text).split()).replace("|", "\\|") or "—"


def _counted(items: List[Dict[str, str]]) -> set:
    """Codes that may be summed — everything but reserves/transfers/earmarked revenue."""
    return {r["code"] for r in items if r.get("counts_in_total", "yes") != "no"}


def _yearly(budgets: List[Dict[str, str]], codes: set) -> Dict[str, Dict[str, List]]:
    """{year: {field: [total, non_null_count]}} over the given codes."""
    per_year: Dict[str, Dict[str, List]] = {}
    for row in budgets:
        if row.get("code") not in codes:
            continue
        year = (row.get("year") or "").strip()
        if not year:
            continue
        bucket = per_year.setdefault(year, {field: [0.0, 0] for field, _ in SERIES})
        for field, _ in SERIES:
            value = _num(row.get(field))
            if value is not None:
                bucket[field][0] += value
                bucket[field][1] += 1
    return per_year


def _alloc_by_code(budgets: List[Dict[str, str]], codes: set,
                   year: Optional[str] = None) -> Dict[str, float]:
    """{code: amount_allocated}, for one year or summed over all of them.

    Codes with no budget in the requested scope are absent, not zero — the
    callers all want "which lines are funded", and a zero slice or a zero row is
    noise either way.
    """
    out: Dict[str, float] = {}
    for row in budgets:
        code = row.get("code")
        if code not in codes:
            continue
        if year is not None and (row.get("year") or "").strip() != year:
            continue
        amount = _num(row.get("amount_allocated"))
        if amount:
            out[code] = out.get(code, 0.0) + amount
    return out


def _last_funded(budgets: List[Dict[str, str]], codes: set) -> Dict[str, Tuple[str, float]]:
    """{code: (year, amount_allocated)} for the most recent year each line was funded.

    A subject's selected items are rarely all live at once — 87 lines were
    selected for נוער מחונן and 4 of them are in the 2026 book. Without this the
    other 83 render as a wall of names with no number against them, which reads
    as missing data rather than as "this programme ended in 2014".
    """
    out: Dict[str, Tuple[str, float]] = {}
    for row in budgets:
        code = row.get("code")
        if code not in codes:
            continue
        year = (row.get("year") or "").strip()
        amount = _num(row.get("amount_allocated"))
        if not year or not amount:
            continue
        if code not in out or year > out[code][0]:
            out[code] = (year, amount)
    return out


def _current_year(per_year: Dict[str, Dict[str, List]]) -> Optional[str]:
    """The latest year in which the subject actually has an approved budget.

    Not simply max(years): phase 1 materialises every year a code exists, so the
    last one is often a future year the subject has no line in yet, and a top-10
    pie built on it would be empty.
    """
    funded = [y for y in per_year if per_year[y]["amount_allocated"][0] > 0]
    return max(funded) if funded else None


# --- the four blocks ----------------------------------------------------------

def trend_chart(items, budgets, subject: str) -> str:
    codes = _counted(items)
    per_year = _yearly(budgets, codes)
    years = sorted(per_year)

    # Phase 1 materialises a row for every year a code exists in the database,
    # including years long before or after the subject was funded. Those show up
    # as a flat zero tail on both ends of the chart, so trim them.
    def empty(year: str) -> bool:
        return all(per_year[year][field][0] == 0 for field, _ in SERIES)

    while years and empty(years[0]):
        years.pop(0)
    while years and empty(years[-1]):
        years.pop()
    if not years:
        return NO_DATA % subject

    traces = []
    for field, name in SERIES:
        values: List[Optional[float]] = [
            per_year[y][field][0] if per_year[y][field][1] else None for y in years
        ]
        if field == "amount_used":
            # A year that has not been executed yet reports 0, not NULL, which
            # would draw the execution line straight down to the axis and read as
            # "the money was cut". Trailing zeros are that artefact, not a finding.
            for i in range(len(values) - 1, -1, -1):
                if values[i]:
                    break
                values[i] = None
        if all(v is None for v in values):
            continue
        traces.append({"type": "scatter", "mode": "lines+markers",
                       "name": name, "x": years, "y": values})
    if not traces:
        return NO_DATA % subject

    block = _plotly({
        "data": traces,
        "layout": {
            "title": "מגמה תקציבית לאורך השנים",
            "xaxis": {"title": "שנה", "type": "category"},
            "yaxis": {"title": "תקציב ב-₪", "rangemode": "tozero",
                      "separatethousands": True},
        },
    })

    excluded = len(items) - len(codes)
    if excluded:
        block += ("\n\nהסכומים מסכמים %d סעיפי תקציב. %d סעיפים נוספים "
                  "(רזרבות, העברות פנימיות והכנסה מיועדת) אינם נכללים בסיכום "
                  "כדי למנוע ספירה כפולה." % (len(codes), excluded))
    return block


def top_items_chart(items, budgets, subject: str, top_n: int = 10) -> str:
    codes = _counted(items)
    year = _current_year(_yearly(budgets, codes))
    if not year:
        return NO_DATA % subject

    amounts = _alloc_by_code(budgets, codes, year)
    if not amounts:
        return NO_DATA % subject

    by_code = {r["code"]: r for r in items}
    ranked = sorted(amounts.items(), key=lambda kv: kv[1], reverse=True)[:top_n]

    # Two ministries can run programmes with identical titles ("תמיכות", "מחקר"),
    # and two identically-labelled slices are unreadable — disambiguate with the code.
    titles = [by_code.get(code, {}).get("title", code) for code, _ in ranked]
    labels = [
        "%s (%s)" % (title, code) if titles.count(title) > 1 else title
        for title, (code, _) in zip(titles, ranked)
    ]

    return _plotly({
        "data": [{"type": "pie", "textinfo": "label+percent",
                  "labels": labels, "values": [round(v, 2) for _, v in ranked]}],
        "layout": {"title": "סעיפי התקציב הגדולים ביותר בשנת %s (top %d)"
                            % (year, len(ranked))},
    })


def sources_chart(items, budgets, subject: str) -> str:
    """Where the subject's money comes from, by funding ministry.

    Deliberately not built from hierarchy.csv: that file holds the *whole*
    budget tree of every ministry that funds the subject, so a pie of it shows
    the ministries' total budgets rather than the subject's, which for a narrow
    subject is off by three orders of magnitude. Grouping the subject's own
    selected items by their level-1 office answers the question the section asks.
    """
    codes = _counted(items)
    per_year = _yearly(budgets, codes)
    year = _current_year(per_year)
    if not year:
        return NO_DATA % subject

    by_code = {r["code"]: r for r in items}
    funded_years = sorted(y for y in per_year if per_year[y]["amount_allocated"][0] > 0)

    def group(amounts: Dict[str, float], field: str) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for code, amount in amounts.items():
            key = (by_code.get(code, {}).get(field) or "").strip() or "לא מסווג"
            out[key] = out.get(key, 0.0) + amount
        return out

    # A one-slice pie says nothing, so widen the question until it splits. Ministry
    # before programme (the section is about *sources*), current year before the
    # whole period. Narrow subjects routinely end up in the last rung: נוער מחונן
    # has 4 live lines in 2026, all inside one programme of משרד החינוך, but 7
    # ministries funded it across the period.
    current = _alloc_by_code(budgets, codes, year)
    lifetime = _alloc_by_code(budgets, codes)
    period = "%s-%s" % (funded_years[0], funded_years[-1]) if funded_years else year
    for amounts, field, scope, label in (
        (current, "office", year, "משרד מממן"),
        (lifetime, "office", period, "משרד מממן"),
        (current, "program", year, "תכנית"),
        (lifetime, "program", period, "תכנית"),
    ):
        totals = group(amounts, field)
        if len(totals) >= 2:
            break
    else:
        return NO_DATA % subject

    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    return _plotly({
        "data": [{"type": "pie", "textinfo": "label+percent",
                  "labels": [k for k, _ in ranked],
                  "values": [round(v, 2) for _, v in ranked]}],
        "layout": {"title": "מקורות תקציב לפי %s (%s)" % (label, scope)},
    })


def _leaves(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every selected line under a node, in code order."""
    if entry.get("leaf"):
        return [entry]
    out: List[Dict[str, Any]] = []
    for _, child in sorted(entry["children"].items()):
        out.extend(_leaves(child))
    return out


def _collapsed_label(depth: int, count: int) -> str:
    """What a single row standing in for `count` lines calls itself."""
    noun = {1: "כל המשרד", 2: "כל התחום", 3: "כל התכנית"}.get(depth, "כל הסעיפים")
    return "%s — %d סעיפים" % (noun, count)


def hierarchy_list(items, budgets, hierarchy, subject: str,
                   latest_year: int = ref.LATEST_YEAR) -> str:
    """The selected items as a משרד / תכנית / סעיף table, with links.

    Built from selected_items.csv rather than from hierarchy.csv's full tree: the
    section is the list of lines this page actually covers, and for a subject like
    נוער מחונן hierarchy.csv holds 135 rows of which a handful are relevant.
    hierarchy.csv is used to name and link the level 1-3 ancestors of the selected
    items, and for the coverage test below.

    This used to be a nested bullet list sitting in the middle of the page, and it
    was the longest thing on it by an order of magnitude — 190 lines for renewables,
    of which 17 were in the current budget. It is a table in an appendix now, and
    a subtree collapses to one row when its lines add nothing a reader of a summary
    needs: either the selected lines cover the node's *whole* budget (the programme
    is wholly this subject, so naming it is naming them), or none of them appears in
    the current year's book (a programme that ended, listed line by line, was most
    of the old length). `selected_items.csv` remains the full, uncollapsed record.
    """
    if not items:
        return NO_DATA % subject

    parents = {r["code"]: r for r in hierarchy if r.get("code")}
    codes = _counted(items)
    year = _current_year(_yearly(budgets, codes))
    current = _alloc_by_code(budgets, codes, year) if year else {}
    # Same, but including reserves/transfers, which carry no amount anywhere else
    # in this block. Only used to decide whether a subtree is dormant: one live
    # reserve line under a programme means it is not.
    live = _alloc_by_code(budgets, {r["code"] for r in items}, year) if year else {}
    last = _last_funded(budgets, codes)

    # code -> {"label": ..., "url": ..., "amount": ..., "children": {...}}
    tree: Dict[str, Any] = {}
    for item in sorted(items, key=lambda r: r["code"]):
        code = item["code"]
        node = tree
        # Levels 1, 2 and 3 are the first 2, 5 and 8 characters of the dotted code.
        for width, fallback in ((2, item.get("office")), (5, None), (8, item.get("program"))):
            if len(code) <= width:
                break
            prefix = code[:width]
            parent = parents.get(prefix, {})
            entry = node.setdefault(prefix, {
                "code": prefix,
                "label": parent.get("title") or fallback or prefix,
                "url": parent.get("item_url", ""),
                "amount": 0.0,
                "leaf": False,
                "children": {},
            })
            # Only the current year rolls up: a parent labelled with a mix of
            # 2026 and 2011 money would be a number that exists nowhere.
            entry["amount"] += current.get(code, 0.0)
            node = entry["children"]
        node[code] = {
            "code": code,
            "label": item.get("title") or code,
            "url": item.get("item_url", ""),
            "amount": current.get(code, 0.0),
            "live": live.get(code, 0.0),
            "last": last.get(code),
            "counts": item.get("counts_in_total", "yes") != "no",
            "leaf": True,
            "children": {},
        }

    def covered(entry: Dict[str, Any]) -> bool:
        """The selected lines under this node add up to its whole budget."""
        if year != str(latest_year):
            # hierarchy.csv is a snapshot of one year; comparing a 2019 rollup
            # against a 2026 programme budget would collapse on nothing.
            return False
        total = _num(parents.get(entry["code"], {}).get("amount_allocated"))
        return bool(total) and entry["amount"] >= FULLY_COVERED * total

    # Each row is the path from the ministry down to the line (or to the node that
    # stands in for a group of them), so the columns can be filled from its ends.
    rows: List[List[Dict[str, Any]]] = []

    def walk(node: Dict[str, Any], path: List[Dict[str, Any]]) -> None:
        for _, entry in sorted(node.items(), key=lambda kv: (-kv[1]["amount"], kv[0])):
            here = path + [entry]
            if entry["leaf"]:
                rows.append(here)
                continue
            lines = _leaves(entry)
            dormant = len(here) == 3 and not any(line["live"] for line in lines)
            if len(lines) >= COLLAPSE_MIN_LINES and (covered(entry) or dormant):
                rows.append(here)
                continue
            walk(entry["children"], here)

    walk(tree, [])
    if not rows:
        return NO_DATA % subject

    def label_of(entry: Dict[str, Any]) -> str:
        # hierarchy.csv only covers the latest budget year, so an ancestor of a
        # long-dead line may have no title to show — then the code is the label.
        text = (entry["code"] if entry["label"] == entry["code"]
                else "%s (%s)" % (entry["label"], entry["code"]))
        return _link(_cell(text), entry["url"])

    header = "תקציב מקורי %s" % year if year else "תקציב מקורי"
    table = ["| משרד | תכנית | סעיף | %s | הערות |" % header,
             "|---|---|---|---|---|"]
    # Rows are emitted depth-first, so a ministry or programme owns a contiguous
    # run of them. Repeating its full linked title down that run triples the width
    # of the table for no information; the blank reads as "as above".
    previous: List[str] = []
    for path in rows:
        entry = path[-1]
        notes: List[str] = []
        if entry["leaf"]:
            program = label_of(path[-2]) if len(path) > 1 else "—"
            item = label_of(entry)
            if not entry["amount"] and entry["last"]:
                last_year, last_amount = entry["last"]
                notes.append("לא בתקציב %s (אחרון: %s ב-%s)"
                             % (year, _shekels(last_amount), last_year))
            if not entry["counts"]:
                notes.append("רזרבה/העברה — אינו נכלל בסיכום")
        else:
            group = _leaves(entry)
            program = label_of(entry) if len(path) > 1 else "—"
            item = _collapsed_label(len(path), len(group))
            if not entry["amount"]:
                dates = [line["last"][0] for line in group if line["last"]]
                notes.append("אף סעיף אינו בתקציב %s%s"
                             % (year, " (אחרון: %s)" % max(dates) if dates else ""))
            flagged = sum(1 for line in group if not line["counts"])
            if flagged:
                notes.append("כולל %d רזרבות/העברות שאינן בסיכום" % flagged)
        ancestors = [label_of(path[0]), program]
        shown = ["" if new == old else new
                 for new, old in zip(ancestors, previous or ["", ""])]
        previous = ancestors
        table.append("| %s | %s | %s | %s | %s |" % (
            shown[0], shown[1], item,
            _shekels(entry["amount"]) if entry["amount"] else "—",
            _cell(" · ".join(notes))))

    if year:
        table.append("")
        table.append(
            "הטבלה מציגה %d שורות עבור %d סעיפי תקציב נבחרים. הסכומים הם התקציב "
            "המקורי לשנת %s; סכום ברמת משרד/תחום/תכנית הוא סך הסעיפים הנבחרים "
            "שתחתיו בלבד, ולא תקציבו המלא. תכנית שכל תקציבה נכלל בנושא, או שאף "
            "סעיף שלה אינו בתקציב %s, מוצגת בשורה אחת במקום סעיף-סעיף; הפירוט "
            "המלא נמצא בקובץ selected_items.csv של הריצה."
            % (len(rows), len(items), year, year))
    return "\n".join(table)


# --- assembly -----------------------------------------------------------------

def deterministic_blocks(run_dir: str, subject: str) -> Dict[str, str]:
    """Every template block that phase 1's CSVs fully determine, rendered."""
    items = [r for r in _read(run_dir, "selected_items.csv") if r.get("code")]
    budgets = _read(run_dir, "item_budgets.csv")
    hierarchy = _read(run_dir, "hierarchy.csv")

    if not items:
        return {token: NO_DATA % subject for token in TOKEN_HEADINGS}

    return {
        TREND_CHART: trend_chart(items, budgets, subject),
        TOP_ITEMS_CHART: top_items_chart(items, budgets, subject),
        SOURCES_CHART: sources_chart(items, budgets, subject),
        HIERARCHY_LIST: hierarchy_list(items, budgets, hierarchy, subject),
    }


def _repair(text: str, heading: str, block: str) -> Tuple[str, bool]:
    """Put `block` into its section when the model dropped the token entirely.

    The observed failure mode is the model replacing a whole section with
    "לא נמצא מידע רלוונטי" while phase 1 did find the data, so a placeholder line
    inside the section is replaced; otherwise the block is appended to the section.
    """
    lines = text.split("\n")
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        return text, False

    # The section ends at the *next heading of any level*, not the next one of the
    # same level: "## תכניות פעילות כיום" owns "### סעיפים בולטים", and appending
    # the top-10 chart to the end of the outer section would put it below the
    # suppliers pie that follows it.
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].lstrip().startswith("#"):
            end = i
            break

    for i in range(start + 1, end):
        if lines[i].strip().startswith("לא נמצא מידע"):
            lines[i] = block
            return "\n".join(lines), True

    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    lines.insert(end, "\n" + block)
    return "\n".join(lines), True


def apply_blocks(text: str, blocks: Dict[str, str]) -> Tuple[str, List[str]]:
    """Substitute {{TOKEN}} with its rendered block. Returns (text, unplaced tokens)."""
    unplaced: List[str] = []
    for token, block in blocks.items():
        marker = "{{%s}}" % token
        if marker in text:
            text = text.replace(marker, block)
            continue
        text, repaired = _repair(text, TOKEN_HEADINGS[token], block)
        if not repaired:
            unplaced.append(token)
    return text, unplaced


# --- frontmatter --------------------------------------------------------------
# Same argument as the blocks above: every field is fully determined before the
# model is called (subject, slug, today, model name), so asking it to transcribe
# them buys nothing and it got them wrong in four of the six checked-in reports
# — `model:` came back as `gpt-4o`, `gpt-4`, `ReportGeneratorV1.0` and a literal
# `{MODEL}`. Worse, the block only works if it is the very first thing in the
# file: `server/lib/scanDashboards.js` parses it with gray-matter, which gives
# up unless the opening `---` is at offset 0. `reports/Magendavidadom.md` wrapped
# the whole document in a code fence, which is enough to lose all of its
# metadata. So the model is now told not to write frontmatter at all (see
# prompts/skill_phase_final_synthesis.md rule 2) and we prepend it here.

TITLE_TEMPLATE = "נתוני תקציב, התקשרויות והחלטות ממשלה בתחום %s"

# The model wrapping its whole reply in ```markdown / ```yaml. Only stripped when
# BOTH ends match, so a reply that legitimately ends on a ```plotly fence but
# doesn't open with one is left alone.
_OPEN_FENCE = re.compile(r"\A\s*```[a-zA-Z]*[ \t]*\n")
_CLOSE_FENCE = re.compile(r"\n```[ \t]*\s*\Z")

# A frontmatter block the model wrote anyway, at either end of the reply. The
# trailing form is what the template used to ask for, so old habits show up there.
_LEAD_FRONTMATTER = re.compile(r"\A\s*-{3,}[ \t]*\n.*?\n-{3,}[ \t]*(?:\n|\Z)", re.DOTALL)
_TAIL_FRONTMATTER = re.compile(r"\n-{3,}[ \t]*\n(?:[^\n]*\n)+?-{3,}[ \t]*\s*\Z")


def frontmatter(subject: str, slug: str, today: str, model: str) -> str:
    """The YAML block `main_agent/instructions/content-file-schema.md` requires.

    Dates are quoted per that schema: bare YAML dates are parsed into `Date`
    objects and can shift a day across timezones.
    """
    return "\n".join([
        "---",
        f"title: {TITLE_TEMPLATE % subject}",
        f'created: "{today}"',
        f'updated: "{today}"',
        f"model: {model}",
        f"path: reports/{slug}",
        "---",
    ])


def apply_frontmatter(text: str, subject: str, slug: str, today: str,
                      model: str) -> Tuple[str, List[str]]:
    """Prepend the computed frontmatter, removing whatever the model emitted.

    Returns (text, notes) — the notes name each thing that had to be cleaned up,
    so a drifting synthesis prompt shows up in the run log rather than silently.
    """
    notes: List[str] = []

    if _OPEN_FENCE.match(text) and _CLOSE_FENCE.search(text):
        text = _CLOSE_FENCE.sub("", _OPEN_FENCE.sub("", text))
        notes.append("stripped a code fence wrapping the whole document")

    stripped = _LEAD_FRONTMATTER.sub("", text, count=1)
    if stripped != text:
        text = stripped
        notes.append("replaced the model's own frontmatter block")

    match = _TAIL_FRONTMATTER.search(text)
    if match and "title:" in match.group(0):
        text = text[:match.start()]
        notes.append("removed a trailing frontmatter block")

    return f"{frontmatter(subject, slug, today, model)}\n{text.strip()}\n", notes
