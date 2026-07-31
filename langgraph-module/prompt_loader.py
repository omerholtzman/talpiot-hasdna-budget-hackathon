"""
Loads the markdown "skill" files (in prompts/) that double as the system
prompts for each phase, and fills in the handful of placeholders they use
({TODAY}, {MODEL}).

Keeping this as its own tiny module means the prompt files stay in plain,
human-editable markdown — a non-engineer can tweak a phase's instructions
(e.g. change the row limit, add a new column) without touching any Python.
"""
from pathlib import Path
from config import PROMPTS_DIR
from constants import PROMPT_FILES

# Maps a short phase name (used everywhere else in the codebase) to its
# markdown file on disk. Add a row here if you add a new phase.



def load_prompt(phase: str, **placeholders: str) -> str:
    """
    Read prompts/<phase>.md and substitute {PLACEHOLDER} tokens with the
    given keyword arguments, e.g. load_prompt("phase1_budget", TODAY="2026-07-30").

    We deliberately use str.replace() rather than str.format(): the skill
    files contain literal curly braces in JSON/YAML example blocks (e.g.
    `{"type": "web_search"}`-style snippets), and .format() would choke on
    those, mistaking them for format fields we didn't provide.
    """
    if phase not in PROMPT_FILES:
        raise ValueError(f"Unknown phase '{phase}'. Known phases: {list(PROMPT_FILES)}")

    text = (Path(PROMPTS_DIR) / PROMPT_FILES[phase]).read_text(encoding="utf-8")
    for key, value in placeholders.items():
        text = text.replace("{" + key + "}", value)
    return text
