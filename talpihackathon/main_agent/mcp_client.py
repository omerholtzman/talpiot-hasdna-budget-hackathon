import json
import threading
import requests
from typing import List, Dict, Any, Optional

class ToolDefinition:
    def __init__(self, name: str, description: str, input_schema: Dict[str, Any]):
        self.name = name
        self.description = description
        self.input_schema = input_schema

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema
        }

class MCPClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.session_id: Optional[str] = None
        self.close_event = threading.Event()
        self.sse_thread: Optional[threading.Thread] = None

    def connect(self):
        # 1. Handshake to get Mcp-Session-Id
        # Note: The server may return 406 Not Acceptable since this handshake GET does not request
        # text/event-stream, but it still generates and returns the session ID in headers.
        resp = requests.get(self.base_url, timeout=30)
        self.session_id = resp.headers.get("Mcp-Session-Id")
        if not self.session_id:
            raise RuntimeError(f"Handshake failed: missing Mcp-Session-Id header (status {resp.status_code}): {resp.text}")

        # 2. Establish SSE connection in a background thread to keep it alive
        self.sse_thread = threading.Thread(target=self._read_sse, daemon=True)
        self.sse_thread.start()

    def _read_sse(self):
        headers = {
            "Accept": "application/json, text/event-stream",
            "Mcp-Session-Id": self.session_id
        }
        try:
            with requests.get(self.base_url, headers=headers, stream=True, timeout=30) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if self.close_event.is_set():
                        break
                    # We discard SSE messages because the server responds synchronously over POST.
                    # This background read keeps the connection open and prevents buffer overflow.
                    pass
        except Exception:
            pass

    def _send_post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Mcp-Session-Id": self.session_id
        }
        resp = requests.post(self.base_url, json=payload, headers=headers, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"POST request failed with status {resp.status_code}: {resp.text}")

        resp.encoding = 'utf-8'
        # Parse SSE-wrapped synchronous response body
        return self._parse_sse_response(resp.text)

    def _parse_sse_response(self, text: str) -> Dict[str, Any]:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                json_str = line[len("data:"):].strip()
                return json.loads(json_str)
        raise ValueError(f"No 'data:' field found in SSE response body: {text}")

    def initialize(self):
        init_payload = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "id": 1,
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "python-mcp-agent",
                    "version": "1.0.0"
                }
            }
        }
        self._send_post(init_payload)

        # Send initialized notification
        initialized_notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Mcp-Session-Id": self.session_id
        }
        try:
            requests.post(self.base_url, json=initialized_notification, headers=headers, timeout=10)
        except Exception:
            pass

    def list_tools(self) -> List[ToolDefinition]:
        list_payload = {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 2,
            "params": {}
        }
        resp_data = self._send_post(list_payload)
        
        tools = []
        result = resp_data.get("result", {})
        for t in result.get("tools", []):
            tools.append(ToolDefinition(
                name=t["name"],
                description=t["description"],
                input_schema=t["inputSchema"]
            ))
        return tools

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        call_payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 3,
            "params": {
                "name": name,
                "arguments": arguments
            }
        }
        resp_data = self._send_post(call_payload)
        
        result = resp_data.get("result", {})
        if result.get("isError"):
            raise RuntimeError(f"Tool returned error response: {result.get('content')}")
            
        contents = result.get("content", [])
        if not contents:
            raise RuntimeError("Tool returned empty response")
            
        output = ""
        for content in contents:
            if content.get("type") == "text":
                output += content.get("text", "")
        return output

    def close(self):
        self.close_event.set()
