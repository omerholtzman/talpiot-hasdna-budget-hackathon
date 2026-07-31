# -*- coding: utf-8 -*-
"""Deterministic budget-item discovery pipeline (replaces the Phase 1 ReAct loop).

    expand -> retrieve -> triage -> judge -> materialise -> report

Steps 2, 5 and 6 never touch a model, so no budget figure can be invented: every
number in the output CSVs comes straight from SQL. Steps 1, 3 and 4 are single
schema-constrained calls with bounded input, so cost is predictable and the run is
reproducible enough to compare models against each other.

This is ordinary synchronous code, called from the async phase-1 node via
`asyncio.to_thread`. Its SQL goes through a SyncMCPBridge (mcp_tools.py) and its
model calls through a JSONLLM (llm_json.py); neither the graph nor the event loop
appears anywhere below.

Ported from talpihackathon/main_agent/step1_pipeline.py — keep the two in sync.
"""

import json
import os
import threading
import time
from concurrent import futures
from datetime import datetime
from typing import Any, Dict, List, Optional

import helpers.prompts.budget_reference as ref
from helpers.prompts.budget_api import (EXACT_MATCH_MAX_CHARS, BudgetAPI, QueryError,
                                        read_csv, write_csv)
from config import (EXPAND, JUDGE_ITEMS, PHASE1, PHASE_LABELS,
                    TRIAGE_DOMAINS, TRIAGE_PROGRAMS)
from helpers.prompts.logs import log
from agent_engineering.prompt_loader import load_prompt

_LABEL = PHASE_LABELS[PHASE1]


def print_agent(text: str):
    log(_LABEL, text)


def print_error(text: str):
    log(_LABEL, "ERROR " + text)


# One verdict per input row has to fit in a single response. 181 programs came back
# complete in testing, but a large subject has ~390 programs and several thousand
# items, so both are chunked well below the point where the reply gets cut off - a
# truncated reply silently costs recall, and only the unjudged-count log reveals it.
CHUNK_ITEMS = 150          # items per judging call
MAX_PROGRAMS_PER_CALL = 150
# Chunks are independent, so they overlap. Judging is dominated by output-token
# generation (~110s for a 250-item chunk), and a run spends most of its wall clock
# there. 4 keeps well inside a default Vertex quota; a 429 is retried with backoff
# inside the client, so overshooting degrades rather than fails. Set it to 1 to get
# sequential behaviour back.
MAX_PARALLEL_CALLS = 4
# Domains are inlined into three queries as a "LEFT(code,5) IN (...)" list. Each code
# costs ~9 chars, and the largest of those queries has ~900 chars of skeleton, so this
# stays comfortably inside budget_api.MAX_SQL_CHARS.
MAX_DOMAINS_INLINE = 180


# --------------------------------------------------------------------- schemas

EXPAND_SCHEMA = {
    "type": "object",
    "properties": {
        "functional_classes": {"type": "array", "items": {"type": "string"}},
        "offices": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "fts_queries": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": ["functional_classes", "offices", "keywords", "fts_queries"],
}


def _verdict_schema(values: List[str]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "verdict": {"type": "string", "enum": values},
                        "reason": {"type": "string"},
                    },
                    "required": ["code", "verdict"],
                },
            }
        },
        "required": ["verdicts"],
    }


TRIAGE_SCHEMA = _verdict_schema(["keep", "ambiguous", "drop"])
# Two verdicts, not three. A "related" bucket used to sit between them for reserves,
# internal transfers and earmarked revenue - but that distinction is `economic_class`,
# a database column, so asking a model for it was both unnecessary and lossy: it
# routed 567 items there, including two lines whose titles named the subject outright.
# It is now computed in step_materialise and the judge only decides on-subject or not.
JUDGE_SCHEMA = _verdict_schema(["selected", "dropped"])


# --------------------------------------------------------------------- helpers

def _fmt_rows(rows: List[Dict[str, Any]], columns: List[str]) -> str:
    """Compact pipe-delimited block - cheaper per row than JSON, and the model
    never has to reproduce it, so quoting does not matter."""
    out = [" | ".join(columns)]
    for r in rows:
        out.append(" | ".join(str(r.get(c) or "") for c in columns))
    return "\n".join(out)


def _chunks(items: List[Any], size: int) -> List[List[Any]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


class Cost:
    """Tracks what each step spent, for the run summary and model comparison."""

    def __init__(self):
        self.llm_chars = 0
        self.llm_calls = 0
        self.steps: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def record(self, step: str, provider, seconds: float, extra: Optional[Dict] = None):
        # Called from worker threads, so read the payload (thread-local on the provider)
        # before taking the lock, then mutate the shared totals under it.
        payload = getattr(provider, "last_payload", None)
        chars = len(json.dumps(payload, ensure_ascii=False)) if payload else 0
        with self._lock:
            self.llm_chars += chars
            self.llm_calls += 1
            entry = self.steps.setdefault(step, {"calls": 0, "chars": 0, "seconds": 0.0})
            entry["calls"] += 1
            entry["chars"] += chars
            # Wall-clock seconds no longer sum to elapsed time once calls overlap; this
            # is total time spent waiting on the model, not the duration of the step.
            entry["seconds"] = round(entry["seconds"] + seconds, 1)
            if extra:
                entry.update(extra)


# ----------------------------------------------------------------------- steps

def step_expand(provider, subject: str, cost: Cost) -> Dict[str, Any]:
    """LLM: subject -> offices, functional classes, keywords."""
    offices_txt = "\n".join(
        "- `%s` %s%s" % (c, v["title"],
                         "" if v["last_year"] >= ref.LATEST_YEAR
                         else "  (historical, %s-%s)" % (v["first_year"], v["last_year"]))
        for c, v in sorted(ref.OFFICES.items())
    )
    pairs_txt = "\n".join(
        "- %s: ordinary %s | development %s" % (
            p["area"], ", ".join(p["ordinary"]) or "-", ", ".join(p["development"]) or "-")
        for p in ref.DEVELOPMENT_PAIRS
    )
    prompt = load_prompt(
        EXPAND,
        TODAY=datetime.now().strftime("%Y-%m-%d"),
        SUBJECT=subject,
        FUNCTIONAL_CLASSES="\n".join("- %s" % c for c in ref.FUNCTIONAL_CLASSES),
        OFFICES=offices_txt,
        DEVELOPMENT_PAIRS=pairs_txt,
    )
    t0 = time.time()
    result = provider.generate_json(prompt, EXPAND_SCHEMA)
    cost.record("expand", provider, time.time() - t0)

    # EXPAND_SCHEMA is an object with several array properties, so unlike the
    # verdict schemas there's no single array to fall back to if the model
    # returns the wrong shape - just fail this step clearly instead of crashing
    # on an unguarded .get() below.
    if not isinstance(result, dict):
        raise QueryError(
            "expand step got a malformed response (expected an object, got %s)"
            % type(result).__name__
        )

    # The model may only choose from the closed sets; anything else is dropped.
    offices = ref.valid_offices([str(o).strip() for o in result.get("offices", [])])
    dropped_offices = [o for o in result.get("offices", []) if o not in offices]
    twins = ref.development_offices_for(offices)
    if twins:
        print_agent("  added development twins the model omitted: %s" % ", ".join(twins))
    offices = offices + twins

    valid_classes = {ref.normalize(c) for c in ref.FUNCTIONAL_CLASSES}
    classes = [c for c in result.get("functional_classes", [])
               if ref.normalize(c) in valid_classes]

    # Short keywords are kept, not dropped: they are matched as whole words instead of
    # substrings, which is the only way a word like גז survives a title sweep at all.
    keywords = [k.strip() for k in result.get("keywords", []) if k.strip()]
    short = [k for k in keywords if len(k) <= EXACT_MATCH_MAX_CHARS]
    if short:
        print_agent("  %d short keyword(s) matched as whole words: %s"
                    % (len(short), ", ".join(short)))

    plan = {
        "offices": offices,
        "functional_classes": classes,
        "keywords": keywords,
        "fts_queries": [q for q in result.get("fts_queries", []) if q.strip()][:4],
        "notes": result.get("notes", ""),
        "rejected_offices": dropped_offices,
        "whole_word_keywords": short,
    }
    print_agent("  offices=%s classes=%s keywords=%d" % (
        ",".join(offices), ",".join(classes), len(keywords)))
    return plan


def fetch_domains(api: BudgetAPI, offices: List[str]) -> List[Dict]:
    """Every level-2 domain in the chosen offices. Cheap: a few dozen short rows."""
    sql = """
        SELECT DISTINCT ON (b.code) b.code, b.title, o.title AS office
        FROM budget_items_data b
        LEFT JOIN budget_items_data o ON o.year = b.year AND o.level = 1 AND o.code = LEFT(b.code, 2)
        WHERE b.level = 2 AND %s
        ORDER BY b.code, b.year DESC
    """ % api.office_filter(offices or ["00"], "b")
    rows, _ = api.query(sql)
    return rows


def step_domains(provider, subject: str, plan: Dict, domains: List[Dict], cost: Cost,
                 max_parallel: Optional[int] = None) -> Dict[str, Dict]:
    """LLM: which level-2 domains of the chosen ministries could hold the subject.

    This is the only stage that can discard a whole branch for the price of one short
    row. Selecting a ministry pulls in everything it funds - the environment ministry
    brought in solid waste, coasts, hazardous materials and animal welfare - and each
    of those domains otherwise reaches program triage and item judging at full cost.
    """
    verdicts = _judge_batch(
        provider, TRIAGE_DOMAINS, subject,
        domains, ["code", "title", "office"], "DOMAINS",
        TRIAGE_SCHEMA, cost, "domains", max_parallel)
    for d in domains:
        verdicts.setdefault(d["code"], {"verdict": "ambiguous", "reason": "unjudged - defaulted"})
    split = {"keep": 0, "ambiguous": 0, "drop": 0}
    for v in verdicts.values():
        split[v["verdict"]] = split.get(v["verdict"], 0) + 1
    print_agent("  keep=%d ambiguous=%d drop=%d of %d domain(s)"
                % (split["keep"], split["ambiguous"], split["drop"], len(domains)))
    return verdicts


def step_retrieve(api: BudgetAPI, plan: Dict[str, Any],
                  open_domains: Optional[List[str]] = None) -> Dict[str, List[Dict]]:
    """Deterministic. Wide net: everything in the surviving domains and classes."""
    offices, classes = plan["offices"], plan["functional_classes"]
    if not offices and not classes:
        raise QueryError("expansion produced neither offices nor functional classes")

    # The office arm is narrowed to the domains that survived triage; the functional
    # class arm is deliberately left alone, because that is the cross-ministry net and
    # it reaches items in offices nobody selected.
    # An IN-list this long would push the query past the length at which the server
    # returns zero rows silently, so fall back to the wider office scope rather than
    # risk an empty result that looks like "no such data".
    if open_domains and len(open_domains) > MAX_DOMAINS_INLINE:
        print_error("  %d open domains is too many to inline - scoping by office instead"
                    % len(open_domains))
        open_domains = None

    if open_domains:
        office_arm = "LEFT(b.code, 5) IN (%s)" % api.sql_list(open_domains)
        program_arm = office_arm
    else:
        office_arm = api.office_filter(offices, "b") if offices else ""
        program_arm = api.office_filter(offices or ["00"], "b")

    scope = []
    if classes:
        scope.append("b.functional_class_detailed IN (%s)" % api.sql_list(classes))
    if office_arm:
        scope.append(office_arm)
    scope_sql = "(" + " OR ".join(scope) + ")"

    programs_sql = """
        SELECT DISTINCT ON (b.code) b.code, b.title, b.year,
               o.title AS office, l2.title AS domain
        FROM budget_items_data b
        LEFT JOIN budget_items_data o  ON o.year = b.year AND o.level = 1 AND o.code = LEFT(b.code, 2)
        LEFT JOIN budget_items_data l2 ON l2.year = b.year AND l2.level = 2 AND l2.code = LEFT(b.code, 5)
        WHERE b.level = 3 AND %s
        ORDER BY b.code, b.year DESC
    """ % program_arm
    programs, _ = api.query(programs_sql)

    candidates_sql = """
        SELECT DISTINCT ON (b.code) b.code, b.title,
               o.title AS office, l2.title AS domain, l3.title AS program,
               b.economic_class_primary AS economic_class,
               b.functional_class_detailed AS functional_class,
               LEFT(b.code, 8) AS program_code
        FROM budget_items_data b
        LEFT JOIN budget_items_data o  ON o.year = b.year AND o.level = 1 AND o.code = LEFT(b.code, 2)
        LEFT JOIN budget_items_data l2 ON l2.year = b.year AND l2.level = 2 AND l2.code = LEFT(b.code, 5)
        LEFT JOIN budget_items_data l3 ON l3.year = b.year AND l3.level = 3 AND l3.code = LEFT(b.code, 8)
        WHERE b.level = 4 AND %s
        ORDER BY b.code, b.year DESC
    """ % scope_sql
    candidates, _ = api.query(candidates_sql)

    # Year span per code, so the judge can see dormant/historical lines for what they are.
    span_sql = """
        SELECT code, MIN(year) AS first_year, MAX(year) AS last_year
        FROM budget_items_data WHERE level = 4 AND %s GROUP BY code
    """ % scope_sql.replace("b.", "")
    spans, _ = api.query(span_sql)
    span_by_code = {r["code"]: r for r in spans}
    for c in candidates:
        s = span_by_code.get(c["code"], {})
        c["first_year"] = s.get("first_year")
        c["last_year"] = s.get("last_year")

    # Keyword hits: scoped (never a bare title sweep - those take 30-60s and time out)
    # and deliberately allowed to reach outside the office list, because an on-subject
    # item can sit inside an unrelated program (04.63.03.46 under "פיתוח הנגב והגליל").
    keyword_hits: List[Dict] = []
    if plan["keywords"]:
        for cls in (classes or []):
            sql = """
                SELECT DISTINCT ON (code) code, title, LEFT(code, 8) AS program_code
                FROM budget_items_data
                WHERE level = 4 AND functional_class_detailed = '%s' AND %s
                ORDER BY code, year DESC
            """ % (cls.replace("'", "''"), api.keyword_filter(plan["keywords"]))
            rows, _ = api.query(sql)
            keyword_hits.extend(rows)
        if offices:
            sql = """
                SELECT DISTINCT ON (code) code, title, LEFT(code, 8) AS program_code
                FROM budget_items_data
                WHERE level = 4 AND %s AND %s
                ORDER BY code, year DESC
            """ % (api.office_filter(offices), api.keyword_filter(plan["keywords"]))
            rows, _ = api.query(sql)
            keyword_hits.extend(rows)

    fts_hits: List[Dict] = []
    fts_truncated = []
    for q in plan["fts_queries"]:
        try:
            data = api.full_text_search(q)
        except Exception as e:
            print_error("  full-text search %r failed: %s" % (q, str(e)[:120]))
            continue
        results = data.get("search_results") or []
        if data.get("total_results", 0) > len(results):
            fts_truncated.append({"query": q, "returned": len(results),
                                  "total": data.get("total_results")})
        for r in results:
            code = r.get("code", "")
            if code.count(".") == 3:  # level-4 only
                fts_hits.append({"code": code, "title": r.get("title"),
                                 "program_code": code[:8]})

    # Hits can land outside the office/class scope entirely - 04.63.03.46
    # "תשתיות אנרגיה מתחדשת" lives under משרד ראש הממשלה with functional class
    # "ראש הממשלה". Pull their metadata in so candidates.csv is a complete audit
    # trail and a later miss can be attributed to judging rather than retrieval.
    known = {c["code"] for c in candidates}
    orphan_codes = sorted({h["code"] for h in keyword_hits + fts_hits} - known)
    if orphan_codes:
        orphans = api.query_by_codes("""
            SELECT DISTINCT ON (b.code) b.code, b.title,
                   o.title AS office, l2.title AS domain, l3.title AS program,
                   b.economic_class_primary AS economic_class,
                   b.functional_class_detailed AS functional_class,
                   LEFT(b.code, 8) AS program_code
            FROM budget_items_data b
            LEFT JOIN budget_items_data o  ON o.year = b.year AND o.level = 1 AND o.code = LEFT(b.code, 2)
            LEFT JOIN budget_items_data l2 ON l2.year = b.year AND l2.level = 2 AND l2.code = LEFT(b.code, 5)
            LEFT JOIN budget_items_data l3 ON l3.year = b.year AND l3.level = 3 AND l3.code = LEFT(b.code, 8)
            WHERE b.level = 4 AND b.code IN ({codes})
            ORDER BY b.code, b.year DESC
        """, orphan_codes)
        orphan_spans = api.query_by_codes("""
            SELECT code, MIN(year) AS first_year, MAX(year) AS last_year
            FROM budget_items_data WHERE level = 4 AND code IN ({codes}) GROUP BY code
        """, orphan_codes)
        span2 = {r["code"]: r for r in orphan_spans}
        for o in orphans:
            s = span2.get(o["code"], {})
            o["first_year"] = s.get("first_year")
            o["last_year"] = s.get("last_year")
            o["found_by"] = "keyword/fts outside scope"
        candidates.extend(orphans)
        print_agent("  +%d item(s) matched only by keyword/FTS, outside the office scope"
                    % len(orphans))

    print_agent("  programs=%d candidates=%d keyword_hits=%d fts_hits=%d" % (
        len(programs), len(candidates), len(keyword_hits), len(fts_hits)))
    return {
        "programs": programs,
        "candidates": candidates,
        "keyword_hits": keyword_hits,
        "fts_hits": fts_hits,
        "fts_truncated": fts_truncated,
    }


def _judge_batch(provider, prompt_key: str, subject: str,
                 rows: List[Dict], columns: List[str], field: str,
                 schema: Dict, cost: Cost, step: str,
                 max_parallel: Optional[int] = None) -> Dict[str, Dict]:
    """Runs one prompt over `rows`, chunked, and returns {code: {verdict, reason}}.

    Chunks are independent - each is a stateless call sharing no context with the
    others - so they run concurrently. The step is bound by output-token generation
    (one verdict per input row), which no amount of chunking reduces; overlapping the
    calls is what actually shortens it.
    """
    size = MAX_PROGRAMS_PER_CALL if step == "triage" else CHUNK_ITEMS
    chunks = _chunks(rows, size)
    workers = max(1, min(max_parallel or MAX_PARALLEL_CALLS, len(chunks)))
    if len(chunks) > 1:
        print_agent("  %d chunk(s) of <=%d, %d at a time" % (len(chunks), size, workers))

    def run_chunk(numbered):
        i, chunk = numbered
        # The expansion step's free-text notes are deliberately NOT forwarded here. They
        # are a rationale for how the net was cast, and every classification stage read
        # them as a definition of the subject: a note saying the subject "has a strong
        # overlap with environmental protection" produced keep verdicts reasoned as
        # "waste treatment is related to energy". The subject is the only definition.
        prompt = load_prompt(
            prompt_key,
            TODAY=datetime.now().strftime("%Y-%m-%d"),
            SUBJECT=subject,
            **{field: _fmt_rows(chunk, columns)}
        )
        t0 = time.time()
        try:
            result = provider.generate_json(prompt, schema)
        except Exception as e:
            return i, {}, ["%s chunk %d failed: %s" % (step, i, str(e)[:160])]
        cost.record(step, provider, time.time() - t0)

        # The schema asks for {"verdicts": [...]}, but schema-constrained generation
        # is not 100% reliable - a model (especially once the native-schema call fails
        # and generate_json falls back to a text instruction) occasionally emits the
        # array directly instead of wrapping it. Tolerate that shape rather than
        # crashing the whole run on an unguarded .get(); anything else is a genuine
        # per-chunk failure, handled the same way an exception from generate_json is.
        if isinstance(result, list):
            verdict_list = result
        elif isinstance(result, dict):
            verdict_list = result.get("verdicts", [])
        else:
            return i, {}, ["%s chunk %d failed: unexpected response type %s"
                           % (step, i, type(result).__name__)]

        out: Dict[str, Dict] = {}
        for v in verdict_list:
            if not isinstance(v, dict):
                continue
            code = str(v.get("code", "")).strip()
            if code:
                out[code] = {"verdict": v.get("verdict"), "reason": v.get("reason", "")}
        # Checked against this chunk's own reply, not the running total, so a chunk
        # whose response was cut off is reported even if another chunk covered a code.
        missing = [r["code"] for r in chunk if r["code"] not in out]
        notes_out = []
        if missing:
            notes_out.append("%s chunk %d: %d item(s) unjudged, defaulting to the safe side"
                             % (step, i, len(missing)))
        return i, out, notes_out

    numbered = list(enumerate(chunks, 1))
    if workers == 1:
        results = [run_chunk(n) for n in numbered]
    else:
        with futures.ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(run_chunk, numbered))  # map preserves input order

    # Merged in chunk order so a run is reproducible regardless of completion order.
    verdicts: Dict[str, Dict] = {}
    for _, out, messages in results:
        verdicts.update(out)
        for msg in messages:
            print_error("  " + msg)
    return verdicts


def step_triage(provider, subject: str, plan: Dict, programs: List[Dict], cost: Cost,
                max_parallel: Optional[int] = None) -> Dict[str, Dict]:
    verdicts = _judge_batch(
        provider, TRIAGE_PROGRAMS, subject,
        programs, ["code", "title", "office", "domain"], "PROGRAMS",
        TRIAGE_SCHEMA, cost, "triage", max_parallel)
    # Anything the model failed to judge is treated as ambiguous, never dropped.
    for p in programs:
        verdicts.setdefault(p["code"], {"verdict": "ambiguous", "reason": "unjudged - defaulted"})
    split = {"keep": 0, "ambiguous": 0, "drop": 0}
    for v in verdicts.values():
        split[v["verdict"]] = split.get(v["verdict"], 0) + 1
    total = max(1, sum(split.values()))
    print_agent("  keep=%d ambiguous=%d drop=%d (ambiguous %.0f%%)" % (
        split["keep"], split["ambiguous"], split["drop"], 100.0 * split["ambiguous"] / total))
    if split["ambiguous"] / total > 0.34:
        print_error("  triage looks soft: >34% ambiguous - the prompt may need recalibrating")
    return verdicts


def step_judge(provider, subject: str, plan: Dict, retrieved: Dict,
               triage: Dict[str, Dict], cost: Cost,
               max_parallel: Optional[int] = None) -> Dict[str, Dict]:
    """Judges items in kept/ambiguous programs UNION every keyword and FTS hit.

    The union is essential: an on-subject item can live inside a program that is not
    about the subject at all, and program-level triage alone would lose it.
    """
    open_programs = {c for c, v in triage.items() if v["verdict"] in ("keep", "ambiguous")}
    by_code = {c["code"]: c for c in retrieved["candidates"]}

    selected_codes = {c["code"] for c in retrieved["candidates"]
                      if c.get("program_code") in open_programs}
    hit_codes = {h["code"] for h in retrieved["keyword_hits"] + retrieved["fts_hits"]}
    outside = hit_codes - selected_codes
    if outside:
        print_agent("  %d keyword/FTS hit(s) sit outside kept programs - included anyway"
                    % len(outside))

    to_judge = []
    for code in sorted(selected_codes | hit_codes):
        row = by_code.get(code)
        if row is None:
            # A hit outside the retrieval scope: keep it with what little we know.
            hit = next((h for h in retrieved["keyword_hits"] + retrieved["fts_hits"]
                        if h["code"] == code), {})
            row = {"code": code, "title": hit.get("title"), "office": "", "domain": "",
                   "program": "", "economic_class": "", "functional_class": "",
                   "first_year": "", "last_year": "", "program_code": code[:8]}
            by_code[code] = row
        # The judge is told what triage concluded about the containing program. Without
        # it, the rule "a bland title inside an on-subject program is real spending"
        # gets applied to merely-unruled-out programs, which is how payroll lines like
        # "שעות נוספות" ended up selected.
        row = dict(row)
        row["program_verdict"] = triage.get(row.get("program_code", ""), {}).get(
            "verdict", "keyword match - program not triaged")
        to_judge.append(row)

    print_agent("  judging %d item(s) from %d open program(s)" % (len(to_judge), len(open_programs)))
    verdicts = _judge_batch(
        provider, JUDGE_ITEMS, subject,
        to_judge, ["code", "title", "office", "program", "program_verdict",
                   "functional_class", "economic_class", "first_year", "last_year"], "ITEMS",
        JUDGE_SCHEMA, cost, "judge", max_parallel)
    for row in to_judge:
        verdicts.setdefault(row["code"], {"verdict": "selected",
                                          "reason": "unjudged - kept for review"})
    return verdicts


ITEM_COLUMNS = ["code", "title", "office", "program", "economic_class_primary",
                "counts_in_total", "item_url"]


def step_materialise(api: BudgetAPI, out_dir: str, codes: List[str]) -> Dict[str, int]:
    """Deterministic. Every figure below comes from SQL, none from a model."""
    if not codes:
        write_csv([], os.path.join(out_dir, "selected_items.csv"), ITEM_COLUMNS)
        write_csv([], os.path.join(out_dir, "item_budgets.csv"),
                  ["code", "year", "amount_allocated", "amount_revised", "amount_used"])
        return {"selected_items": 0, "item_budgets": 0}

    # Chunked: an IN-list of ~190 codes pushes the SQL past the length at which the
    # server silently returns nothing. query_by_codes sizes the batches for us.
    items = api.query_by_codes("""
        SELECT DISTINCT ON (b.code) b.code, b.title,
               o.title AS office, l3.title AS program,
               b.economic_class_primary, b.item_url
        FROM budget_items_data b
        LEFT JOIN budget_items_data o  ON o.year = b.year AND o.level = 1 AND o.code = LEFT(b.code, 2)
        LEFT JOIN budget_items_data l3 ON l3.year = b.year AND l3.level = 3 AND l3.code = LEFT(b.code, 8)
        WHERE b.level = 4 AND b.code IN ({codes})
        ORDER BY b.code, b.year DESC
    """, codes)
    items.sort(key=lambda r: r["code"])
    # Reserves, internal transfers, earmarked revenue and clearing accounts are real
    # findings but would double-count if summed alongside the rest. That is decided by
    # economic_class_primary, so it is read off the column rather than asked of a model.
    for r in items:
        r["counts_in_total"] = "no" if ref.is_non_substantive(
            r.get("economic_class_primary")) else "yes"

    budgets = api.query_by_codes("""
        SELECT code, year, amount_allocated, amount_revised, amount_used
        FROM budget_items_data
        WHERE level = 4 AND code IN ({codes})
        ORDER BY code, year
    """, codes)
    budgets.sort(key=lambda r: (r["code"], r["year"]))

    n1 = write_csv(items, os.path.join(out_dir, "selected_items.csv"), ITEM_COLUMNS)
    n2 = write_csv(budgets, os.path.join(out_dir, "item_budgets.csv"),
                   ["code", "year", "amount_allocated", "amount_revised", "amount_used"])
    off_total = sum(1 for r in items if r["counts_in_total"] == "no")
    if off_total:
        print_agent("  %d of %d item(s) are reserves/transfers/earmarked revenue "
                    "(counts_in_total=no)" % (off_total, len(items)))
    return {"selected_items": n1, "item_budgets": n2, "not_in_total": off_total}


def step_report(api: BudgetAPI, out_dir: str, codes: List[str],
                triage: Dict, judged: Dict, retrieved: Dict,
                domains: Optional[Dict] = None) -> Dict[str, Any]:
    """Deterministic data_errors and possible_misses - computed, never asked for."""
    errors: List[str] = []
    misses: List[str] = []

    if codes:
        rows = api.query_by_codes("""
            SELECT code, year, amount_allocated, amount_revised, amount_used
            FROM budget_items_data WHERE level = 4 AND code IN ({codes}) ORDER BY code, year
        """, codes)
        no_budget_years = sorted({r["year"] for r in rows if r["amount_allocated"] is None})
        unexecuted = sorted({r["year"] for r in rows if r["amount_used"] is None})
        negative = [(r["code"], r["year"], r["amount_used"]) for r in rows
                    if isinstance(r["amount_used"], (int, float)) and r["amount_used"] < 0]
        if no_budget_years:
            errors.append("amount_allocated is NULL for %s - no approved budget that year."
                          % ", ".join(str(y) for y in no_budget_years))
        if unexecuted:
            errors.append("amount_used is NULL for %s - not yet executed."
                          % ", ".join(str(y) for y in unexecuted))
        for code, year, amount in negative[:10]:
            errors.append("%s %s has negative amount_used (%s) - a refund or correction."
                          % (code, year, int(amount)))

        titles = api.query_by_codes("""
            SELECT code, title, MIN(year) AS f, MAX(year) AS l
            FROM budget_items_data WHERE level = 4 AND code IN ({codes})
            GROUP BY code, title ORDER BY code, f
        """, codes)
        by_code: Dict[str, List[Dict]] = {}
        for t in titles:
            by_code.setdefault(t["code"], []).append(t)
        for code, variants in by_code.items():
            if len(variants) > 1:
                errors.append("%s changed title over time: %s" % (
                    code, " -> ".join('"%s" (%s-%s)' % (v["title"], v["f"], v["l"])
                                      for v in variants)))
        # Same title under different codes with non-overlapping spans = likely migration.
        by_title: Dict[str, List[Dict]] = {}
        for t in titles:
            by_title.setdefault(t["title"], []).append(t)
        for title, variants in by_title.items():
            if len(variants) < 2:
                continue
            ordered = sorted(variants, key=lambda v: v["f"])
            for a, b in zip(ordered, ordered[1:]):
                if a["code"] != b["code"] and a["l"] < b["f"]:
                    errors.append('"%s" appears to have migrated %s (to %s) -> %s (from %s).'
                                  % (title, a["code"], a["l"], b["code"], b["f"]))

    ambiguous = {c for c, v in triage.items() if v["verdict"] == "ambiguous"}
    for prog in sorted(ambiguous):
        judged_here = [c for c in judged if c.startswith(prog)]
        if judged_here and not any(judged[c]["verdict"] == "selected" for c in judged_here):
            misses.append("program %s was ambiguous and every item in it was dropped - "
                          "worth a manual look." % prog)
    for t in retrieved.get("fts_truncated", []):
        misses.append("full-text search %r returned %s of %s results - the rest were never seen."
                      % (t["query"], t["returned"], t["total"]))
    dropped_programs = sum(1 for v in triage.values() if v["verdict"] == "drop")
    if dropped_programs:
        misses.append("%d program(s) were dropped at triage without their items being read; "
                      "see programs.csv for the reasons." % dropped_programs)
    dropped_domains = sum(1 for v in (domains or {}).values() if v["verdict"] == "drop")
    if dropped_domains:
        misses.append("%d whole domain(s) were dropped before retrieval - the coarsest "
                      "discard in the run; see domains.csv for the reasons." % dropped_domains)

    report = {"data_errors": errors, "possible_misses": misses}
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


# ------------------------------------------------------------------------ main

def run_pipeline(subject: str, out_dir: str, provider, mcp_client,
                 stop_after: Optional[str] = None,
                 max_parallel: Optional[int] = None) -> Dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)
    api = BudgetAPI(mcp_client)
    cost = Cost()
    started = time.time()

    print_agent("Step 1/7  expand")
    plan = step_expand(provider, subject, cost)
    with open(os.path.join(out_dir, "search_plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    if stop_after == "expand":
        return {"plan": plan}

    print_agent("Step 2/7  triage domains")
    domains = fetch_domains(api, plan["offices"])
    domain_verdicts = step_domains(provider, subject, plan, domains, cost, max_parallel)
    open_domains = sorted(c for c, v in domain_verdicts.items()
                          if v["verdict"] in ("keep", "ambiguous"))

    write_csv([{**d,
                "verdict": domain_verdicts.get(d["code"], {}).get("verdict", ""),
                "reason": domain_verdicts.get(d["code"], {}).get("reason", "")}
               for d in domains],
              os.path.join(out_dir, "domains.csv"),
              ["code", "title", "office", "verdict", "reason"])
    if not open_domains:
        print_error("  every domain was dropped - falling back to the full office scope")
        open_domains = None
    if stop_after == "domains":
        return {"plan": plan, "domains": len(domains), "open_domains": len(open_domains or [])}

    print_agent("Step 3/7  retrieve")
    retrieved = step_retrieve(api, plan, open_domains)
    write_csv(retrieved["candidates"], os.path.join(out_dir, "candidates.csv"))
    if stop_after == "retrieve":
        return {"plan": plan, "retrieved": {k: len(v) for k, v in retrieved.items()}}

    print_agent("Step 4/7  triage programs")
    triage = step_triage(provider, subject, plan, retrieved["programs"], cost, max_parallel)
    write_csv([{**p,
                "verdict": triage.get(p["code"], {}).get("verdict", ""),
                "reason": triage.get(p["code"], {}).get("reason", "")}
               for p in retrieved["programs"]],
              os.path.join(out_dir, "programs.csv"),
              ["code", "title", "office", "domain", "verdict", "reason"])

    print_agent("Step 5/7  judge items")
    judged = step_judge(provider, subject, plan, retrieved, triage, cost, max_parallel)
    selected = sorted(c for c, v in judged.items() if v["verdict"] == "selected")
    dropped = sorted(c for c, v in judged.items() if v["verdict"] == "dropped")

    by_code = {c["code"]: c for c in retrieved["candidates"]}
    write_csv([{"code": c,
                "title": by_code.get(c, {}).get("title", ""),
                "program": by_code.get(c, {}).get("program", ""),
                "reason": judged[c]["reason"]} for c in dropped],
              os.path.join(out_dir, "excluded_items.csv"),
              ["code", "title", "program", "reason"])

    print_agent("Step 6/7  materialise")
    counts = step_materialise(api, out_dir, selected)

    print_agent("Step 7/7  report")
    report = step_report(api, out_dir, selected, triage, judged, retrieved, domain_verdicts)
    hierarchy = build_hierarchy(api, out_dir, plan["offices"])

    triage_split = {"keep": 0, "ambiguous": 0, "drop": 0}
    for v in triage.values():
        triage_split[v["verdict"]] = triage_split.get(v["verdict"], 0) + 1
    domain_split = {"keep": 0, "ambiguous": 0, "drop": 0}
    for v in domain_verdicts.values():
        domain_split[v["verdict"]] = domain_split.get(v["verdict"], 0) + 1

    summary = {
        "subject": subject,
        "model": getattr(provider, "model", None) or getattr(provider, "cmd", "unknown"),
        "seconds": round(time.time() - started, 1),
        "search_plan": {k: plan[k] for k in ("offices", "functional_classes", "keywords")},
        "counts": {
            "domains": len(domains),
            "open_domains": len(open_domains or []),
            "programs": len(retrieved["programs"]),
            "candidates": len(retrieved["candidates"]),
            "keyword_hits": len({h["code"] for h in retrieved["keyword_hits"]}),
            "fts_hits": len({h["code"] for h in retrieved["fts_hits"]}),
            "items_judged": len(judged),
            "selected": len(selected),
            "not_in_total": counts.get("not_in_total", 0),
            "dropped": len(dropped),
            "item_budget_rows": counts["item_budgets"],
            "hierarchy_rows": len(hierarchy),
        },
        "domain_split": domain_split,
        "triage_split": triage_split,
        "cost": {
            "llm_calls": cost.llm_calls,
            "llm_request_chars": cost.llm_chars,
            "sql_queries": api.query_count,
            "sql_request_chars": api.request_chars,
            "by_step": cost.steps,
        },
        "data_errors": len(report["data_errors"]),
        "possible_misses": len(report["possible_misses"]),
    }
    with open(os.path.join(out_dir, "run_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print_agent("Done: %d selected (%d not in totals), %d dropped | %d LLM calls, %s chars | %.0fs"
                % (len(selected), counts.get("not_in_total", 0), len(dropped),
                   cost.llm_calls, format(cost.llm_chars, ","), summary["seconds"]))
    return summary


def build_hierarchy(api: BudgetAPI, out_dir: str, offices: List[str],
                    year: int = ref.LATEST_YEAR, min_amount: int = 0) -> List[Dict]:
    """Levels 1-3 for the given offices, for the dashboard's flow diagram.

    Replaces skill_phase4_hierarchy.md, which told the model to use
    `code LIKE '24%'` - the wildcard that mixes levels and double-counts a parent
    with all of its children, and which the server itself flags as a mistake.
    """
    if not offices:
        return []
    rows, _ = api.query("""
        SELECT code, title, level, amount_allocated, item_url
        FROM budget_items_data
        WHERE year = %d AND level IN (1, 2, 3) AND %s
        ORDER BY code
    """ % (year, api.office_filter(offices)))
    rows = [r for r in rows if (r.get("amount_allocated") or 0) >= min_amount]
    write_csv(rows, os.path.join(out_dir, "hierarchy.csv"),
              ["code", "title", "level", "amount_allocated", "item_url"])
    return rows


def build_digest(out_dir: str, subject: str, top_n: int = 25) -> str:
    """Renders a compact Hebrew/English digest of a pipeline run for downstream phases.

    Phases 2, 3 and 4 take Phase 1's findings as free text in their prompt. They need
    the budget codes, the ministries and a sense of scale - not the full item list -
    so this is assembled from the CSVs rather than asked of a model.
    """
    items = {r["code"]: r for r in read_csv(os.path.join(out_dir, "selected_items.csv"))}
    budgets = read_csv(os.path.join(out_dir, "item_budgets.csv"))
    if not items:
        return "Phase 1 found no budget items for '%s'." % subject

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # Reserves, internal transfers and earmarked revenue are kept in the item list
    # but left out of every total: step_materialise flagged them precisely because
    # summing them alongside ordinary lines double-counts the same shekel.
    counted = {c for c, r in items.items() if r.get("counts_in_total", "yes") != "no"}

    latest: Dict[str, Dict] = {}
    per_year: Dict[str, Dict[str, float]] = {}
    for row in budgets:
        year = row.get("year", "")
        code = row.get("code", "")
        alloc, rev, used = num(row.get("amount_allocated")), num(row.get("amount_revised")), num(row.get("amount_used"))
        prev = latest.get(code)
        if prev is None or year > prev["year"]:
            latest[code] = {"year": year, "allocated": alloc, "revised": rev, "used": used}
        if code not in counted:
            continue
        bucket = per_year.setdefault(year, {"allocated": 0.0, "revised": 0.0, "used": 0.0, "items": 0})
        bucket["allocated"] += alloc or 0.0
        bucket["revised"] += rev or 0.0
        bucket["used"] += used or 0.0
        bucket["items"] += 1

    years = sorted(per_year)
    offices = sorted({r.get("office", "") for r in items.values() if r.get("office")})

    lines = [
        "Phase 1 (budget items) - deterministic pipeline output.",
        "Subject: %s" % subject,
        "%d budget items (סעיפי תקציב, level 4) across %d ministries: %s."
        % (len(items), len(offices), ", ".join(offices)),
        "Coverage: %s-%s." % (years[0], years[-1]) if years else "",
        "",
        "Aggregate by year (₪, summed over the %d selected items that count "
        "towards a total; %d reserves/transfers/earmarked-revenue lines excluded):"
        % (len(counted), len(items) - len(counted)),
        "year | allocated | revised | used | items",
    ]
    for y in years:
        b = per_year[y]
        lines.append("%s | %d | %d | %d | %d" % (y, b["allocated"], b["revised"], b["used"], b["items"]))

    ranked = sorted(items.values(),
                    key=lambda r: (latest.get(r["code"], {}).get("allocated") or -1),
                    reverse=True)
    lines += ["", "Largest items by latest-year allocation (code | title | ministry | program | ₪):"]
    for r in ranked[:top_n]:
        info = latest.get(r["code"], {})
        alloc = info.get("allocated")
        lines.append("%s | %s | %s | %s | %s" % (
            r["code"], r.get("title", ""), r.get("office", ""), r.get("program", ""),
            "%d" % alloc if alloc is not None else ""))
    if len(ranked) > top_n:
        lines.append("... and %d more in selected_items.csv" % (len(ranked) - top_n))

    lines += ["", "Budget code prefixes for these items (use these to filter other datasets): %s"
              % ", ".join(sorted({c[:2] for c in items}))]

    report_path = os.path.join(out_dir, "report.json")
    if os.path.exists(report_path):
        with open(report_path, encoding="utf-8") as f:
            report = json.load(f)
        if report.get("data_errors"):
            lines += ["", "Data caveats:"] + ["- " + e for e in report["data_errors"][:8]]
    return "\n".join(l for l in lines if l is not None)


def render_hierarchy(out_dir: str, top_n: int = 40) -> str:
    """Renders hierarchy.csv as the Phase 4 section, without a model.

    The old skill file asked an agent to query the hierarchy itself, using
    `code LIKE '24%'` - see build_hierarchy above for why that is wrong. The
    pipeline has already written the same rows correctly, so this only formats them.
    """
    path = os.path.join(out_dir, "hierarchy.csv")
    if not os.path.exists(path):
        return "No hierarchy data available for this subject."
    rows = [r for r in read_csv(path) if r.get("code")]

    def amount(r):
        try:
            return float(r.get("amount_allocated") or 0)
        except ValueError:
            return 0.0

    rows = [r for r in rows if amount(r) > 0]
    rows.sort(key=amount, reverse=True)
    lines = [
        "Budget hierarchy (levels 1-3) for the ministries funding this subject, "
        "latest budget year. Amounts in shekels; a parent already includes its "
        "children, so these must not be summed together.",
        "code | title | level | amount_allocated | item_url",
    ]
    for r in rows[:top_n]:
        lines.append("%s | %s | %s | %d | %s" % (
            r["code"], r.get("title", ""), r.get("level", ""),
            amount(r), r.get("item_url", "")))
    return "\n".join(lines)
