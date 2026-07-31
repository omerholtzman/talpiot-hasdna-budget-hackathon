"""
Connects to the obudget MCP server and exposes its tools — DatasetInfo,
DatasetFullTextSearch, DatasetDBQuery — as LangChain tools that a
LangGraph agent can call directly.

All three research phases (budget / contracts / decisions) talk to the
*same* MCP server and the *same* three generic tools; only the system
prompt for each phase tells the model which dataset, columns, and filters
to use. So we only need one loader, shared by every phase, rather than
one per phase.

NOTE FOR MAINTAINERS: this file was written without the ability to reach
the live MCP server (sandboxed authoring environment, no network egress),
so it has NOT been executed against https://next.obudget.org/mcp. Before
relying on it:
  1. Confirm the transport. "streamable_http" is the modern MCP HTTP
     transport and is the most likely fit for a URL ending in `/mcp`, but
     if the server instead speaks the older SSE transport, change
     `transport` below to "sse".
  2. Confirm the `langchain-mcp-adapters` API against the version you
     install — `MultiServerMCPClient(...).get_tools()` is current as of
     mid-2025, but this library moves fast.
"""
import asyncio
import json

from langchain_mcp_adapters.client import MultiServerMCPClient

from config import MCP_URL, MCP_CALL_TIMEOUT

# Module-level cache: phases 1-3 run concurrently and can safely share one
# connection/tool-list rather than each opening its own.
_client: MultiServerMCPClient | None = None


async def get_mcp_tools():
    """Return the list of LangChain-compatible tools exposed by the MCP server."""
    global _client
    if _client is None:
        _client = MultiServerMCPClient(
            {
                "obudget": {
                    "url": MCP_URL,
                    "transport": "streamable_http",  # see module docstring if this needs to be "sse"
                }
            }
        )
    return await _client.get_tools()


class SyncMCPBridge:
    """A blocking `call_tool(name, args) -> str` on top of the async MCP tools.

    Phase 1 is a deterministic SQL pipeline (see pipeline.py), not an agent loop:
    it is ordinary synchronous code that issues dozens of queries and fans its
    model calls out over a thread pool. Rewriting it as async would spread
    `await` through every step for no benefit, so instead the node runs it in a
    worker thread and hands it this bridge, which posts
    each tool call back to the event loop it came from.

    Only safe to call from a thread OTHER than the one running `loop` — which is
    exactly the arrangement above. Calling it on the loop's own thread would
    deadlock, so that raises instead.
    """

    def __init__(self, tools, loop: asyncio.AbstractEventLoop):
        self._tools = {t.name: t for t in tools}
        self._loop = loop

    def call_tool(self, name: str, arguments: dict) -> str:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(
                f"MCP server exposes no tool named '{name}'. Available: "
                + ", ".join(sorted(self._tools))
            )
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            raise RuntimeError(
                "SyncMCPBridge.call_tool was called on the event loop's own thread; "
                "run the pipeline on a worker thread."
            )

        future = asyncio.run_coroutine_threadsafe(tool.ainvoke(arguments), self._loop)
        result = future.result(timeout=MCP_CALL_TIMEOUT)

        return _as_text(result)


def _as_text(result) -> str:
    """Reduce whatever ainvoke() handed back to the tool's own text payload.

    Callers parse JSON out of this string, so every wrapper the adapter may add has
    to come off first. Three shapes show up in practice:

      * a plain str - already the payload;
      * a (content, artifact) tuple, from a tool declared with
        response_format="content_and_artifact";
      * a list of MCP content blocks, [{"type": "text", "text": "..."}, ...] -
        this is what the installed langchain-mcp-adapters actually returns for
        DatasetDBQuery, and it is the shape that matters: json.dumps-ing it
        produced a JSON *array*, so budget_api.query's data.get("warnings")
        blew up with "'list' object has no attribute 'get'" on the very first
        SQL call of the pipeline.

    A server may split one payload across several text blocks, so they are
    concatenated rather than indexed - taking [0] would truncate the JSON.
    """
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, str):
        return result
    if isinstance(result, dict) and isinstance(result.get("text"), str):
        return result["text"]
    if isinstance(result, list):
        texts = [b["text"] for b in result
                 if isinstance(b, dict) and isinstance(b.get("text"), str)]
        if texts:
            return "".join(texts)
        if all(isinstance(b, str) for b in result):
            return "".join(result)
    return json.dumps(result, ensure_ascii=False)
