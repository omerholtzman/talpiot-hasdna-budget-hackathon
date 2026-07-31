"""
Central configuration for the Wiki Harness.

Everything that might change between environments — API keys, the model
name, the MCP server URL, file paths, safety limits — lives here so the
rest of the codebase never has to guess where a setting comes from or
hardcode a value in three different places.
"""
import os
from pathlib import Path


from dotenv import load_dotenv

load_dotenv()  # picks up a local .env file if one exists (see .env.example)

# --- Anthropic / model settings ---------------------------------------------
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# The model used for every phase (research phases 1-3 AND the synthesis
# phase 4). Override with the WIKI_HARNESS_MODEL env var if you want to,
# e.g., use a cheaper/faster model for the research phases.
MODEL_NAME = os.environ.get("WIKI_HARNESS_MODEL", "claude-sonnet-5")

# --- MCP server ---------------------------------------------------------------
# This is the one server all three research phases talk to. Only one URL
# to change if the endpoint ever moves.
MCP_URL = os.environ.get("WIKI_HARNESS_MCP_URL", "https://next.obudget.org/mcp")

# --- Paths ----------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"
OUTPUT_DIR = PROJECT_ROOT / "reports"

# --- Safety limits --------------------------------------------------------------
# Each research-phase agent (phases 1-3) runs a model -> tool-call -> model
# loop. This caps how many of those round trips a single phase can take
# before LangGraph gives up on it, so one bad SQL query can't loop forever.
AGENT_MAX_STEPS = int(os.environ.get("WIKI_HARNESS_MAX_STEPS", "12"))