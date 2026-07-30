import os
import json
import requests
import subprocess
import time
import sys
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from mcp_client import ToolDefinition

class Message:
    def __init__(self, role: str, content: str, tool_calls: Optional[List[Dict[str, Any]]] = None, tool_response_id: Optional[str] = None, name: Optional[str] = None):
        self.role = role                  # "user", "assistant", "tool"
        self.content = content            # Text content
        self.tool_calls = tool_calls or []  # [{"id": "...", "name": "...", "arguments": "{...}"}]
        self.tool_response_id = tool_response_id  # Used by Claude to match tool result to call ID
        self.name = name                  # Used by Gemini to match tool name

class LLMProvider(ABC):
    @abstractmethod
    def generate(self, history: List[Message], tools: List[ToolDefinition]) -> Message:
        pass

class GeminiStudioProvider(LLMProvider):
    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-3.5-flash"):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("Gemini API key is required (set GEMINI_API_KEY environment variable)")
        self.model = model

    def generate(self, history: List[Message], tools: List[ToolDefinition]) -> Message:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"

        # 1. Map messages to Gemini format
        contents = []
        for msg in history:
            role = "model" if msg.role == "assistant" else "user"
            parts = []

            if msg.content:
                parts.append({"text": msg.content})

            if msg.role == "assistant" and msg.tool_calls:
                for tc in msg.tool_calls:
                    args = json.loads(tc["arguments"]) if isinstance(tc["arguments"], str) else tc["arguments"]
                    parts.append({
                        "functionCall": {
                            "name": tc["name"],
                            "args": args
                        }
                    })

            if msg.role == "tool":
                parts.append({
                    "functionResponse": {
                        "name": msg.name or "unknown",
                        "response": {"result": msg.content}
                    }
                })

            contents.append({
                "role": role,
                "parts": parts
            })

        # 2. Map tools to Gemini format
        gemini_tools = []
        if tools:
            decls = []
            for t in tools:
                decls.append({
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema
                })
            gemini_tools = [{"functionDeclarations": decls}]

        payload = {
            "contents": contents,
        }
        if gemini_tools:
            payload["tools"] = gemini_tools

        headers = {"Content-Type": "application/json"}
        
        max_retries = 5
        backoff = 4
        for attempt in range(max_retries):
            resp = requests.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                break
            if resp.status_code in [429, 502, 503, 504] and attempt < max_retries - 1:
                print(f"[Warning] Gemini API returned error {resp.status_code}. Retrying in {backoff}s...", file=sys.stderr)
                time.sleep(backoff)
                backoff *= 2
            else:
                raise RuntimeError(f"Gemini API returned error {resp.status_code}: {resp.text}")

        resp_data = resp.json()
        candidates = resp_data.get("candidates", [])
        if not candidates:
            raise RuntimeError("No candidates returned by Gemini API")

        candidate_content = candidates[0].get("content", {})
        resp_msg = Message(role="assistant", content="")

        for part in candidate_content.get("parts", []):
            if "text" in part:
                resp_msg.content += part["text"]
            if "functionCall" in part:
                fc = part["functionCall"]
                resp_msg.tool_calls.append({
                    "id": fc.get("name"), # Gemini doesn't use random call IDs, name is identifier
                    "name": fc["name"],
                    "arguments": fc.get("args", {})
                })

        return resp_msg


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: Optional[str] = None, model: str = "claude-3-5-sonnet-latest"):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("Anthropic API key is required (set ANTHROPIC_API_KEY environment variable)")
        self.model = model

    def generate(self, history: List[Message], tools: List[ToolDefinition]) -> Message:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01"
        }

        # Map messages to Claude format
        messages = []
        for msg in history:
            if msg.role == "assistant":
                content = []
                if msg.content:
                    content.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    args = json.loads(tc["arguments"]) if isinstance(tc["arguments"], str) else tc["arguments"]
                    content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": args
                    })
                messages.append({"role": "assistant", "content": content})
            elif msg.role == "tool":
                # A tool response in Claude is grouped in a user message containing tool_result content blocks
                content = [{
                    "type": "tool_result",
                    "tool_use_id": msg.tool_response_id,
                    "content": msg.content
                }]
                messages.append({"role": "user", "content": content})
            else:
                messages.append({"role": "user", "content": msg.content})

        # Map tools to Claude format
        claude_tools = []
        for t in tools:
            claude_tools.append({
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema
            })

        payload = {
            "model": self.model,
            "max_tokens": 4000,
            "messages": messages
        }
        if claude_tools:
            payload["tools"] = claude_tools

        resp = requests.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Anthropic API returned error {resp.status_code}: {resp.text}")

        resp_data = resp.json()
        resp_msg = Message(role="assistant", content="")

        for content_block in resp_data.get("content", []):
            if content_block.get("type") == "text":
                resp_msg.content += content_block.get("text", "")
            elif content_block.get("type") == "tool_use":
                resp_msg.tool_calls.append({
                    "id": content_block["id"],
                    "name": content_block["name"],
                    "arguments": content_block["input"]
                })

        return resp_msg


class VertexAIProvider(LLMProvider):
    def __init__(self, project_id: Optional[str] = None, region: Optional[str] = None, model: str = "gemini-2.5-flash"):
        self.project_id = project_id or os.getenv("GCP_PROJECT")
        self.region = region or os.getenv("GCP_REGION") or "europe-west1"
        self.model = model
        
        # Load GCP credentials
        try:
            import google.auth
            import google.auth.transport.requests
            self.credentials, default_project = google.auth.default()
            self.project_id = self.project_id or default_project
            self.auth_req = google.auth.transport.requests.Request()
        except ImportError:
            raise ImportError("VertexAIProvider requires 'google-auth' library. Install it or use another provider.")

        if not self.project_id:
            raise ValueError("GCP project ID is required for Vertex AI. Set GCP_PROJECT environment variable.")

    def _get_access_token(self) -> str:
        self.credentials.refresh(self.auth_req)
        return self.credentials.token

    def generate(self, history: List[Message], tools: List[ToolDefinition]) -> Message:
        url = f"https://{self.region}-aiplatform.googleapis.com/v1/projects/{self.project_id}/locations/{self.region}/publishers/google/models/{self.model}:generateContent"
        
        token = self._get_access_token()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }

        # Vertex AI payload is structurally identical to Gemini Studio
        contents = []
        for msg in history:
            role = "model" if msg.role == "assistant" else "user"
            parts = []

            if msg.content:
                parts.append({"text": msg.content})

            if msg.role == "assistant" and msg.tool_calls:
                for tc in msg.tool_calls:
                    args = json.loads(tc["arguments"]) if isinstance(tc["arguments"], str) else tc["arguments"]
                    parts.append({
                        "functionCall": {
                            "name": tc["name"],
                            "args": args
                        }
                    })

            if msg.role == "tool":
                parts.append({
                    "functionResponse": {
                        "name": msg.name or "unknown",
                        "response": {"result": msg.content}
                    }
                })

            contents.append({
                "role": role,
                "parts": parts
            })

        gemini_tools = []
        if tools:
            decls = []
            for t in tools:
                decls.append({
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema
                })
            gemini_tools = [{"functionDeclarations": decls}]

        payload = {
            "contents": contents,
        }
        if gemini_tools:
            payload["tools"] = gemini_tools

        max_retries = 5
        backoff = 4
        for attempt in range(max_retries):
            resp = requests.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                break
            if resp.status_code in [429, 502, 503, 504] and attempt < max_retries - 1:
                print(f"[Warning] Vertex AI returned error {resp.status_code}. Retrying in {backoff}s...", file=sys.stderr)
                time.sleep(backoff)
                backoff *= 2
            else:
                raise RuntimeError(f"Vertex AI returned error {resp.status_code}: {resp.text}")

        resp_data = resp.json()
        candidates = resp_data.get("candidates", [])
        if not candidates:
            raise RuntimeError("No candidates returned by Vertex AI")

        candidate_content = candidates[0].get("content", {})
        resp_msg = Message(role="assistant", content="")

        for part in candidate_content.get("parts", []):
            if "text" in part:
                resp_msg.content += part["text"]
            if "functionCall" in part:
                fc = part["functionCall"]
                resp_msg.tool_calls.append({
                    "id": fc.get("name"),
                    "name": fc["name"],
                    "arguments": fc.get("args", {})
                })

        return resp_msg


class CLIClaudeProvider(LLMProvider):
    def __init__(self, cmd: str = "claude", agent: str = "mk-researcher"):
        self.cmd = cmd
        self.agent = agent

    def _build_prompt(self, history: List[Message]) -> str:
        prompt_parts = []
        for msg in history:
            if msg.role == "user":
                prompt_parts.append(f"User: {msg.content}")
            elif msg.role == "assistant":
                prompt_parts.append(f"Assistant: {msg.content}")
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        prompt_parts.append(f"Assistant requested tool call: {tc['name']} with arguments {tc['arguments']}")
            elif msg.role == "tool":
                prompt_parts.append(f"Tool {msg.name} returned:\n{msg.content}")
        return "\n\n".join(prompt_parts)

    def generate(self, history: List[Message], tools: List[ToolDefinition]) -> Message:
        allowed_tools_str = json.dumps([
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema
            } for t in tools
        ])

        prompt = self._build_prompt(history)

        cmd = [
            self.cmd,
            "-p", prompt,
            "--agent", self.agent,
            "--allowedTools", allowed_tools_str,
            "--permission-mode", "acceptEdits",
            "--output-format", "json"
        ]

        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                env=env, timeout=120
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("CLI Claude invocation timed out (exceeded 120s)")
        except FileNotFoundError:
            raise RuntimeError(
                f"CLI command '{self.cmd}' was not found. Please ensure it is installed and in your PATH."
            )

        if proc.returncode != 0:
            raise RuntimeError(
                f"CLI Claude invocation returned exit code {proc.returncode}.\n"
                f"Stdout: {proc.stdout}\nStderr: {proc.stderr}"
            )

        try:
            data = json.loads(proc.stdout.strip())
        except Exception as e:
            raise RuntimeError(
                f"Failed to parse JSON output from CLI Claude. Raw output:\n{proc.stdout}\nError: {e}"
            )

        content_text = ""
        tool_calls = []

        if isinstance(data, dict):
            raw_content = data.get("content", "")
            if isinstance(raw_content, str):
                content_text = raw_content
            elif isinstance(raw_content, list):
                for block in raw_content:
                    if block.get("type") == "text":
                        content_text += block.get("text", "")
                    elif block.get("type") == "tool_use":
                        tool_calls.append({
                            "id": block.get("id") or block.get("name"),
                            "name": block.get("name"),
                            "arguments": block.get("input", {})
                        })
            
            raw_tool_calls = data.get("tool_calls", [])
            if isinstance(raw_tool_calls, list):
                for tc in raw_tool_calls:
                    tool_calls.append({
                        "id": tc.get("id") or tc.get("name"),
                        "name": tc.get("name"),
                        "arguments": tc.get("arguments", tc.get("input", {}))
                    })
        elif isinstance(data, list):
            for block in data:
                if block.get("type") == "text":
                    content_text += block.get("text", "")
                elif block.get("type") == "tool_use":
                    tool_calls.append({
                        "id": block.get("id") or block.get("name"),
                        "name": block.get("name"),
                        "arguments": block.get("input", {})
                    })

        return Message(
            role="assistant",
            content=content_text if content_text else None,
            tool_calls=tool_calls if tool_calls else None
        )
