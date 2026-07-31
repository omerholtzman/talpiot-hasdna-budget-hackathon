"""
Schema-constrained one-shot JSON calls, for the pipeline's classification steps.

The ReAct phases talk to the model through LangChain's chat interface, which is
built around a message history and tool calls. The Phase 1 pipeline needs the
opposite: a single stateless call whose answer is a JSON value matching a fixed
schema — one verdict per input row, no prose, no tool loop. This module is that
call, wrapped so pipeline.py can stay provider-agnostic (`provider.generate_json`).

Why constrain the response rather than ask for JSON in the prompt: it removes
the whole class of output-format failures (fences, a leading sentence, a
trailing note). Not every version of langchain-google-genai accepts the native
`response_schema` argument, so the first call probes for it and remembers the
answer; the fallback asks in the prompt and parses tolerantly.
"""
import json
import re
import threading
from typing import Any, Dict, Optional

from langchain_google_genai import ChatGoogleGenerativeAI

from config import GEMINI_MODEL, GOOGLE_LOCATION, GOOGLE_PROJECT

# generate_json() is only ever used for classification — expand, triage, judge —
# where there is a best answer rather than a diverse one. Gemini defaults to
# temperature 1.0, and at that setting two runs of the identical prompt disagreed
# on 17 items and on whether the subject spanned 5 ministries or 9, which changed
# the run's cost 3.3x.
JSON_TEMPERATURE = 0.0

# Keywords the Gemini responseSchema validator accepts. Anything else — most
# notably `additionalProperties`, which the pipeline's schemas do not use but
# hand-written ones might — causes a 400 rather than being ignored.
_GEMINI_SCHEMA_KEYS = {
    "type", "format", "description", "nullable", "enum",
    "items", "properties", "required", "minItems", "maxItems",
}


def sanitize_gemini_schema(schema: Any) -> Any:
    """Strips JSON-Schema keywords the Gemini responseSchema validator rejects."""
    if isinstance(schema, list):
        return [sanitize_gemini_schema(x) for x in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key not in _GEMINI_SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {k: sanitize_gemini_schema(v) for k, v in value.items()}
        elif key == "items":
            out[key] = sanitize_gemini_schema(value)
        else:
            out[key] = value
    return out


def parse_json_response(text: str) -> Any:
    """Parses a model reply that should be JSON but may be fenced or padded.

    Models wrap JSON in ```json fences, prepend a sentence, or append a note. Try
    the strict parse first, then a fenced block, then the outermost bracketed span.
    """
    if text is None:
        raise ValueError("empty response")
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    fence = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except Exception:
            pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                continue
    raise ValueError("could not parse JSON from response: %s" % text[:300])


class JSONLLM:
    """One-shot JSON generation over a Gemini chat model.

    A single instance is shared by every worker thread in the pipeline's judging
    steps, so `last_payload` — which the cost tracker reads immediately after its
    own call to size that call — is thread-local. A plain attribute would hand a
    thread whichever concurrent call happened to finish most recently.
    """

    _payload_tls = threading.local()

    def __init__(self, model: Optional[str] = None):
        self.model = model or GEMINI_MODEL
        self._native_schema = True  # probed on first use, then remembered
        self._chat = ChatGoogleGenerativeAI(
            model=self.model,
            temperature=JSON_TEMPERATURE,
            project=GOOGLE_PROJECT,
            location=GOOGLE_LOCATION,
        )

    @property
    def last_payload(self) -> Optional[Dict[str, Any]]:
        return getattr(JSONLLM._payload_tls, "payload", None)

    @last_payload.setter
    def last_payload(self, value: Optional[Dict[str, Any]]) -> None:
        JSONLLM._payload_tls.payload = value

    def generate_json(self, prompt: str, schema: Dict[str, Any],
                      system: Optional[str] = None) -> Any:
        content = "\n\n".join(b for b in (system, prompt) if b)
        self.last_payload = {"model": self.model, "prompt": content}

        if self._native_schema:
            try:
                reply = self._chat.invoke(
                    content,
                    generation_config={
                        "response_mime_type": "application/json",
                        "response_schema": sanitize_gemini_schema(schema),
                    },
                )
                return parse_json_response(_text_of(reply))
            except TypeError:
                # The installed client does not accept generation_config here.
                # Fall through, and stop paying for the probe on later calls.
                self._native_schema = False

        instruction = (
            "Respond with a single JSON value matching this schema. "
            "Output JSON only - no prose, no code fences.\n\n"
            + json.dumps(schema, ensure_ascii=False)
        )
        reply = self._chat.invoke("\n\n".join([instruction, content]))
        return parse_json_response(_text_of(reply))


def _text_of(reply: Any) -> str:
    """The reply's text, whether the client returned a string or a message."""
    content = getattr(reply, "content", reply)
    if isinstance(content, list):
        # Multi-part content: concatenate the text parts, skip everything else.
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return content if isinstance(content, str) else str(content)
