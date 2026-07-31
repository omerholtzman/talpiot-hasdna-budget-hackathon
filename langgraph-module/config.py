"""
Central configuration for the Wiki Harness.

Everything that might change between environments — API keys, the model
name, the MCP server URL, file paths, safety limits — lives here so the
rest of the codebase never has to guess where a setting comes from or
hardcode a value in three different places.
"""
import os
from pathlib import Path


try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> None:
        return None

load_dotenv()  # picks up a local .env file if one exists (see .env.example)

# --- Anthropic / model settings ---------------------------------------------
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# The model used for every phase (research phases 1-3 AND the synthesis
# phase 4). Override with the WIKI_HARNESS_MODEL env var if you want to,
# e.g., use a cheaper/faster model for the research phases.
MODEL_NAME = os.environ.get("WIKI_HARNESS_MODEL", "claude-sonnet-5")

# --- Google / Gemini ----------------------------------------------------------
# The model actually used by every phase, including the Phase 1 pipeline's
# classification steps. Kept here so the ReAct phases and the pipeline cannot
# drift onto different models — a run summary comparing them would be worthless.
GEMINI_MODEL = os.environ.get("WIKI_HARNESS_GEMINI_MODEL", "gemini-2.5-flash")
GOOGLE_PROJECT = os.environ.get("GCP_PROJECT", "qwiklabs-gcp-01-1436437a2cf1")
GOOGLE_LOCATION = os.environ.get("GCP_REGION", "europe-southwest1")

# --- MCP server ---------------------------------------------------------------
# This is the one server all three research phases talk to. Only one URL
# to change if the endpoint ever moves.
MCP_URL = os.environ.get("WIKI_HARNESS_MCP_URL", "https://next.obudget.org/mcp")

# How long a single MCP tool call may take. The server drops its own connection
# at 60 seconds, so anything past that is the client having lost the response
# rather than the query still running; the margin covers paging retries.
MCP_CALL_TIMEOUT = int(os.environ.get("WIKI_HARNESS_MCP_TIMEOUT", "180"))

# --- Paths ----------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"
OUTPUT_DIR = PROJECT_ROOT / "reports"
QUERY_OPTIMIZER_DIR = Path(
    os.environ.get("WIKI_HARNESS_QUERY_OPTIMIZER_DIR", PROJECT_ROOT.parent / "query_optimizer")
)
QUERY_CACHE_DIR = Path(
    os.environ.get("WIKI_HARNESS_QUERY_CACHE_DIR", QUERY_OPTIMIZER_DIR / "queries")
)


def env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


QUERY_RESEARCH_CACHE_ENABLED = env_flag("WIKI_HARNESS_QUERY_RESEARCH_CACHE", True)

# --- Safety limits --------------------------------------------------------------
# Each research-phase agent (phases 1-3) runs a model -> tool-call -> model
# loop. This caps how many of those round trips a single phase can take
# before LangGraph gives up on it, so one bad SQL query can't loop forever.
AGENT_MAX_STEPS = int(os.environ.get("WIKI_HARNESS_MAX_STEPS", "12"))

# --- Phase identifiers & prompt files --------------------------------------------
PHASE1 = "phase_1"
PHASE2 = "phase_2"
PHASE3 = "phase_3"
PHASE4 = "phase_4"
PHASE5 = "final_phase"
TEMPLATE = "template"

# The Phase 1 pipeline's four classification prompts. Unlike the skill files
# above, these are not system prompts for an agent: each is a complete one-shot
# request whose answer is a JSON verdict list. See agent_engineering/step1_pipeline.py.
EXPAND = "expand"
TRIAGE_DOMAINS = "triage_domains"
TRIAGE_PROGRAMS = "triage_programs"
JUDGE_ITEMS = "judge_items"

PROMPT_FILES = {
    PHASE1: "skill_phase1a_main.md",
    PHASE2: "skill_phase2_contracts.md",
    PHASE3: "skill_phase3_decisions.md",
    PHASE4: "skill_phase4_hierarchy.md",
    PHASE5: "skill_phase_final_synthesis.md",
    TEMPLATE: "synthesis_template.md",
    EXPAND: "skill_phase1b_expand_terms.md",
    TRIAGE_DOMAINS: "skill_phase1c_triage_domains.md",
    TRIAGE_PROGRAMS: "skill_phase1d_triage_programs.md",
    JUDGE_ITEMS: "skill_phase1e_judge_items.md",
}

# Phases 2-4 run concurrently, so every log line is tagged with one of these to
# keep the interleaved output readable. Lives here rather than in agents.py so
# that step1_pipeline.py can label its own output without importing agents.
PHASE_LABELS = {
    PHASE1: "Phase 1: Budget",
    PHASE2: "Phase 2: Contracts",
    PHASE3: "Phase 3: Decisions",
    PHASE4: "Phase 4: Hierarchy",
}
