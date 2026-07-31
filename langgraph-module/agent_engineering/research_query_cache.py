# -*- coding: utf-8 -*-
"""Saved-query cache for the expensive ReAct research phases.

Phase 2 and Phase 3 used to turn their prompt into SQL on every run through a
model/tool loop. This module lets them pay that cost once, by saving the
successful DatasetDBQuery calls from the ReAct transcript as a query spec. Later
runs execute that spec directly through query_run.py.

The saved files intentionally use the same JSON spec format that
query_optimizer/query_gen.sh writes and query_optimizer/query_run.sh consumes:
top-level title/human_request/notes plus either one query object or a `queries`
array whose entries contain dataset, query, page_size, min_rows and columns.
That keeps manually-generated, query_gen.sh-generated and ReAct-learned specs
interchangeable.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from config import (
    MCP_CALL_TIMEOUT,
    MCP_URL,
    QUERY_CACHE_DIR,
    QUERY_RESEARCH_CACHE_ENABLED,
)
from agent_engineering import query_run


_CACHE_VERSION = 1


@dataclass(frozen=True)
class ResearchQueryCacheResult:
    text: Optional[str]
    messages: list[str]
    used_cache: bool = False


def run_cached(
    *,
    phase_name: str,
    phase_label: str,
    subject: str,
    subject_slug: str,
    today: str,
    system_prompt: str,
    user_message: str,
) -> ResearchQueryCacheResult:
    """Return cached research text, or None when the caller should run ReAct."""
    if not QUERY_RESEARCH_CACHE_ENABLED:
        return ResearchQueryCacheResult(None, ["query research cache disabled"])

    spec_path = _spec_path(subject_slug, phase_name)
    prompt_hash = _cache_prompt_hash(phase_label, subject, today, system_prompt, user_message)
    messages: list[str] = []

    if _matching_cached_spec(spec_path, prompt_hash):
        messages.append(f"query cache hit: {spec_path}")
        cached = _run_saved_spec(spec_path)
        if cached.text is not None:
            return ResearchQueryCacheResult(cached.text, messages + cached.messages, used_cache=True)
        messages.extend(cached.messages)
    else:
        messages.append(f"query cache miss: {spec_path}")

    messages.append("falling back to ReAct agent; successful SQL calls will be cached")
    return ResearchQueryCacheResult(None, messages)


def save_from_react_transcript(
    *,
    phase_name: str,
    phase_label: str,
    subject: str,
    subject_slug: str,
    today: str,
    system_prompt: str,
    user_message: str,
    messages: Sequence[Any],
    final_text: str,
) -> list[str]:
    """Persist successful DatasetDBQuery calls from a completed ReAct run as query_gen.sh-compatible JSON."""
    if not QUERY_RESEARCH_CACHE_ENABLED:
        return ["query research cache disabled; not saving ReAct SQL"]

    spec_path = _spec_path(subject_slug, phase_name)
    prompt_hash = _cache_prompt_hash(phase_label, subject, today, system_prompt, user_message)
    entries = _extract_successful_db_queries(messages)
    if not entries:
        return ["no successful DatasetDBQuery calls found; query cache not populated"]

    spec = _build_spec(
        entries=entries,
        phase_name=phase_name,
        phase_label=phase_label,
        subject=subject,
        subject_slug=subject_slug,
        prompt_hash=prompt_hash,
        final_text=final_text,
    )
    try:
        query_run.parse_query_spec(spec)
    except query_run.QuerySpecError as exc:
        return [f"extracted query spec is invalid; not saving cache: {exc}"]

    spec_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = spec_path.with_suffix(spec_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp_path.replace(spec_path)
    return [f"saved {len(entries)} ReAct SQL query spec(s): {spec_path}"]


def _spec_path(subject_slug: str, phase_name: str) -> Path:
    stem = _safe_slug(f"{subject_slug}_{phase_name}")
    return Path(QUERY_CACHE_DIR) / f"{stem}.json"


def _safe_slug(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return safe or "research_query"


def _cache_prompt_hash(
    phase_label: str,
    subject: str,
    today: str,
    system_prompt: str,
    user_message: str,
) -> str:
    normalized = (
        f"Phase: {phase_label}\n"
        f"Subject: {subject}\n\n"
        "System prompt:\n"
        f"{system_prompt.replace(today, '{TODAY}')}\n\n"
        "User message:\n"
        f"{user_message.replace(today, '{TODAY}')}\n"
    )
    return _prompt_hash(normalized)


def _prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _matching_cached_spec(spec_path: Path, prompt_hash: str) -> bool:
    if not spec_path.is_file():
        return False
    try:
        with spec_path.open(encoding="utf-8") as f:
            spec = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    meta = spec.get("_query_cache") if isinstance(spec, dict) else None
    return (
        isinstance(meta, dict)
        and meta.get("version") == _CACHE_VERSION
        and meta.get("kind") == "research_phase"
        and meta.get("prompt_hash") == prompt_hash
    )


@dataclass(frozen=True)
class _SavedSpecRun:
    text: Optional[str]
    messages: list[str]


def _run_saved_spec(spec_path: Path) -> _SavedSpecRun:
    try:
        client = query_run.HTTPMCPClient(MCP_URL, MCP_CALL_TIMEOUT)
        run = query_run.run_query_spec_file(spec_path, client)
    except query_run.QuerySpecError as exc:
        return _SavedSpecRun(
            None,
            [f"cached query spec failed: {exc} (code {exc.exit_code})"],
        )
    return _SavedSpecRun(query_run.format_pretty(run), [f"ran saved query spec: {spec_path}"])


def _extract_successful_db_queries(messages: Sequence[Any]) -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = []
    extracted: list[dict[str, Any]] = []
    seen_sql: set[tuple[str, str]] = set()

    for msg in messages:
        msg_type = msg.__class__.__name__
        if msg_type == "AIMessage":
            for call in getattr(msg, "tool_calls", None) or []:
                if call.get("name") != "DatasetDBQuery":
                    continue
                args = call.get("args")
                if not isinstance(args, dict):
                    continue
                pending.append({
                    "id": call.get("id"),
                    "dataset": args.get("dataset"),
                    "query": args.get("query"),
                    "page_size": args.get("page_size"),
                })
        elif msg_type == "ToolMessage" and getattr(msg, "name", None) == "DatasetDBQuery":
            call = _pop_matching_call(pending, getattr(msg, "tool_call_id", None))
            if not call:
                continue
            entry = _entry_from_tool_pair(call, getattr(msg, "content", ""))
            if not entry:
                continue
            key = (entry["dataset"], entry["query"])
            if key in seen_sql:
                continue
            seen_sql.add(key)
            extracted.append(entry)

    positive = [entry for entry in extracted if entry["row_count"] > 0]
    if positive:
        return positive
    return extracted[-1:] if extracted else []


def _pop_matching_call(pending: list[dict[str, Any]], tool_call_id: Any) -> Optional[dict[str, Any]]:
    if tool_call_id is not None:
        for index, call in enumerate(pending):
            if call.get("id") == tool_call_id:
                return pending.pop(index)
    return pending.pop(0) if pending else None


def _entry_from_tool_pair(call: Mapping[str, Any], content: Any) -> Optional[dict[str, Any]]:
    dataset = call.get("dataset")
    query = _normalize_sql(call.get("query"))
    if not isinstance(dataset, str) or not dataset or not query:
        return None

    try:
        page_size = int(call.get("page_size") or 50)
    except (TypeError, ValueError):
        page_size = 50

    try:
        payload = _payload_from_tool_content(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("error") is not None:
        return None
    warnings = payload.get("warnings")
    if warnings:
        return None

    rows = payload.get("rows") or []
    if not isinstance(rows, list):
        return None
    columns = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
    return {
        "dataset": dataset,
        "query": query,
        "page_size": page_size,
        "min_rows": 1 if rows else 0,
        "columns": columns,
        "row_count": len(rows),
    }


def _normalize_sql(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    query = value.strip()
    while query.endswith(";"):
        query = query[:-1].rstrip()
    if ";" in query:
        return None
    if not re.match(r"^\s*(SELECT|WITH)\s", query, re.IGNORECASE):
        return None
    return query


def _payload_from_tool_content(content: Any) -> Any:
    if isinstance(content, dict):
        structured = _nested_get(content, "result", "structuredContent")
        if structured is not None:
            return structured
        if content.get("structuredContent") is not None:
            return content["structuredContent"]
        return content
    if isinstance(content, list):
        texts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        if texts:
            content = "".join(texts)
        elif all(isinstance(block, str) for block in content):
            content = "".join(content)
    if isinstance(content, str):
        return query_run._extract_tool_payload(content)
    raise TypeError("unsupported tool content")


def _nested_get(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _build_spec(
    *,
    entries: Sequence[Mapping[str, Any]],
    phase_name: str,
    phase_label: str,
    subject: str,
    subject_slug: str,
    prompt_hash: str,
    final_text: str,
) -> dict[str, Any]:
    queries = []
    for index, entry in enumerate(entries, 1):
        dataset = str(entry["dataset"])
        queries.append({
            "name": _safe_slug(f"{dataset}_{index}"),
            "title": "%s query %d (%d row%s)" % (
                dataset,
                index,
                entry["row_count"],
                "" if entry["row_count"] == 1 else "s",
            ),
            "dataset": dataset,
            "page_size": entry["page_size"],
            "min_rows": entry["min_rows"],
            "columns": entry["columns"],
            "query": entry["query"],
        })

    return {
        "title": f"{phase_label} saved query research for {subject}",
        "human_request": f"{phase_label} research for {subject}",
        "notes": "Generated from successful DatasetDBQuery calls during a ReAct fallback run.",
        "queries": queries,
        "_query_cache": {
            "version": _CACHE_VERSION,
            "kind": "research_phase",
            "source": "react_transcript",
            "phase_name": phase_name,
            "phase_label": phase_label,
            "subject": subject,
            "subject_slug": subject_slug,
            "prompt_hash": prompt_hash,
            "final_text_hash": _prompt_hash(final_text),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }
