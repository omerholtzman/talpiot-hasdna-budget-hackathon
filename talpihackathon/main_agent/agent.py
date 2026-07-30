import os
import sys
import argparse
import uuid
import json
from typing import List, Optional, Any
from mcp_client import MCPClient
from llm_providers import (
    Message,
    GeminiStudioProvider,
    AnthropicProvider,
    VertexAIProvider,
    CLIClaudeProvider,
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

def fix_bidi(text: str) -> str:
    """Applies Bidirectional algorithm to fix RTL languages (like Hebrew) in LTR terminals."""
    try:
        from bidi.algorithm import get_display
        return get_display(text)
    except ImportError:
        return text

def print_agent(text: str):
    print(f"{Colors.BLUE}{Colors.BOLD}[Agent]{Colors.RESET} {fix_bidi(text)}")

def print_mcp(text: str):
    print(f"{Colors.GREEN}{Colors.BOLD}[MCP]{Colors.RESET} {fix_bidi(text)}")

def print_llm(text: str):
    print(f"{Colors.YELLOW}{Colors.BOLD}[LLM]{Colors.RESET} {fix_bidi(text)}")

def print_error(text: str):
    print(f"{Colors.RED}{Colors.BOLD}[Error]{Colors.RESET} {fix_bidi(text)}", file=sys.stderr)


def load_system_instruction(file_path: str) -> str:
    """Reads system instructions from the text file. Falls back to a default if missing."""
    default_instruction = (
        "You are an expert data researcher, helping to find information on issues related "
        "to the State Budget of Israel. You communicate efficiently in Hebrew. "
        "Use ONLY the tools provided to query database schemas and execute SQL queries."
    )
    if not os.path.exists(file_path):
        return default_instruction
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        print_error(f"Warning: Failed to load system instruction from {file_path}: {e}")
        return default_instruction

def setup_mcp(mcp_url: str) -> MCPClient:
    """Connects to and initializes the MCP server session."""
    print_agent(f"Connecting to BudgetKey MCP server at {mcp_url}...")
    mcp_client = MCPClient(mcp_url)
    mcp_client.connect()
    print_agent("Initializing session...")
    mcp_client.initialize()
    return mcp_client

def setup_llm_provider(provider_name: str, model_override: Optional[str] = None) -> LLMProvider:
    """Instantiates the selected LLM provider adapter."""
    print_agent(f"Setting up provider: {provider_name}...")
    if provider_name == "gemini":
        model = model_override or "gemini-3.5-flash"
        return GeminiStudioProvider(model=model)
    elif provider_name == "anthropic":
        model = model_override or "claude-3-5-sonnet-latest"
        return AnthropicProvider(model=model)
    elif provider_name == "vertex":
        model = model_override or "gemini-3.5-flash"
        return VertexAIProvider(model=model)
    elif provider_name == "cli-claude":
        cmd_name = model_override or "claude"
        return CLIClaudeProvider(cmd=cmd_name)
    else:
        raise ValueError(f"Unknown provider: {provider_name}")

def run_agent_loop(
    provider: LLMProvider,
    mcp_client: MCPClient,
    tools: List[Any],
    prompt: str,
    system_instruction: str,
    max_turns: int = 10
) -> str:
    """Runs the autonomous ReAct reasoning-action loop to answer the prompt."""
    
    # 1. Initialize History with instructions & user query
    history: List[Message] = [
        Message(role="user", content=f"System Instruction:\n{system_instruction}"),
        Message(role="assistant", content="הבנתי. אני מוכן לעזור לך למצוא מידע בתקציב המדינה. כיצד אוכל לסייע?")
    ]
    history.append(Message(role="user", content=prompt))
    print_agent(f"User Query: {prompt}")

    # 2. Loop Execution
    turn = 0
    final_response = ""
    while turn < max_turns:
        turn += 1
        print_agent(f"Turn {turn}: Thinking...")
        
        # Call LLM reasoning
        try:
            response = provider.generate(history, tools)
        except Exception as e:
            print_error(f"LLM generation error: {e}")
            break
        if response.content:
            print_llm(f"Response:\n{response.content}")
            final_response = response.content

        history.append(response)

        # Termination check
        if not response.tool_calls:
            print_agent("Task completed. No more tool calls requested.")
            break

        # Action execution phase
        print_agent(f"Model requested {len(response.tool_calls)} tool call(s):")
        for tc in response.tool_calls:
            name = tc["name"]
            args_data = tc["arguments"]
            call_id = tc.get("id") or str(uuid.uuid4())
            
            print_mcp(f"Executing tool {Colors.BOLD}{name}{Colors.RESET} with arguments: {args_data}")
            
            try:
                # Call tool on MCP server
                tool_output = mcp_client.call_tool(name, args_data)
                
                # Console output preview formatting
                try:
                    parsed_output = json.loads(tool_output)
                    pretty_output = json.dumps(parsed_output, indent=2, ensure_ascii=False)
                    preview = pretty_output[:500] + "..." if len(pretty_output) > 500 else pretty_output
                except Exception:
                    preview = tool_output[:300] + "..." if len(tool_output) > 300 else tool_output
                print_mcp(f"Output Preview:\n{preview}")
                
                # Feed output back to history
                history.append(Message(
                    role="tool",
                    content=tool_output,
                    tool_response_id=call_id,
                    name=name
                ))
            except Exception as e:
                print_error(f"Tool execution failed: {e}")
                history.append(Message(
                    role="tool",
                    content=f"Error executing tool {name}: {e}",
                    tool_response_id=call_id,
                    name=name
                ))

    if turn >= max_turns:
        print_agent("Reached maximum iteration limit.")
    
    return final_response

def main():
    parser = argparse.ArgumentParser(description="BudgetKey MCP Autonomous Agent")
    parser.add_argument("--prompt", type=str, default=None,
                        help="The prompt/question to ask the budget database")
    parser.add_argument("--subject", type=str, default=None,
                        help="The subject to query budget information for")
    parser.add_argument("--provider", type=str, default="gemini", choices=["gemini", "anthropic", "vertex", "cli-claude"],
                        help="LLM Provider to use (gemini, anthropic, vertex, cli-claude)")
    parser.add_argument("--model", type=str, default=None,
                        help="Model name override")
    parser.add_argument("--mcp-url", type=str, default="https://next.obudget.org/mcp",
                        help="BudgetKey MCP server URL")
    parser.add_argument("--list-tools", action="store_true",
                        help="List all available tools from the MCP server and exit")
    args = parser.parse_args()

    if not args.list_tools and not args.prompt and not args.subject:
        parser.error("either --prompt, --subject, or --list-tools flag must be set")
    if args.prompt and args.subject:
        parser.error("cannot specify both --prompt and --subject")

    # Load system instructions from file
    instruction_path = os.path.join(os.path.dirname(__file__), "instructions", "system_instruction.txt")
    system_instruction = load_system_instruction(instruction_path)

    # Determine prompt text
    prompt_text = ""
    if args.prompt:
        prompt_text = args.prompt
    elif args.subject:
        subject_prompt_path = os.path.join(os.path.dirname(__file__), "instructions", "subject_prompt.txt")
        try:
            with open(subject_prompt_path, "r", encoding="utf-8") as f:
                template = f.read().strip()
            prompt_text = template.format(subject=args.subject)
        except Exception as e:
            print_error(f"Failed to load subject prompt template from {subject_prompt_path}: {e}")
            sys.exit(1)

    # Initialize MCP Client
    try:
        mcp_client = setup_mcp(args.mcp_url)
    except Exception as e:
        print_error(f"Failed to connect/initialize MCP server: {e}")
        sys.exit(1)

    try:
        print_agent("Fetching available tools...")
        tools = mcp_client.list_tools()
        print_agent(f"Successfully loaded {len(tools)} tools from MCP:")
        for t in tools:
            print_mcp(f"  - {t.name}: {t.description.splitlines()[0]}")

        if args.list_tools:
            return

        # Setup LLM Provider
        try:
            provider = setup_llm_provider(args.provider, args.model)
        except Exception as e:
            print_error(f"Failed to initialize LLM provider: {e}")
            sys.exit(1)

        # Run autonomous loop
        run_agent_loop(provider, mcp_client, tools, prompt_text, system_instruction)

    finally:
        print_agent("Closing MCP connection...")
        mcp_client.close()

if __name__ == "__main__":
    main()
