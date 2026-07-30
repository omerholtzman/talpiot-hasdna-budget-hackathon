import os
import sys
import argparse
import uuid
import json
from typing import List
from mcp_client import MCPClient
from llm_providers import (
    Message,
    GeminiStudioProvider,
    AnthropicProvider,
    VertexAIProvider,
    LLMProvider
)

# Color ANSI escapes for clean formatting
class Colors:
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_agent(text: str):
    print(f"{Colors.BLUE}{Colors.BOLD}[Agent]{Colors.RESET} {text}")

def print_mcp(text: str):
    print(f"{Colors.GREEN}{Colors.BOLD}[MCP]{Colors.RESET} {text}")

def print_llm(text: str):
    print(f"{Colors.YELLOW}{Colors.BOLD}[LLM]{Colors.RESET} {text}")

def print_error(text: str):
    print(f"{Colors.RED}{Colors.BOLD}[Error]{Colors.RESET} {text}", file=sys.stderr)

def main():
    parser = argparse.ArgumentParser(description="BudgetKey MCP Autonomous Agent")
    parser.add_argument("prompt", type=str, nargs="?", default=None,
                        help="The prompt/question to ask the budget database")
    parser.add_argument("--provider", type=str, default="gemini", choices=["gemini", "anthropic", "vertex"],
                        help="LLM Provider to use (gemini, anthropic, vertex)")
    parser.add_argument("--model", type=str, default=None,
                        help="Model name override")
    parser.add_argument("--mcp-url", type=str, default="https://next.obudget.org/mcp",
                        help="BudgetKey MCP server URL")
    parser.add_argument("--list-tools", action="store_true",
                        help="List all available tools from the MCP server and exit")
    args = parser.parse_args()

    if not args.list_tools and not args.prompt:
        parser.error("either prompt must be specified or --list-tools flag must be set")

    # 1. Connect to MCP Server
    print_agent(f"Connecting to BudgetKey MCP server at {args.mcp_url}...")
    mcp_client = MCPClient(args.mcp_url)
    try:
        mcp_client.connect()
    except Exception as e:
        print_error(f"Failed to connect to MCP server: {e}")
        sys.exit(1)

    try:
        print_agent("Initializing session...")
        mcp_client.initialize()

        print_agent("Fetching available tools...")
        tools = mcp_client.list_tools()
        print_agent(f"Successfully loaded {len(tools)} tools from MCP:")
        for t in tools:
            print_mcp(f"  - {t.name}: {t.description.splitlines()[0]}")

        if args.list_tools:
            return

        # 2. Setup LLM Provider
        print_agent(f"Setting up provider: {args.provider}...")
        provider: LLMProvider
        try:
            if args.provider == "gemini":
                model_name = args.model or "gemini-2.5-flash"
                provider = GeminiStudioProvider(model=model_name)
            elif args.provider == "anthropic":
                model_name = args.model or "claude-3-5-sonnet-latest"
                provider = AnthropicProvider(model=model_name)
            elif args.provider == "vertex":
                model_name = args.model or "gemini-2.5-flash"
                provider = VertexAIProvider(model=model_name)
        except Exception as e:
            print_error(f"Failed to initialize LLM provider: {e}")
            mcp_client.close()
            sys.exit(1)

        # 3. Initialize Conversation History
        # We append a robust system instruction at the beginning
        system_instruction = (
            "You are an expert data researcher, helping to find information on issues related "
            "to the State Budget of Israel. You provide information from the Israeli budget book "
            "(ספר התקציב הישראלי), budgetary support data (נתוני תמיכות תקציביות), information on "
            "contracts (התקשרויות), and tenders (מכרזים).\n\n"
            "You communicate efficiently in Hebrew.\n"
            "You use ONLY the information obtained through the tools provided and no other information.\n"
            "The current year is 2025. Budget data is available from 1997 to 2025.\n\n"
            "Tool guidelines:\n"
            "1. ALWAYS call DatasetInfo first to understand the dataset structure and columns before running queries.\n"
            "2. If you need text identifiers (supplier name, budget codes), search using DatasetFullTextSearch first.\n"
            "3. Finally, execute SQL query using DatasetDBQuery to get results. Always include 'item_url' in SELECT.\n"
        )
        
        history: List[Message] = [
            Message(role="user", content=f"System Instruction:\n{system_instruction}"),
            Message(role="assistant", content="הבנתי. אני מוכן לעזור לך למצוא מידע בתקציב המדינה. כיצד אוכל לסייע?")
        ]

        # Add the actual user query
        history.append(Message(role="user", content=args.prompt))
        print_agent(f"User Query: {args.prompt}")

        # 4. Autonomous Reasoning & Action (ReAct) Loop
        # The agent loops up to max_turns to allow multi-step queries (e.g. getSchema -> textSearch -> DBQuery).
        max_turns = 10
        turn = 0
        while turn < max_turns:
            turn += 1
            print_agent(f"Turn {turn}: Thinking...")
            
            # --- REASONING PHASE ---
            # Send the entire conversation history (including previous tool outputs)
            # along with the list of available tools to the LLM.
            try:
                response = provider.generate(history, tools)
            except Exception as e:
                print_error(f"LLM generation error: {e}")
                break

            # Print LLM text reasoning/explanation if it generated one
            if response.content:
                print_llm(f"Response:\n{response.content}")

            # Append the LLM's response (reasoning + requested tool calls) to history
            history.append(response)

            # --- DECISION/TERMINATION CHECK ---
            # If the model did not request any tool calls, it has finished reasoning
            # and compiled the final output. We can terminate the loop here.
            if not response.tool_calls:
                print_agent("Task completed. No more tool calls requested.")
                break

            # --- ACTION PHASE ---
            # Loop through and execute each tool call requested by the LLM.
            print_agent(f"Model requested {len(response.tool_calls)} tool call(s):")
            for tc in response.tool_calls:
                name = tc["name"]
                args_data = tc["arguments"]
                call_id = tc.get("id") or str(uuid.uuid4())
                
                print_mcp(f"Executing tool {Colors.BOLD}{name}{Colors.RESET} with arguments: {args_data}")
                
                try:
                    # Call the local MCP client synchronously (POST request)
                    tool_output = mcp_client.call_tool(name, args_data)
                    
                    # Pretty-print Hebrew JSON output in the console preview
                    try:
                        parsed_output = json.loads(tool_output)
                        pretty_output = json.dumps(parsed_output, indent=2, ensure_ascii=False)
                        preview = pretty_output[:500] + "..." if len(pretty_output) > 500 else pretty_output
                    except Exception:
                        preview = tool_output[:300] + "..." if len(tool_output) > 300 else tool_output
                    print_mcp(f"Output Preview:\n{preview}")
                    
                    # Append the tool's raw output back to history so the LLM can read it in the next turn
                    history.append(Message(
                        role="tool",
                        content=tool_output,
                        tool_response_id=call_id,
                        name=name
                    ))
                except Exception as e:
                    print_error(f"Tool execution failed: {e}")
                    # Feed the error message back to the LLM so it can attempt self-correction
                    history.append(Message(
                        role="tool",
                        content=f"Error executing tool {name}: {e}",
                        tool_response_id=call_id,
                        name=name
                    ))

        if turn >= max_turns:
            print_agent("Reached maximum iteration limit.")

    finally:
        print_agent("Closing MCP connection...")
        mcp_client.close()

if __name__ == "__main__":
    main()
