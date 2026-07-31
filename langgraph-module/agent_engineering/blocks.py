# -*- coding: utf-8 -*-
"""Deterministic rendering of the synthesis template's data-driven blocks.

Phase 1 already writes every number these blocks need — `selected_items.csv`,
`item_budgets.csv` and `hierarchy.csv` are the data, and the markdown report is
only a view of them. Until now the synthesis model was asked to read a text
digest of those CSVs and hand-write the Plotly JSON and the nested item list
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
appear in the hierarchy list, flagged.
"""
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

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
    HIERARCHY_LIST: "### רשימת סעיפי תקציב נבחרים",
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


def hierarchy_list(items, budgets, hierarchy, subject: str) -> str:
    """The selected items as a nested משרד → תחום → תכנית → סעיף list, with links.

    Built from selected_items.csv rather than from hierarchy.csv's full tree: the
    section is titled "סעיפי תקציב נבחרים", and for a subject like נוער מחונן
    hierarchy.csv holds 135 rows of which a handful are relevant. hierarchy.csv is
    used only to name and link the level 1-3 ancestors of the items that were selected.
    """
    if not items:
        return NO_DATA % subject

    parents = {r["code"]: r for r in hierarchy if r.get("code")}
    codes = _counted(items)
    year = _current_year(_yearly(budgets, codes))
    current = _alloc_by_code(budgets, codes, year) if year else {}
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
                "label": parent.get("title") or fallback or prefix,
                "url": parent.get("item_url", ""),
                "amount": 0.0,
                "children": {},
            })
            # Only the current year rolls up: a parent labelled with a mix of
            # 2026 and 2011 money would be a number that exists nowhere.
            entry["amount"] += current.get(code, 0.0)
            node = entry["children"]
        node[code] = {
            "label": item.get("title") or code,
            "url": item.get("item_url", ""),
            "amount": current.get(code, 0.0),
            "last": last.get(code),
            "counts": item.get("counts_in_total", "yes") != "no",
            "children": {},
        }

    lines: List[str] = []

    def render(node: Dict[str, Any], depth: int) -> None:
        for code, entry in sorted(node.items(),
                                  key=lambda kv: (-kv[1]["amount"], kv[0])):
            # hierarchy.csv only covers the latest budget year, so an ancestor of a
            # long-dead line may have no title to show — then the code is the label.
            text = code if entry["label"] == code else "%s (%s)" % (entry["label"], code)
            label = _link(text, entry["url"])
            if entry["amount"]:
                suffix = " — %s" % _shekels(entry["amount"])
            elif entry.get("last"):
                last_year, last_amount = entry["last"]
                suffix = " — לא בתקציב %s (אחרון: %s ב-%s)" % (
                    year, _shekels(last_amount), last_year)
            else:
                suffix = ""
            if entry.get("counts") is False:
                suffix += " *(רזרבה/העברה — אינו נכלל בסיכום)*"
            lines.append("%s- %s%s" % ("  " * depth, label, suffix))
            render(entry["children"], depth + 1)

    render(tree, 0)
    if not lines:
        return NO_DATA % subject

    if year:
        lines.append("")
        lines.append("הסכומים הם התקציב המקורי לשנת %s. סכום ברמת משרד/תכנית הוא "
                     "סך הסעיפים הנבחרים שתחתיה בלבד, ולא תקציבה המלא." % year)
    return "\n".join(lines)


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
    # same level: "## מקורות תקציב" owns "### רשימת סעיפי תקציב נבחרים", and
    # appending the sources chart to the end of that would put it below the list
    # it is supposed to introduce.
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
