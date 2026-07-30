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
from langchain_mcp_adapters.client import MultiServerMCPClient

from config import MCP_URL

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
