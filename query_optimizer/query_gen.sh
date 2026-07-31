#!/usr/bin/env bash
set -euo pipefail

readonly EXIT_INVALID_SPEC=10
readonly EXIT_AGENT_FAILED=20
readonly EXIT_EXTRACT_FAILED=21
readonly EXIT_VALIDATION_FAILED=22

die() {
  local code="$1"
  shift
  echo "Error: $*" >&2
  echo "Error code: $code" >&2
  exit "$code"
}

usage() {
  cat >&2 <<EOF
Usage: $0 [options] "human question"

Generates a saved BudgetKey query spec by asking an MCP-capable agent to answer
the human question and emit query_run.sh-compatible JSON.

Options:
  --agent-cmd CMD       Command used to run the agent. Defaults to QUERY_GEN_AGENT_CMD.
                        The generated prompt is passed to the command on stdin.
  --agent-output FILE   Parse an existing agent transcript instead of running an agent.
  --out FILE            Save query spec to FILE. Defaults to queries/<slug>.json.
  --slug NAME           Filename slug used when --out is omitted.
  --no-check            Save without running ./query_run.sh --check.
  --force               Overwrite an existing output file.
  --keep-raw FILE       Save the raw agent output for debugging.
  --print-prompt        Print the generated prompt and exit without running the agent.
  -h, --help            Show this help.

Agent output contract:
  The agent should print the query spec between these exact marker lines:

  BEGIN_QUERY_SPEC_JSON
  { ... }
  END_QUERY_SPEC_JSON

  If markers are absent, this script also accepts a response that is only JSON,
  or the first fenced JSON block in the response.
EOF
  exit "${1:-2}"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="$SCRIPT_DIR/query_run.sh"
QUERIES_DIR="$SCRIPT_DIR/queries"

AGENT_CMD="${QUERY_GEN_AGENT_CMD:-}"
AGENT_OUTPUT_FILE=""
OUT_FILE=""
SLUG=""
CHECK=true
FORCE=false
KEEP_RAW_FILE=""
PRINT_PROMPT=false
QUESTION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent-cmd)
      shift
      if [[ $# -eq 0 ]]; then
        die "$EXIT_INVALID_SPEC" "--agent-cmd requires a value."
      fi
      AGENT_CMD="$1"
      ;;
    --agent-cmd=*)
      AGENT_CMD="${1#*=}"
      ;;
    --agent-output)
      shift
      if [[ $# -eq 0 ]]; then
        die "$EXIT_INVALID_SPEC" "--agent-output requires a value."
      fi
      AGENT_OUTPUT_FILE="$1"
      ;;
    --agent-output=*)
      AGENT_OUTPUT_FILE="${1#*=}"
      ;;
    --out)
      shift
      if [[ $# -eq 0 ]]; then
        die "$EXIT_INVALID_SPEC" "--out requires a value."
      fi
      OUT_FILE="$1"
      ;;
    --out=*)
      OUT_FILE="${1#*=}"
      ;;
    --slug)
      shift
      if [[ $# -eq 0 ]]; then
        die "$EXIT_INVALID_SPEC" "--slug requires a value."
      fi
      SLUG="$1"
      ;;
    --slug=*)
      SLUG="${1#*=}"
      ;;
    --no-check)
      CHECK=false
      ;;
    --force)
      FORCE=true
      ;;
    --keep-raw)
      shift
      if [[ $# -eq 0 ]]; then
        die "$EXIT_INVALID_SPEC" "--keep-raw requires a value."
      fi
      KEEP_RAW_FILE="$1"
      ;;
    --keep-raw=*)
      KEEP_RAW_FILE="${1#*=}"
      ;;
    --print-prompt)
      PRINT_PROMPT=true
      ;;
    -h|--help)
      usage 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      die "$EXIT_INVALID_SPEC" "unknown option: $1"
      ;;
    *)
      if [[ -n "$QUESTION" ]]; then
        QUESTION="$QUESTION $1"
      else
        QUESTION="$1"
      fi
      ;;
  esac
  shift
done

if [[ $# -gt 0 ]]; then
  if [[ -n "$QUESTION" ]]; then
    QUESTION="$QUESTION $*"
  else
    QUESTION="$*"
  fi
fi

if [[ -z "$QUESTION" ]]; then
  usage
fi

if ! command -v jq >/dev/null 2>&1; then
  die "$EXIT_INVALID_SPEC" "this script requires jq."
fi

if [[ ! -x "$RUNNER" ]]; then
  die "$EXIT_INVALID_SPEC" "runner not found or not executable: $RUNNER"
fi

slugify() {
  local text="$1"
  local slug=""

  slug="$(
    printf '%s' "$text" |
      tr '[:upper:]' '[:lower:]' |
      sed 's/[^a-z0-9][^a-z0-9]*/_/g; s/^_//; s/_$//' |
      cut -c 1-80
  )"

  if [[ -z "$slug" ]]; then
    slug="query_$(date +%Y%m%d_%H%M%S)"
  fi

  printf '%s\n' "$slug"
}

if [[ -z "$SLUG" ]]; then
  SLUG="$(slugify "$QUESTION")"
fi

if [[ -z "$OUT_FILE" ]]; then
  OUT_FILE="$QUERIES_DIR/$SLUG.json"
fi

build_prompt() {
  cat <<EOF
You are generating a saved query spec for the local BudgetKey MCP query runner.

Human question:
$QUESTION

You have access to the BudgetKey MCP server:
https://next.obudget.org/mcp

Use the MCP tools to research and validate the answer:
1. Call DatasetInfo before using a dataset.
2. Use DatasetFullTextSearch only if you need to resolve identifiers.
3. Use DatasetDBQuery to validate the final SQL.
4. If one table is enough, emit a single-query spec.
5. If several result tables are useful for plotting, emit a multi-query spec with a top-level "queries" array.

Return a query_run.sh-compatible query spec, not a prose answer.

Valid single-query shape:
{
  "title": "Short title",
  "human_request": "$QUESTION",
  "notes": "Important caveats and time period",
  "dataset": "dataset_id",
  "page_size": 50,
  "min_rows": 1,
  "columns": ["column_a", "column_b"],
  "query": "SELECT ..."
}

Valid multi-query shape:
{
  "title": "Short dashboard title",
  "human_request": "$QUESTION",
  "notes": "Important caveats and time period",
  "queries": [
    {
      "name": "stable_table_name",
      "title": "Table title",
      "dataset": "dataset_id",
      "page_size": 50,
      "min_rows": 1,
      "columns": ["column_a", "column_b"],
      "query": "SELECT ..."
    }
  ]
}

Rules:
- SQL must be a single SELECT or WITH statement.
- SQL must not contain semicolons.
- Use aliases so returned column names exactly match "columns".
- Set min_rows to the smallest reasonable number for validation.
- Include item_url when row-level links are useful.
- If the answer depends on a time period, include that period in notes and SQL.
- If expected validation columns should differ from display columns, include expected_columns.
- Do not emit Markdown, prose, or comments outside the markers.

Output exactly:
BEGIN_QUERY_SPEC_JSON
<valid JSON object>
END_QUERY_SPEC_JSON
EOF
}

extract_spec_json() {
  local raw="$1"
  local marked=""
  local fenced=""

  marked="$(
    awk '
      /^BEGIN_QUERY_SPEC_JSON[[:space:]]*$/ { inside = 1; next }
      /^END_QUERY_SPEC_JSON[[:space:]]*$/ { found = 1; exit }
      inside { print }
    ' <<<"$raw"
  )"

  if [[ -n "$marked" ]] && jq empty >/dev/null 2>&1 <<<"$marked"; then
    printf '%s\n' "$marked"
    return 0
  fi

  if jq empty >/dev/null 2>&1 <<<"$raw"; then
    printf '%s\n' "$raw"
    return 0
  fi

  fenced="$(
    awk '
      /^```json[[:space:]]*$/ { inside = 1; next }
      /^```[[:space:]]*$/ && inside { exit }
      inside { print }
    ' <<<"$raw"
  )"

  if [[ -n "$fenced" ]] && jq empty >/dev/null 2>&1 <<<"$fenced"; then
    printf '%s\n' "$fenced"
    return 0
  fi

  return 1
}

PROMPT="$(build_prompt)"

if [[ "$PRINT_PROMPT" == "true" ]]; then
  printf '%s\n' "$PROMPT"
  exit 0
fi

RAW_OUTPUT=""
if [[ -n "$AGENT_OUTPUT_FILE" ]]; then
  if [[ ! -f "$AGENT_OUTPUT_FILE" ]]; then
    die "$EXIT_INVALID_SPEC" "agent output file not found: $AGENT_OUTPUT_FILE"
  fi
  RAW_OUTPUT="$(<"$AGENT_OUTPUT_FILE")"
else
  if [[ -z "$AGENT_CMD" ]]; then
    die "$EXIT_INVALID_SPEC" "no agent command configured. Use --agent-cmd or QUERY_GEN_AGENT_CMD."
  fi
  if ! RAW_OUTPUT="$(printf '%s\n' "$PROMPT" | bash -c "$AGENT_CMD")"; then
    die "$EXIT_AGENT_FAILED" "agent command failed."
  fi
fi

if [[ -n "$KEEP_RAW_FILE" ]]; then
  mkdir -p "$(dirname "$KEEP_RAW_FILE")"
  printf '%s\n' "$RAW_OUTPUT" > "$KEEP_RAW_FILE"
fi

if ! SPEC_JSON="$(extract_spec_json "$RAW_OUTPUT")"; then
  die "$EXIT_EXTRACT_FAILED" "could not extract valid query spec JSON from agent output."
fi

NORMALIZED_SPEC="$(
  jq --arg human_request "$QUESTION" '
    .human_request = $human_request
  ' <<<"$SPEC_JSON"
)"

if [[ "$OUT_FILE" == */* ]]; then
  mkdir -p "${OUT_FILE%/*}"
fi

if [[ -e "$OUT_FILE" && "$FORCE" != "true" ]]; then
  die "$EXIT_INVALID_SPEC" "output file already exists: $OUT_FILE"
fi

TMP_SPEC="$(mktemp)"
trap 'rm -f "$TMP_SPEC"' EXIT
printf '%s\n' "$NORMALIZED_SPEC" > "$TMP_SPEC"

if [[ "$CHECK" == "true" ]]; then
  if ! "$RUNNER" --check "$TMP_SPEC" >&2; then
    die "$EXIT_VALIDATION_FAILED" "generated query spec failed runner validation; not saving."
  fi
fi

mv "$TMP_SPEC" "$OUT_FILE"
trap - EXIT

echo "Saved query spec: $OUT_FILE"
