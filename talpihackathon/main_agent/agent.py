import os
import sys
import argparse
import uuid
import json
import re
from datetime import datetime
from typing import List, Optional, Any, Dict
from mcp_client import MCPClient
from llm_providers import (
    Message,
    GeminiStudioProvider,
    AnthropicProvider,
    VertexAIProvider,
    CLIClaudeProvider,
    LLMProvider
)

from colorama import Fore, Style, init
init()

# Color mappings backed by colorama
class Colors:
    BLUE = Fore.BLUE
    GREEN = Fore.GREEN
    YELLOW = Fore.YELLOW
    RED = Fore.RED
    RESET = Style.RESET_ALL
    BOLD = Style.BRIGHT

def print_agent(text: str):
    print(f"{Colors.BLUE}{Colors.BOLD}[Agent]{Colors.RESET} {text}")

def print_mcp(text: str):
    print(f"{Colors.GREEN}{Colors.BOLD}[MCP]{Colors.RESET} {text}")

def print_llm(text: str):
    print(f"{Colors.YELLOW}{Colors.BOLD}[LLM]{Colors.RESET} {text}")

def print_error(text: str):
    print(f"{Colors.RED}{Colors.BOLD}[Error]{Colors.RESET} {text}", file=sys.stderr)
def get_default_output_path(subject: Optional[str], prompt: Optional[str]) -> str:
    """Generates a default output path under /tmp with subject-timestamp or prompt-timestamp."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if subject:
        # Normalize/clean subject slug
        slug = re.sub(r'[^\w\-_]', '_', subject)
        filename = f"{slug}-{timestamp}.md"
    elif prompt:
        # Use first 3 words of prompt or 'prompt'
        words = [w for w in re.sub(r'[^\w\s]', '', prompt).split() if w][:3]
        slug = "_".join(words) if words else "prompt"
        filename = f"{slug}-{timestamp}.md"
    else:
        filename = f"report-{timestamp}.md"
    return os.path.join("/tmp", filename)



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
        model = model_override or "gemini-2.5-flash"
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
) -> tuple[str, List[Dict[str, Any]]]:
    """Runs the autonomous ReAct reasoning-action loop to answer the prompt.
    
    Returns:
        (final_response, execution_trace)
    """
    
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
    execution_trace: List[Dict[str, Any]] = []
    
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
                
                # Capture to execution trace in memory
                execution_trace.append({
                    "turn": turn,
                    "tool_name": name,
                    "arguments": args_data,
                    "reasoning": response.content or "",
                    "output": tool_output
                })

                # Feed output back to history
                history.append(Message(
                    role="tool",
                    content=tool_output,
                    tool_response_id=call_id,
                    name=name
                ))
            except Exception as e:
                print_error(f"Tool execution failed: {e}")
                
                execution_trace.append({
                    "turn": turn,
                    "tool_name": name,
                    "arguments": args_data,
                    "reasoning": response.content or "",
                    "output": f"Error: {e}"
                })

                history.append(Message(
                    role="tool",
                    content=f"Error executing tool {name}: {e}",
                    tool_response_id=call_id,
                    name=name
                ))

    if turn >= max_turns:
        if response.tool_calls:
            print_agent("Reached maximum iteration limit. Forcing final synthesis turn...")
            history.append(Message(
                role="user",
                content="You have reached the execution limit. Please stop performing further tool calls and compile your final markdown dashboard using all the information gathered so far."
            ))
            try:
                print_agent("Final Turn: Thinking...")
                response = provider.generate(history, tools)
                if response.content:
                    print_llm(f"Response:\n{response.content}")
                    final_response = response.content
            except Exception as e:
                print_error(f"LLM final synthesis generation error: {e}")
        else:
            print_agent("Reached maximum iteration limit.")
    
    return final_response, execution_trace

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
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Output Markdown file path (defaults to /tmp/<subject>-<timestamp>.md)")
    parser.add_argument("--list-tools", action="store_true",
                        help="List all available tools from the MCP server and exit")
    parser.add_argument("--test", "-t", action="store_true",
                        help="Run in test mode (limits execution to 1 loop turn)")
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
            prompt_text = template.replace("{SUBJECT}", args.subject)
        except Exception as e:
            print_error(f"Failed to load subject prompt template from {subject_prompt_path}: {e}")
            sys.exit(1)

    # Determine output path
    output_path = args.output
    if not output_path and not args.list_tools:
        output_path = get_default_output_path(args.subject, args.prompt)

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

        # Inject today's date and model name into system instruction placeholders
        today_str = datetime.now().strftime("%Y-%m-%d")
        model_name = getattr(provider, "model", None) or getattr(provider, "cmd", None) or args.model or "unknown-model"
        
        system_instruction = system_instruction.replace("{TODAY}", today_str).replace("{MODEL}", model_name)

        # Run autonomous loop
        max_turns = 1 if args.test else 10
        final_ans, trace_logs = run_agent_loop(provider, mcp_client, tools, prompt_text, system_instruction, max_turns=max_turns)

        # Save output to Markdown file
        if final_ans and output_path:
            parent_dir = os.path.dirname(output_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            try:
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(final_ans)
                print_agent(f"Saved final markdown response to: {output_path}")
            except Exception as e:
                print_error(f"Failed to save output to {output_path}: {e}")

    finally:
        print_agent("Closing MCP connection...")
        mcp_client.close()

if __name__ == "__main__":
    main()
