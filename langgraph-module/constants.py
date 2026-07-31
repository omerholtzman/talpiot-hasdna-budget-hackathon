PHASE1 = "phase_1"
PHASE2 = "phase_2"
PHASE3 = "phase_3"
PHASE4 = "phase_4"
PHASE5 = "final_phase"
TEMPLATE = "template"

# The Phase 1 pipeline's four classification prompts. Unlike the skill files
# above, these are not system prompts for an agent: each is a complete one-shot
# request whose answer is a JSON verdict list. See pipeline.py.
EXPAND = "expand"
TRIAGE_DOMAINS = "triage_domains"
TRIAGE_PROGRAMS = "triage_programs"
JUDGE_ITEMS = "judge_items"

PROMPT_FILES = {
    # PHASE1's skill file is no longer wired into the graph: phase 1 is now the
    # deterministic pipeline, not a ReAct agent. It is kept as the reference
    # description of the dataset and its traps, and stays in sync with
    # talpihackathon/main_agent/instructions/skill_phase1_budget.md.
    PHASE1: "skill_phase1_budget.md",
    PHASE2: "skill_phase2_contracts.md",
    PHASE3: "skill_phase3_decisions.md",
    # PHASE4 likewise: the hierarchy is now read off the pipeline's hierarchy.csv.
    PHASE4: "skill_phase4_hierarchy.md",
    PHASE5: "skill_phase_final_synthesis.md",
    TEMPLATE: "synthesis_template.md",
    EXPAND: "expand_terms.md",
    TRIAGE_DOMAINS: "triage_domains.md",
    TRIAGE_PROGRAMS: "triage_programs.md",
    JUDGE_ITEMS: "judge_items.md",
}

# Phases 2-4 run concurrently, so every log line is tagged with one of these to
# keep the interleaved output readable. Lives here rather than in agents.py so
# that pipeline.py can label its own output without importing agents.
PHASE_LABELS = {
    PHASE1: "Phase 1: Budget",
    PHASE2: "Phase 2: Contracts",
    PHASE3: "Phase 3: Decisions",
    PHASE4: "Phase 4: Hierarchy",
}
