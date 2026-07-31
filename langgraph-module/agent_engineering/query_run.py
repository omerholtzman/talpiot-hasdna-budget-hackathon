# -*- coding: utf-8 -*-
"""Run saved BudgetKey query specs from Python.

This is the in-app equivalent of query_optimizer/query_run.sh. It deliberately
does not generate SQL and does not call a model: callers hand it a saved JSON
spec and an MCP client, and it runs DatasetDBQuery directly.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence


EXIT_INVALID_SPEC = 10
EXIT_TRANSPORT = 11
EXIT_QUERY_ERROR = 12
EXIT_QUERY_WARNING = 13
EXIT_MISSING_COLUMNS = 14
EXIT_TOO_FEW_ROWS = 15

DEFAULT_PAGE_SIZE = 50
DEFAULT_MAX_QUERY_ATTEMPTS = int(os.environ.get("MAX_QUERY_ATTEMPTS", "3"))
DEFAULT_MCP_URL = os.environ.get("WIKI_HARNESS_MCP_URL", "https://next.obudget.org/mcp")
DEFAULT_MCP_TIMEOUT = int(os.environ.get("WIKI_HARNESS_MCP_TIMEOUT", "180"))


class MCPClient(Protocol):
    """The small synchronous MCP surface this runner needs."""

    def call_tool(self, name: str, arguments: dict) -> str:
        ...


class HTTPMCPClient:
    """Minimal synchronous MCP-over-HTTP client for the standalone CLI.

    The app's LangGraph path can still pass SyncMCPBridge into run_query_spec_file().
    This class exists so `python -m agent_engineering.query_run ...` does not need
    langchain-mcp-adapters just to run a saved query.
    """

    def __init__(self, url: str = DEFAULT_MCP_URL, timeout: int = DEFAULT_MCP_TIMEOUT):
        self.url = url
        self.timeout = timeout
        self.session_id = ""
        self._request_id = 1
        self._initialize()

    def call_tool(self, name: str, arguments: dict) -> str:
        self._request_id += 1
        body, _ = self._post({
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        data = _json_from_http_body(body)
        if isinstance(data, dict) and data.get("error") is not None:
            raise QueryServerError(f"MCP server error: {data['error']}")
        structured = _nested_get(data, "result", "structuredContent")
        if structured is not None:
            return json.dumps(structured, ensure_ascii=False)
        return json.dumps(data, ensure_ascii=False)

    def _initialize(self) -> None:
        body, headers = self._post({
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "budget-query-spec-runner", "version": "1.0"},
            },
        }, include_session=False)
        self.session_id = headers.get("Mcp-Session-Id", "")
        if not self.session_id:
            raise TransportError(
                "could not find MCP session id in initialize response: %s" % body[:500]
            )
        self._post({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        })

    def _post(self, payload: Mapping[str, Any], *, include_session: bool = True) -> tuple[str, Mapping[str, str]]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "curl/8.5.0",
        }
        if include_session:
            headers["Mcp-Session-Id"] = self.session_id
        request = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", "replace")
                return body, response.headers
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise TransportError(f"MCP HTTP {exc.code}: {body[:500]}") from exc
        except urllib.error.URLError as exc:
            raise TransportError(f"failed to call MCP server: {exc}") from exc


class QuerySpecError(RuntimeError):
    """Base error with a query_run.sh-compatible exit code."""

    exit_code = EXIT_INVALID_SPEC


class InvalidSpecError(QuerySpecError):
    exit_code = EXIT_INVALID_SPEC


class TransportError(QuerySpecError):
    exit_code = EXIT_TRANSPORT


class QueryServerError(QuerySpecError):
    exit_code = EXIT_QUERY_ERROR


class QueryWarningError(QuerySpecError):
    exit_code = EXIT_QUERY_WARNING


class MissingColumnsError(QuerySpecError):
    exit_code = EXIT_MISSING_COLUMNS


class TooFewRowsError(QuerySpecError):
    exit_code = EXIT_TOO_FEW_ROWS


@dataclass(frozen=True)
class QuerySpecEntry:
    name: str
    title: str
    human_request: str
    notes: str
    dataset: str
    query: str
    page_size: int
    min_rows: int
    columns: List[str]
    expected_columns: List[str]


@dataclass(frozen=True)
class QuerySpecResult:
    name: str
    title: str
    human_request: str
    notes: str
    dataset: str
    query: str
    page_size: int
    min_rows: int
    columns: List[str]
    expected_columns: List[str]
    row_count: int
    rows: List[Dict[str, Any]]
    download_url: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "human_request": self.human_request,
            "notes": self.notes,
            "dataset": self.dataset,
            "query": self.query,
            "page_size": self.page_size,
            "min_rows": self.min_rows,
            "columns": self.columns,
            "expected_columns": self.expected_columns,
            "row_count": self.row_count,
            "rows": self.rows,
            "download_url": self.download_url,
        }


@dataclass(frozen=True)
class QuerySpecRun:
    query_file: Optional[str]
    title: str
    human_request: str
    notes: str
    results: List[QuerySpecResult]

    def to_dict(self, check_only: bool = False) -> Dict[str, Any]:
        if check_only:
            return {
                "ok": True,
                "query_file": self.query_file,
                "title": self.title,
                "human_request": self.human_request,
                "notes": self.notes,
                "results": [
                    {
                        "name": result.name,
                        "dataset": result.dataset,
                        "page_size": result.page_size,
                        "min_rows": result.min_rows,
                        "row_count": result.row_count,
                        "columns": result.columns,
                        "expected_columns": result.expected_columns,
                        "download_url": result.download_url,
                    }
                    for result in self.results
                ],
            }

        if len(self.results) == 1:
            result = self.results[0].to_dict()
            result["title"] = result["title"] or self.title
            result["human_request"] = result["human_request"] or self.human_request
            result["notes"] = result["notes"] or self.notes
            result["results"] = [r.to_dict() for r in self.results]
            return result

        return {
            "title": self.title,
            "human_request": self.human_request,
            "notes": self.notes,
            "results": [r.to_dict() for r in self.results],
        }


_SQL_START_RE = re.compile(r"^\s*(SELECT|WITH)\s", re.IGNORECASE)


def load_query_spec(path: str | Path) -> Dict[str, Any]:
    """Read and parse a saved query spec JSON file."""
    spec_path = Path(path)
    if not spec_path.is_file():
        raise InvalidSpecError(f"query file not found: {spec_path}")
    try:
        with spec_path.open(encoding="utf-8") as f:
            spec = json.load(f)
    except json.JSONDecodeError as exc:
        raise InvalidSpecError(f"query file is not valid JSON: {spec_path}") from exc
    if not isinstance(spec, dict):
        raise InvalidSpecError("query spec must be a JSON object")
    return spec


def parse_query_spec(spec: Mapping[str, Any]) -> List[QuerySpecEntry]:
    """Validate a query spec and normalize it into runnable query entries."""
    if isinstance(spec.get("queries"), list):
        raw_queries = spec["queries"]
        if not raw_queries:
            raise InvalidSpecError(".queries must contain at least one query.")
        names = []
        for raw in raw_queries:
            if not isinstance(raw, Mapping):
                raise InvalidSpecError("every entry in .queries must be an object.")
            name = raw.get("name")
            if not isinstance(name, str) or not name:
                raise InvalidSpecError("every entry in .queries must have a non-empty string name.")
            names.append(name)
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise InvalidSpecError("duplicate query names: " + ", ".join(duplicates))
        entries = [
            _entry_from_obj(raw, top=spec, index=index, multi=True)
            for index, raw in enumerate(raw_queries)
        ]
    else:
        entries = [_entry_from_obj(spec, top=spec, index=0, multi=False)]

    return entries


def run_query_spec_file(
    path: str | Path,
    mcp_client: MCPClient,
    *,
    max_attempts: int = DEFAULT_MAX_QUERY_ATTEMPTS,
) -> QuerySpecRun:
    """Load and run a saved query spec file."""
    spec_path = Path(path)
    spec = load_query_spec(spec_path)
    return run_query_spec(spec, mcp_client, query_file=str(spec_path), max_attempts=max_attempts)


def run_query_spec(
    spec: Mapping[str, Any],
    mcp_client: MCPClient,
    *,
    query_file: Optional[str] = None,
    max_attempts: int = DEFAULT_MAX_QUERY_ATTEMPTS,
) -> QuerySpecRun:
    """Run a parsed query spec through DatasetDBQuery and validate the result."""
    entries = parse_query_spec(spec)
    results = [_run_entry(entry, mcp_client, max_attempts=max_attempts) for entry in entries]
    return QuerySpecRun(
        query_file=query_file,
        title=_string(spec.get("title"), ""),
        human_request=_string(spec.get("human_request"), ""),
        notes=_string(spec.get("notes"), ""),
        results=results,
    )


def format_pretty(run: QuerySpecRun, *, check_only: bool = False) -> str:
    """Human-readable output matching query_run.sh's default format."""
    lines: List[str] = []
    if check_only:
        lines.append(f"OK: {run.query_file or '<query spec>'}")
        for result in run.results:
            lines.append(
                "OK: %s rows=%d min_rows=%d columns=%d expected_columns=%d"
                % (
                    result.name,
                    result.row_count,
                    result.min_rows,
                    len(result.columns),
                    len(result.expected_columns),
                )
            )
        return "\n".join(lines)

    has_multi = len(run.results) > 1
    if has_multi:
        _append_metadata(lines, run.title, run.human_request, run.notes, "")
        if lines:
            lines.append("")

    for index, result in enumerate(run.results):
        if has_multi:
            lines.append(f"Table: {result.name}")
        _append_metadata(
            lines,
            result.title,
            result.human_request,
            result.notes,
            result.download_url,
        )
        lines.append("")
        if not result.rows:
            lines.append("No rows returned.")
        else:
            lines.extend(_delimited_rows(result.columns, result.rows, delimiter="\t"))
        if index < len(run.results) - 1:
            lines.append("")
    return "\n".join(lines)


def format_json(run: QuerySpecRun, *, check_only: bool = False) -> str:
    """Structured JSON output matching query_run.sh --format json."""
    return json.dumps(run.to_dict(check_only=check_only), ensure_ascii=False, indent=2)


def format_tsv(run: QuerySpecRun) -> str:
    return _format_delimited(run, delimiter="\t")


def format_csv(run: QuerySpecRun) -> str:
    return _format_delimited(run, delimiter=",")


def _run_entry(
    entry: QuerySpecEntry,
    mcp_client: MCPClient,
    *,
    max_attempts: int,
) -> QuerySpecResult:
    payload = _call_dataset_db_query(
        mcp_client,
        dataset=entry.dataset,
        query=entry.query,
        page_size=entry.page_size,
        name=entry.name,
        max_attempts=max_attempts,
    )

    warnings = payload.get("warnings")
    if warnings:
        raise QueryWarningError(f"query '{entry.name}' returned warnings: {warnings}")

    rows = payload.get("rows") or []
    if not isinstance(rows, list):
        raise QueryServerError(f"query '{entry.name}' returned a non-list rows payload")
    normalized_rows = [_normalize_row(row, entry.name) for row in rows]

    if len(normalized_rows) < entry.min_rows:
        raise TooFewRowsError(
            "query '%s' returned too few rows: got %d, expected at least %d"
            % (entry.name, len(normalized_rows), entry.min_rows)
        )

    missing = _missing_expected_columns(normalized_rows, entry.expected_columns)
    if missing:
        raise MissingColumnsError(
            "query '%s' result is missing expected column(s): %s"
            % (entry.name, ", ".join(missing))
        )

    columns = entry.columns
    if not columns and normalized_rows:
        columns = list(normalized_rows[0].keys())

    return QuerySpecResult(
        name=entry.name,
        title=entry.title,
        human_request=entry.human_request,
        notes=entry.notes,
        dataset=entry.dataset,
        query=entry.query,
        page_size=entry.page_size,
        min_rows=entry.min_rows,
        columns=columns,
        expected_columns=entry.expected_columns,
        row_count=len(normalized_rows),
        rows=normalized_rows,
        download_url=_string(payload.get("download_url"), ""),
    )


def _call_dataset_db_query(
    mcp_client: MCPClient,
    *,
    dataset: str,
    query: str,
    page_size: int,
    name: str,
    max_attempts: int,
) -> Dict[str, Any]:
    last_exc: Optional[BaseException] = None
    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            raw = mcp_client.call_tool(
                "DatasetDBQuery",
                {"dataset": dataset, "query": query, "page_size": page_size},
            )
            payload = _extract_tool_payload(raw)
            if not isinstance(payload, dict):
                raise QueryServerError(f"query '{name}' returned a non-object payload")
            if payload.get("error") is not None:
                raise QueryServerError(f"query '{name}' server error: {payload['error']}")
            return payload
        except QueryServerError:
            raise
        except Exception as exc:  # noqa: BLE001 - retry transport/adaptor failures
            last_exc = exc
            if attempt >= max(1, max_attempts):
                break
            time.sleep(attempt)
    raise TransportError(f"query '{name}' failed while calling MCP server: {last_exc}")


def _extract_tool_payload(raw: str) -> Any:
    """Handle both LangChain tool text and raw JSON-RPC/SSE response shapes."""
    try:
        data = _json_from_http_body(raw)
    except json.JSONDecodeError as exc:
        raise QueryServerError("MCP response was not valid JSON") from exc

    # query_run.sh receives JSON-RPC envelopes from curl.
    if isinstance(data, dict) and data.get("error") is not None:
        raise QueryServerError(f"MCP server error: {data['error']}")
    structured = _nested_get(data, "result", "structuredContent")
    if structured is not None:
        return structured

    # LangChain MCP tools usually return the tool's structured content directly.
    if isinstance(data, dict) and data.get("structuredContent") is not None:
        return data["structuredContent"]

    return data


def _json_from_http_body(body: str) -> Any:
    """Parse a JSON body or the last `data: ...` event from an SSE body."""
    text = body.strip()
    data_lines = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if data_lines:
        return json.loads(data_lines[-1])
    return json.loads(text)


def _entry_from_obj(
    raw: Mapping[str, Any],
    *,
    top: Mapping[str, Any],
    index: int,
    multi: bool,
) -> QuerySpecEntry:
    name = _string(raw.get("name"), "result")
    dataset = _required_string(raw, "dataset", name)
    query = _required_string(raw, "query", name)
    page_size = _positive_int(_coalesce(raw.get("page_size"), top.get("page_size"), DEFAULT_PAGE_SIZE),
                              "page_size", name)
    min_rows = _non_negative_int(_coalesce(raw.get("min_rows"), top.get("min_rows"), 0),
                                 "min_rows", name)
    if min_rows > page_size:
        raise InvalidSpecError(f"query '{name}' min_rows must not exceed page_size.")

    columns = _string_list(raw.get("columns", []), "columns", name)
    expected_columns = _string_list(_coalesce(raw.get("expected_columns"), columns),
                                    "expected_columns", name)

    _validate_sql(name, query)

    return QuerySpecEntry(
        name=name,
        title=_string(raw.get("title"), ""),
        human_request=_string(raw.get("human_request"), ""),
        notes=_string(raw.get("notes"), ""),
        dataset=dataset,
        query=query,
        page_size=page_size,
        min_rows=min_rows,
        columns=columns,
        expected_columns=expected_columns,
    )


def _validate_sql(name: str, query: str) -> None:
    if not _SQL_START_RE.search(query):
        raise InvalidSpecError(f"query '{name}' must start with SELECT or WITH.")
    if ";" in query:
        raise InvalidSpecError(f"query '{name}' must not contain semicolons.")


def _required_string(raw: Mapping[str, Any], field: str, name: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise InvalidSpecError(f"query '{name}' is missing required field: {field}")
    return value


def _string(value: Any, default: str) -> str:
    return value if isinstance(value, str) else default


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _positive_int(value: Any, field: str, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidSpecError(f"query '{name}' has an invalid {field}.") from exc
    if number < 1:
        raise InvalidSpecError(f"query '{name}' {field} must be positive.")
    return number


def _non_negative_int(value: Any, field: str, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidSpecError(f"query '{name}' has an invalid {field}.") from exc
    if number < 0:
        raise InvalidSpecError(f"query '{name}' {field} must be zero or positive.")
    return number


def _string_list(value: Any, field: str, name: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InvalidSpecError(f"query '{name}' {field} must be a list of strings.")
    return list(value)


def _normalize_row(row: Any, name: str) -> Dict[str, Any]:
    if not isinstance(row, dict):
        raise QueryServerError(f"query '{name}' returned a non-object row")
    return dict(row)


def _missing_expected_columns(rows: Sequence[Mapping[str, Any]], expected: Sequence[str]) -> List[str]:
    if not rows:
        return []
    return [column for column in expected if any(column not in row for row in rows)]


def _nested_get(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _append_metadata(lines: List[str], title: str, human_request: str, notes: str, download_url: str) -> None:
    if title:
        lines.append(title)
    if human_request:
        lines.append(f"Request: {human_request}")
    if notes:
        lines.append(f"Notes: {notes}")
    if download_url:
        lines.append(f"Download: {download_url}")


def _format_delimited(run: QuerySpecRun, *, delimiter: str) -> str:
    if len(run.results) == 1:
        result = run.results[0]
        return "\n".join(_delimited_rows(result.columns, result.rows, delimiter=delimiter))

    columns = _union_columns(run.results)
    rows = [["result_name"] + columns]
    for result in run.results:
        for row in result.rows:
            rows.append([result.name] + [_cell(row.get(column, "")) for column in columns])
    return _write_delimited(rows, delimiter=delimiter)


def _delimited_rows(columns: Sequence[str], rows: Sequence[Mapping[str, Any]], *, delimiter: str) -> List[str]:
    table = [list(columns)]
    for row in rows:
        table.append([_cell(row.get(column, "")) for column in columns])
    return _write_delimited(table, delimiter=delimiter).splitlines()


def _write_delimited(rows: Iterable[Sequence[Any]], *, delimiter: str) -> str:
    output = io.StringIO()
    writer = csv.writer(output, delimiter=delimiter, lineterminator="\n")
    writer.writerows(rows)
    return output.getvalue().rstrip("\n")


def _union_columns(results: Sequence[QuerySpecResult]) -> List[str]:
    columns: List[str] = []
    for result in results:
        for column in result.columns:
            if column not in columns:
                columns.append(column)
    return columns


def _cell(value: Any) -> Any:
    return "" if value is None else value


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a saved BudgetKey query spec through the app's MCP stack."
    )
    parser.add_argument("--check", action="store_true", help="Validate the query and print only status.")
    parser.add_argument(
        "--format",
        "-f",
        choices=("pretty", "json", "tsv", "csv"),
        default="pretty",
        help="Output format.",
    )
    parser.add_argument(
        "--out",
        nargs="?",
        const=True,
        default=False,
        help="Write output to PATH, or next to the query file as result_<query-file-name>.",
    )
    parser.add_argument(
        "--mcp-url",
        default=DEFAULT_MCP_URL,
        help="MCP endpoint URL. Defaults to WIKI_HARNESS_MCP_URL or https://next.obudget.org/mcp.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_MCP_TIMEOUT,
        help="HTTP timeout in seconds. Defaults to WIKI_HARNESS_MCP_TIMEOUT or 180.",
    )
    parser.add_argument("query_file", help="Path to query_optimizer/queries/<query>.json")
    return parser.parse_args(argv)


def _main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        client = HTTPMCPClient(args.mcp_url, args.timeout)
        run = run_query_spec_file(args.query_file, client)
        if args.format == "json":
            output = format_json(run, check_only=args.check)
        elif args.format == "tsv":
            output = format_pretty(run, check_only=True) if args.check else format_tsv(run)
        elif args.format == "csv":
            output = format_pretty(run, check_only=True) if args.check else format_csv(run)
        else:
            output = format_pretty(run, check_only=args.check)

        out_path = _resolve_out_path(args.out, args.query_file)
        if out_path:
            Path(out_path).write_text(output + "\n", encoding="utf-8")
        else:
            print(output)
        return 0
    except QuerySpecError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(f"Error code: {exc.exit_code}", file=sys.stderr)
        return exc.exit_code


def _resolve_out_path(out_arg: Any, query_file: str) -> Optional[str]:
    if out_arg is False:
        return None
    if out_arg is True:
        path = Path(query_file)
        return str(path.with_name("result_" + path.name))
    return str(out_arg)


def main(argv: Optional[Sequence[str]] = None) -> int:
    return _main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
