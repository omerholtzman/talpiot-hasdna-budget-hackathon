# -*- coding: utf-8 -*-
"""Deterministic query layer over the BudgetKey MCP.

Everything here runs without a model. The pipeline's retrieval and materialisation
steps go through these helpers so that no budget figure ever passes through an LLM.

Why the MCP rather than https://next.obudget.org/api/query directly: the plain API
returns the same rows but omits the `warnings` field, and that warning is the only
thing that catches `code LIKE '24%'` silently double-counting a parent with all of
its children. Both cap a page at 1000 rows, so the API buys nothing.
"""

import csv
import io
import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

PAGE_SIZE = 1000  # server-side cap; larger values are silently clamped

# Measured: a query whose wrapped text reaches ~2990 characters comes back with zero
# rows, no error and no warning. 185 codes in an IN-list are fine, 190 are not. This
# is the most dangerous limit here because it looks exactly like "no matching data",
# so queries are chunked to stay under it and anything longer raises instead.
MAX_SQL_CHARS = 2800
_WRAPPER_OVERHEAD = 80  # "SELECT * FROM (...) AS _sub LIMIT 1000 OFFSET 100000"

DEFAULT_DATASET = "budget_items_data"

# Short Hebrew words are substrings of unrelated long ones, so an ILIKE sweep on them
# is useless: '%גז%' returns 454 items, nearly all מגזר ("sector"); '%רוח%' returns 130,
# every one of them אירוח / ארוחות / ירוחם / רוחב / רוחני. Matching the same words with
# a word boundary gives 35 real natural-gas items and 1 row respectively. Longer
# keywords keep the substring form, which is what lets them match Hebrew inflections
# and attached prefixes (מתחדש -> מתחדשת, אנרגיה -> באנרגיה); at this length the noise
# is negligible and the recall is worth more.
EXACT_MATCH_MAX_CHARS = 3

# Characters that would otherwise be read as regex operators inside a ~ pattern.
_RE_METACHARS = r".^$*+?()[]{}|\\"

# A '%' inside a code filter mixes levels and double-counts parents with children.
_CODE_WILDCARD_RE = re.compile(r"\bcode\s+(?:NOT\s+)?I?LIKE\s+'[^']*%", re.IGNORECASE)


class QueryError(RuntimeError):
    """Raised when a query is malformed or the server reports a warning."""


def check_sql(query: str) -> List[str]:
    """Static checks for the two mistakes this dataset punishes hardest.

    Returned as a list of problems rather than raised, so callers can log them
    against a specific step. See verify_sql() for the raising variant.
    """
    problems = []
    if _CODE_WILDCARD_RE.search(query):
        problems.append(
            "code filter uses '%' - this mixes budget levels and double-counts "
            "parents with their children. Use LEFT(code, N) = '..' instead."
        )
    if not re.search(r"\blevel\s*(=|IN)", query, re.IGNORECASE) and "TOTAL" not in query:
        problems.append("query does not pin `level`; sums across levels are meaningless.")
    return problems


def verify_sql(query: str) -> None:
    problems = check_sql(query)
    if problems:
        raise QueryError("; ".join(problems))


class BudgetAPI:
    """Thin, paging, warning-aware wrapper around an initialised MCPClient."""

    def __init__(self, mcp_client, dataset: str = DEFAULT_DATASET, strict: bool = True):
        self.mcp = mcp_client
        self.dataset = dataset
        self.strict = strict
        self.request_chars = 0  # rough cost signal for the run summary
        self.query_count = 0

    # ---------------------------------------------------------------- queries

    def query(
        self,
        sql: str,
        dataset: Optional[str] = None,
        max_rows: int = 20000,
        check: bool = True,
    ) -> Tuple[List[Dict[str, Any]], Optional[List[str]]]:
        """Runs `sql` and returns (rows, warnings), fetching every page.

        Paging works by wrapping the caller's SQL in LIMIT/OFFSET, so callers never
        have to think about the 1000-row cap.
        """
        if check:
            verify_sql(sql)
        inner = sql.strip().rstrip(";")
        if len(inner) + _WRAPPER_OVERHEAD > MAX_SQL_CHARS:
            raise QueryError(
                "query is %d chars; above ~%d the server returns zero rows silently. "
                "Split it - see query_by_codes()." % (len(inner), MAX_SQL_CHARS)
            )
        rows: List[Dict[str, Any]] = []
        warnings: Optional[List[str]] = None
        offset = 0
        while True:
            wrapped = f"SELECT * FROM ({inner}) AS _sub LIMIT {PAGE_SIZE} OFFSET {offset}"
            self.request_chars += len(wrapped)
            self.query_count += 1
            raw = self.mcp.call_tool(
                "DatasetDBQuery",
                {"dataset": dataset or self.dataset, "query": wrapped, "page_size": PAGE_SIZE},
            )
            data = json.loads(raw)
            if warnings is None:
                warnings = data.get("warnings")
            batch = data.get("rows") or []
            rows.extend(batch)
            # The server also truncates a page by *response size*, not just row count:
            # a wide SELECT can come back with num_rows=471 while total_rows=1000. So a
            # short page does NOT mean the end, and the next offset must advance by the
            # rows actually received - advancing by PAGE_SIZE would skip the remainder.
            if not batch or len(rows) >= max_rows:
                break
            offset += len(batch)

        if warnings and self.strict:
            raise QueryError(f"server warning: {warnings}")
        return rows, warnings

    def query_by_codes(
        self,
        template: str,
        codes: Sequence[str],
        dataset: Optional[str] = None,
        check: bool = True,
    ) -> List[Dict[str, Any]]:
        """Runs `template` once per batch of codes and concatenates the rows.

        `template` must contain the literal `{codes}` where the IN-list belongs. Batch
        size is derived from MAX_SQL_CHARS so no single query can hit the silent-empty
        cliff, however many codes the caller has.
        """
        codes = list(dict.fromkeys(codes))  # de-dupe, keep order
        if not codes:
            return []
        skeleton = len(template.replace("{codes}", ""))
        budget = MAX_SQL_CHARS - _WRAPPER_OVERHEAD - skeleton
        if budget < 100:
            raise QueryError("template is too long to fit any codes under the SQL limit")
        per_code = max(len(c) for c in codes) + 4  # quotes, comma, space
        batch_size = max(1, min(len(codes), budget // per_code))

        rows: List[Dict[str, Any]] = []
        for i in range(0, len(codes), batch_size):
            batch = codes[i:i + batch_size]
            sql = template.replace("{codes}", self.sql_list(batch))
            got, _ = self.query(sql, dataset=dataset, check=check)
            if not got and len(batch) > 1:
                # Real data can legitimately be empty, but an empty batch is also what
                # the length cliff looks like, so make it visible rather than silent.
                print(
                    "[budget_api] warning: batch of %d codes returned no rows (sql %d chars)"
                    % (len(batch), len(sql)),
                    file=__import__("sys").stderr,
                )
            rows.extend(got)
        return rows

    def full_text_search(self, q: str, dataset: Optional[str] = None) -> Dict[str, Any]:
        """Fuzzy search. Returns the raw payload - `total_results` matters: when it
        exceeds the rows returned, the caller has not seen everything."""
        self.request_chars += len(q)
        self.query_count += 1
        raw = self.mcp.call_tool(
            "DatasetFullTextSearch", {"dataset": dataset or self.dataset, "q": q}
        )
        return json.loads(raw)

    # -------------------------------------------------------------- SQL parts

    @staticmethod
    def sql_list(values: Sequence[str]) -> str:
        """Renders a SQL IN-list, escaping single quotes."""
        return ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)

    @staticmethod
    def office_filter(office_codes: Sequence[str], alias: str = "") -> str:
        """`LEFT(code,2) IN (...)` - the wildcard-free way to filter by ministry."""
        col = f"{alias}.code" if alias else "code"
        return f"LEFT({col}, 2) IN ({BudgetAPI.sql_list(office_codes)})"

    @staticmethod
    def keyword_filter(keywords: Sequence[str], column: str = "title") -> str:
        """ORed title terms. Callers MUST combine this with a class or office scope:
        an unscoped title sweep takes 30-60s and frequently times out.

        Keywords of EXACT_MATCH_MAX_CHARS or fewer are matched as whole words, the rest
        as substrings - see the constant for the measured reason.
        """
        if not keywords:
            return "FALSE"
        return "(" + " OR ".join(
            BudgetAPI.keyword_term(k, column) for k in keywords
        ) + ")"

    @staticmethod
    def keyword_term(keyword: str, column: str = "title") -> str:
        """One keyword's SQL condition: `~ '\\yword\\y'` if short, `ILIKE '%word%'` if not."""
        kw = str(keyword).strip()
        if len(kw) <= EXACT_MATCH_MAX_CHARS and kw[:1].isalnum() and kw[-1:].isalnum():
            # \y is a word boundary; the server's locale treats Hebrew letters as word
            # characters, so it anchors correctly (verified against the live dataset).
            escaped = "".join("\\" + c if c in _RE_METACHARS else c for c in kw)
            return "%s ~ '\\y%s\\y'" % (column, escaped.replace("'", "''"))
        return "%s ILIKE '%%%s%%'" % (column, kw.replace("'", "''"))


# ------------------------------------------------------------------ CSV output


def csv_value(v: Any) -> Any:
    """NULL -> empty, whole floats -> ints, so CSVs stay clean and diffable."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def write_csv(rows: List[Dict[str, Any]], path: str, columns: Optional[List[str]] = None) -> int:
    """Writes rows to `path` (utf-8-sig so Excel opens Hebrew). Returns row count."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    if not rows:
        # Still create the file so downstream tooling can distinguish "ran, found
        # nothing" from "never ran".
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            if columns:
                csv.writer(f).writerow(columns)
        return 0
    cols = columns or list(rows[0].keys())
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: csv_value(r.get(k)) for k in cols})
    return len(rows)


def read_csv(path: str) -> List[Dict[str, str]]:
    with io.open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def csv_field_issues(body: str) -> Tuple[int, List[int]]:
    """(column count, line numbers whose field count differs from the header).

    An unquoted comma inside a Hebrew title shifts every later column: the row still
    parses, it is just silently wrong. Comparing models on such rows is worthless.
    """
    try:
        rows = list(csv.reader(io.StringIO(body)))
    except Exception:
        return 0, []
    rows = [r for r in rows if r and any(c.strip() for c in r)]
    if not rows:
        return 0, []
    cols = len(rows[0])
    return cols, [i + 2 for i, r in enumerate(rows[1:]) if len(r) != cols]
