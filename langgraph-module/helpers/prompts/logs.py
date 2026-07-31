"""
Shared stdout logging for the pipeline stages.

Phases run concurrently, so every line is tagged with a phase label to keep
the interleaved output readable. This lives in its own module rather than in
agents.py because pipeline.py needs it too, and agents.py imports pipeline —
putting it here is what keeps that import one-directional.

Plain `print` rather than the `logging` module, deliberately: no configuration
to get wrong, and the output is a run transcript rather than an application log.
"""
from datetime import datetime


def log(label: str, message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{label}] {message}", flush=True)


def truncate(text: str, limit: int = 500) -> str:
    text = str(text)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... [truncated, {len(text)} chars total]"
