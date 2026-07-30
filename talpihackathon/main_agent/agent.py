import os
import sys
import concurrent.futures
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
    CYAN = Fore.CYAN
    MAGENTA = Fore.MAGENTA
    RESET = Style.RESET_ALL
    BOLD = Style.BRIGHT

def fix_bidi(text: str) -> str:
    """Formats Hebrew/RTL text for LTR terminals."""
    try:
        from bidi.algorithm import get_display
        return get_display(text)
    except ImportError:
        return text

def print_agent(text: str):
    print(f"{Colors.BLUE}{Colors.BOLD}[Orchestrator]{Colors.RESET} {fix_bidi(text)}")

def print_mcp(text: str):
    print(f"{Colors.GREEN}{Colors.BOLD}[MCP]{Colors.RESET} {fix_bidi(text)}")

def print_llm(text: str):
    print(f"{Colors.YELLOW}{Colors.BOLD}[LLM]{Colors.RESET} {fix_bidi(text)}")

def print_error(text: str):
    print(f"{Colors.RED}{Colors.BOLD}[Error]{Colors.RESET} {fix_bidi(text)}", file=sys.stderr)

def clean_markdown_fences(content: str) -> str:
    """Removes leading/trailing code block fences from the content and the frontmatter."""
    content = content.strip()
    
    # Case 1: The entire content is wrapped in a single outer code fence
    # e.g. starting with ```markdown and ending with ```
    if content.startswith("```") and content.endswith("```"):
        first_newline = content.find("\n")
        if first_newline != -1:
            content = content[first_newline:].strip()
        if content.endswith("```"):
            content = content[:-3].strip()
            
    # Case 2: Only the frontmatter is wrapped in code fences
    # e.g. ```yaml\n---\n...\n---\n```\n# Document Title
    # We strip the wrapping fences from around the frontmatter block
    content = re.sub(
        r"^```[a-zA-Z]*\s*\n(---[\s\S]*?\n---)\s*\n```",
        r"\1",
        content
    )
    
    return content

def get_default_output_path(subject: Optional[str]) -> str:
    """Generates a default output path under output_examples with subject-timestamp."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if subject:
        # Normalize/clean subject slug
        slug = re.sub(r'[^\w\-_]', '_', subject)
        run_name = f"{slug}-{timestamp}"
        filename = f"{slug}.md"
    else:
        run_name = f"report-{timestamp}"
        filename = "report.md"
    
    base_dir = os.path.join(os.path.dirname(__file__), "output_examples")
    output_dir = os.path.join(base_dir, run_name)
    os.makedirs(output_dir, exist_ok=True)
    return os.path.join(output_dir, filename)

def load_skill_file(filename: str, today_str: str, model_name: str) -> str:
    """Reads a phase-specific skill instruction file and injects placeholders."""
    path = os.path.join(os.path.dirname(__file__), "instructions", filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        return content.replace("{TODAY}", today_str).replace("{MODEL}", model_name)
    except Exception as e:
        print_error(f"Failed to load skill instruction from {path}: {e}")
        sys.exit(1)

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

def _flush_trace(trace_path: Optional[str], trace_data: List[Dict[str, Any]]):
    if trace_path:
        try:
            with open(trace_path, "w", encoding="utf-8") as f:
                json.dump(trace_data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

def run_agent_loop(
    provider: LLMProvider,
    mcp_client: MCPClient,
    tools: List[Any],
    prompt: str,
    system_instruction: str,
    max_turns: int = 10,
    trace_path: Optional[str] = None,
    silent: bool = False,
    agent_name: str = "Agent",
    agent_color: str = Colors.BLUE
) -> tuple[str, List[Dict[str, Any]]]:
    """Runs the autonomous ReAct reasoning-action loop to answer the prompt.
    
    Returns:
        (final_response, execution_trace)
    """
    def log_agent(msg):
        if not silent:
            print(f"{agent_color}{Colors.BOLD}[Agent - {agent_name}]{Colors.RESET} {fix_bidi(msg)}")
    def log_mcp(msg):
        if not silent:
            print(f"{agent_color}{Colors.BOLD}[MCP - {agent_name}]{Colors.RESET} {fix_bidi(msg)}")
    def log_llm(msg):
        if not silent:
            print(f"{agent_color}{Colors.BOLD}[LLM - {agent_name}]{Colors.RESET} {fix_bidi(msg)}")
    def log_error(msg):
        # Always print errors so failures in background threads are visible
        print_error(f"[Agent - {agent_name}] {msg}")
    
    # 1. Initialize History with instructions & user query
    history: List[Message] = [
        Message(role="user", content=f"System Instruction:\n{system_instruction}"),
        Message(role="assistant", content="הבנתי. אני מוכן לעזור לך למצוא מידע בתקציב המדינה. כיצד אוכל לסייע?")
    ]
    history.append(Message(role="user", content=prompt))
    log_agent(f"User Query: {prompt.splitlines()[0]}")

    # 2. Loop Execution
    turn = 0
    response = None
    final_response = ""
    execution_trace: List[Dict[str, Any]] = []
    
    while turn < max_turns:
        turn += 1
        log_agent(f"Turn {turn}: Thinking...")
        
        # Call LLM reasoning
        try:
            response = provider.generate(history, tools)
        except Exception as e:
            log_error(f"LLM generation error: {e}")
            break

        # Termination check (synthesis/final response without tools)
        if not response.tool_calls:
            execution_trace.append({
                "turn": turn,
                "tool_name": None,
                "arguments": None,
                "reasoning": response.content or "",
                "output": None,
                "raw_payload": getattr(provider, "last_payload", None)
            })
            _flush_trace(trace_path, execution_trace)
            
            if response.content:
                preview = response.content.splitlines()[0] if response.content else ""
                if len(response.content) > 150:
                    preview = response.content[:150].replace('\n', ' ') + "..."
                log_llm(f"Response Preview:\n{preview}")
                final_response = response.content
            log_agent("Task completed. No more tool calls requested.")
            break

        if response.content:
            preview = response.content.splitlines()[0] if response.content else ""
            if len(response.content) > 150:
                preview = response.content[:150].replace('\n', ' ') + "..."
            log_llm(f"Response Preview:\n{preview}")
            final_response = response.content

        history.append(response)

        # Action execution phase
        log_agent(f"Model requested {len(response.tool_calls)} tool call(s):")
        # The payload is identical for every call in a turn (it is the request that produced
        # them all), so record it only once per turn instead of once per call.
        turn_payload = getattr(provider, "last_payload", None)
        for tc in response.tool_calls:
            name = tc["name"]
            args_data = tc["arguments"]
            call_id = tc.get("id") or str(uuid.uuid4())
            
            log_mcp(f"Executing tool {Colors.BOLD}{name}{Colors.RESET} with arguments: {args_data}")
            
            try:
                # Call tool on MCP server
                tool_output = mcp_client.call_tool(name, args_data)
                
                # Console output preview formatting (short, non-noisy)
                try:
                    parsed_output = json.loads(tool_output)
                    pretty_output = json.dumps(parsed_output, indent=2, ensure_ascii=False)
                    preview = pretty_output[:150] + "..." if len(pretty_output) > 150 else pretty_output
                except Exception:
                    preview = tool_output[:100] + "..." if len(tool_output) > 100 else tool_output
                log_mcp(f"Output Preview:\n{preview}")
                
                # Capture to execution trace in memory (complete raw payload & output)
                execution_trace.append({
                    "turn": turn,
                    "tool_name": name,
                    "arguments": args_data,
                    "reasoning": response.content or "",
                    "output": tool_output,
                    "raw_payload": turn_payload
                })
                turn_payload = None
                _flush_trace(trace_path, execution_trace)
                
                # Feed output back to history
                history.append(Message(
                    role="tool",
                    content=tool_output,
                    tool_response_id=call_id,
                    name=name
                ))
            except Exception as e:
                log_error(f"Tool execution failed: {e}")
                
                execution_trace.append({
                    "turn": turn,
                    "tool_name": name,
                    "arguments": args_data,
                    "reasoning": response.content or "",
                    "output": f"Error: {e}",
                    "raw_payload": turn_payload
                })
                turn_payload = None
                _flush_trace(trace_path, execution_trace)

                history.append(Message(
                    role="tool",
                    content=f"Error executing tool {name}: {e}",
                    tool_response_id=call_id,
                    name=name
                ))

    # Only trigger warning if maximum turns were exceeded AND the model wanted to call more tools.
    # If it completed without requesting tools, it successfully compiled its output.
    if response and turn >= max_turns and response.tool_calls:
        print_error(f"Reached maximum iteration limit. Forcing final synthesis turn for query: '{prompt[:60]}...'")
        history.append(Message(
            role="user",
            content="You have reached the execution limit. Please stop performing further tool calls and compile your final markdown dashboard using all the information gathered so far."
        ))
        try:
            log_agent("Final Turn: Thinking...")
            response = provider.generate(history, tools)
            if response.content:
                preview = response.content.splitlines()[0] if response.content else ""
                if len(response.content) > 150:
                    preview = response.content[:150].replace('\n', ' ') + "..."
                log_llm(f"Response Preview:\n{preview}")
                final_response = response.content
            
            execution_trace.append({
                "turn": turn + 1,
                "tool_name": "forced_synthesis",
                "arguments": None,
                "reasoning": "Forced final synthesis turn due to turn limit.",
                "output": response.content or "",
                "raw_payload": getattr(provider, "last_payload", None)
            })
            _flush_trace(trace_path, execution_trace)
        except Exception as e:
            log_error(f"LLM final synthesis generation error: {e}")
    
    return final_response, execution_trace

def main():
    parser = argparse.ArgumentParser(description="BudgetKey MCP Autonomous Agent")
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

    if not args.list_tools and not args.subject:
        parser.error("either --subject or --list-tools flag must be set")

    # Inject today's date and model name into instruction variables later
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Determine prompt text
    prompt_text = ""
    if args.subject:
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
        output_path = get_default_output_path(args.subject)

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

        model_name = getattr(provider, "model", None) or getattr(provider, "cmd", None) or args.model or "unknown-model"

        # Determine trace path first
        trace_path = None
        if output_path:
            parent_dir = os.path.dirname(output_path)
            if parent_dir:
                trace_path = os.path.join(parent_dir, "trace.json")

        # Run structured pipeline
        if args.test:
            print_agent("Running in test mode (Phase 1 budget lookup only, 1 turn)...")
            skill_p1 = load_skill_file("skill_phase1_budget.md", today_str, model_name)
            final_ans, combined_trace = run_agent_loop(
                provider, mcp_client, tools,
                f"Gather budget items data for the subject: {args.subject}",
                skill_p1, max_turns=1, trace_path=trace_path
            )
        else:
            print_agent("Starting Parallel Multi-Step Research Pipeline...")
            combined_trace = []
            subject = args.subject

            # Step 1: Budget Analysis (Sequential, runs in main thread first)
            print_agent("=== Step 1: Budget items analysis ===")
            skill_p1 = load_skill_file("skill_phase1_budget.md", today_str, model_name)
            phase1_prompt = f"Please collect aggregate budget data for the subject: '{subject}'."
            phase1_ans, trace_p1 = run_agent_loop(
                provider, mcp_client, tools, phase1_prompt, skill_p1,
                max_turns=6, trace_path=trace_path, silent=False,
                agent_name="Budget", agent_color=Colors.CYAN
            )
            combined_trace.extend(trace_p1)
            _flush_trace(trace_path, combined_trace)

            if len(trace_p1) >= 6:
                print_error("Warning: Step 1 (Budget) reached the maximum turn limit of 6!")

            # Step 2, 3 & 5: Contracts, Decisions & Hierarchy in Parallel
            def run_contracts():
                print_agent("  - Initiating Contracts and Suppliers Analysis (Thread 1)")
                local_provider = setup_llm_provider(args.provider, args.model)
                skill_p2 = load_skill_file("skill_phase2_contracts.md", today_str, model_name)
                # Pass Step 1 output to Step 2 so it has the budget codes
                phase2_prompt = (
                    f"Please collect procurement contracts and supplier aggregate totals for the subject: '{subject}'.\n"
                    f"Here is the budget items data collected in Step 1:\n{phase1_ans}"
                )
                local_mcp = setup_mcp(args.mcp_url)
                try:
                    return run_agent_loop(
                        local_provider, local_mcp, tools, phase2_prompt, skill_p2,
                        max_turns=6, trace_path=None, silent=False,
                        agent_name="Contracts", agent_color=Colors.MAGENTA
                    )
                finally:
                    local_mcp.close()

            def run_decisions():
                print_agent("  - Initiating Government Decisions Analysis (Thread 2)")
                local_provider = setup_llm_provider(args.provider, args.model)
                skill_p3 = load_skill_file("skill_phase3_decisions.md", today_str, model_name)
                # Pass Step 1 output to Step 3 for extra context
                phase3_prompt = (
                    f"Please query and extract government decisions related to the subject: '{subject}'.\n"
                    f"Here is the budget items data collected in Step 1:\n{phase1_ans}"
                )
                local_mcp = setup_mcp(args.mcp_url)
                try:
                    return run_agent_loop(
                        local_provider, local_mcp, tools, phase3_prompt, skill_p3,
                        max_turns=6, trace_path=None, silent=False,
                        agent_name="Decisions", agent_color=Colors.YELLOW
                    )
                finally:
                    local_mcp.close()

            def run_hierarchy():
                print_agent("  - Initiating Budget Hierarchy Analysis (Thread 3)")
                local_provider = setup_llm_provider(args.provider, args.model)
                skill_p5 = load_skill_file("skill_phase5_hierarchy.md", today_str, model_name)
                # Pass Step 1 output to Step 5 for prefix lookup context
                phase5_prompt = (
                    f"Please collect hierarchical budget items breakdown and program structures for the subject: '{subject}'.\n"
                    f"Here is the budget items data collected in Step 1:\n{phase1_ans}"
                )
                local_mcp = setup_mcp(args.mcp_url)
                try:
                    return run_agent_loop(
                        local_provider, local_mcp, tools, phase5_prompt, skill_p5,
                        max_turns=6, trace_path=None, silent=False,
                        agent_name="Hierarchy", agent_color=Colors.GREEN
                    )
                finally:
                    local_mcp.close()

            print_agent("Firing research threads (Contracts, Decisions, and Hierarchy) concurrently...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                future_p2 = executor.submit(run_contracts)
                future_p3 = executor.submit(run_decisions)
                future_p5 = executor.submit(run_hierarchy)

                print_agent("Waiting for parallel data collection threads to complete...")
                phase2_ans, trace_p2 = future_p2.result()
                phase3_ans, trace_p3 = future_p3.result()
                phase5_ans, trace_p5 = future_p5.result()

            print_agent("All research threads completed! Collating data...")
            
            # Check if workers reached the turn limit
            if len(trace_p2) >= 6:
                print_error("Warning: Step 2 (Contracts) reached the maximum turn limit of 6!")
            if len(trace_p3) >= 6:
                print_error("Warning: Step 3 (Decisions) reached the maximum turn limit of 6!")
            if len(trace_p5) >= 6:
                print_error("Warning: Step 5 (Hierarchy) reached the maximum turn limit of 6!")

            combined_trace.extend(trace_p2)
            combined_trace.extend(trace_p3)
            combined_trace.extend(trace_p5)
            _flush_trace(trace_path, combined_trace)

            # Step 4: Dashboard Synthesis (runs in main thread, not silent)
            print_agent("=== Step 4: Final dashboard synthesis ===")
            skill_p4 = load_skill_file("skill_phase_final_synthesis.md", today_str, model_name)
            template_content = load_skill_file("synthesis_template.md", today_str, model_name)
            
            # Pre-populate static metadata fields
            template_content = template_content.replace("{TODAY}", today_str).replace("{MODEL}", model_name)

            phase4_prompt = (
                f"Compile the final Hebrew Markdown dashboard document for the subject '{subject}' by filling the template below.\n\n"
                f"--- PHASE 1 DATA (BUDGET TIME-SERIES) ---\n{phase1_ans}\n\n"
                f"--- PHASE 5 DATA (BUDGET HIERARCHY FLOW) ---\n{phase5_ans}\n\n"
                f"--- PHASE 2 DATA (CONTRACTS & SUPPLIERS) ---\n{phase2_ans}\n\n"
                f"--- PHASE 3 DATA (GOVERNMENT DECISIONS) ---\n{phase3_ans}\n\n"
                f"Here is the template format you MUST fill. Fill all remaining placeholders (like {{SUBJECT_HEBREW}}, {{SUMMARY}}, etc.) using the data above. "
                f"Respond ONLY with the filled template starting immediately with '---'. Do NOT wrap your entire response in code blocks:\n\n"
                f"{template_content}"
            )
            final_ans, trace_p4 = run_agent_loop(
                provider, mcp_client, tools, phase4_prompt, skill_p4, max_turns=1, trace_path=trace_path,
                agent_name="Synthesis", agent_color=Colors.BLUE
            )
            combined_trace.extend(trace_p4)
            _flush_trace(trace_path, combined_trace)

        # Save output to Markdown file
        if final_ans and output_path:
            parent_dir = os.path.dirname(output_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            try:
                cleaned_ans = clean_markdown_fences(final_ans)
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(cleaned_ans)
                print_agent(f"Saved final markdown response to: {output_path}")
                
                # Save execution trace to JSON file in same directory
                trace_path = os.path.join(parent_dir, "trace.json")
                with open(trace_path, "w", encoding="utf-8") as f:
                    json.dump(combined_trace, f, indent=2, ensure_ascii=False)
                print_agent(f"Saved execution trace to: {trace_path}")
                
            except Exception as e:
                print_error(f"Failed to save outputs to {parent_dir}: {e}")

    finally:
        print_agent("Closing MCP connection...")
        mcp_client.close()

if __name__ == "__main__":
    main()
