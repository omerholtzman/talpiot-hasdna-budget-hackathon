#!/usr/bin/env bash
set -euo pipefail

readonly EXIT_INVALID_SPEC=10
readonly EXIT_TRANSPORT=11
readonly EXIT_QUERY_ERROR=12
readonly EXIT_QUERY_WARNING=13
readonly EXIT_MISSING_COLUMNS=14
readonly EXIT_TOO_FEW_ROWS=15

die() {
  local code="$1"
  shift
  echo "Error: $*" >&2
  echo "Error code: $code" >&2
  exit "$code"
}

usage() {
  cat >&2 <<EOF
Usage: $0 [--check] [--format pretty|json|tsv|csv] [--out [path]] queries/<query>.json

Formats:
  pretty  Human-readable metadata plus table output (default)
  json    Structured JSON for downstream tools
  tsv     Header row plus data rows, tab-separated
  csv     Header row plus data rows, comma-separated

Output:
  --out PATH  Write output to PATH
  --out       Write output next to the query file as result_<query-file-name>

Query specs can be either a single query:
  { "dataset": "...", "query": "SELECT ...", "columns": [...] }

Or multiple named queries:
  { "queries": [{ "name": "...", "dataset": "...", "query": "SELECT ..." }] }

Validation fields:
  columns           Display columns and default expected columns
  expected_columns  Optional validation-only columns, defaults to columns
  min_rows          Minimum required rows, defaults to 0

Exit codes:
  0   success
  10  invalid local query spec
  11  MCP transport/session failure
  12  MCP server/query error
  13  MCP warning returned
  14  expected columns missing
  15  too few rows returned
EOF
  exit "${1:-2}"
}

FORMAT="pretty"
QUERY_FILE=""
CHECK_ONLY=false
OUT_REQUESTED=false
OUT_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      CHECK_ONLY=true
      ;;
    --out)
      OUT_REQUESTED=true
      if [[ $# -gt 1 && "${2:-}" != -* ]]; then
        if [[ -n "$QUERY_FILE" || $# -gt 2 ]]; then
          OUT_FILE="$2"
          shift
        fi
      fi
      ;;
    --out=*)
      OUT_REQUESTED=true
      OUT_FILE="${1#*=}"
      ;;
    --format)
      shift
      if [[ $# -eq 0 ]]; then
        die "$EXIT_INVALID_SPEC" "--format requires a value."
      fi
      FORMAT="$1"
      ;;
    --format=*)
      FORMAT="${1#*=}"
      ;;
    -f)
      shift
      if [[ $# -eq 0 ]]; then
        die "$EXIT_INVALID_SPEC" "-f requires a value."
      fi
      FORMAT="$1"
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
      if [[ -n "$QUERY_FILE" ]]; then
        die "$EXIT_INVALID_SPEC" "only one query file may be provided."
      fi
      QUERY_FILE="$1"
      ;;
  esac
  shift
done

if [[ $# -gt 0 ]]; then
  if [[ -n "$QUERY_FILE" || $# -gt 1 ]]; then
    die "$EXIT_INVALID_SPEC" "only one query file may be provided."
  fi
  QUERY_FILE="$1"
fi

case "$FORMAT" in
  pretty|json|tsv|csv)
    ;;
  *)
    die "$EXIT_INVALID_SPEC" "unsupported format: $FORMAT"
    ;;
esac

if [[ -z "$QUERY_FILE" ]]; then
  usage
fi

if ! command -v jq >/dev/null 2>&1; then
  die "$EXIT_INVALID_SPEC" "this script requires jq."
fi

if [[ ! -f "$QUERY_FILE" ]]; then
  die "$EXIT_INVALID_SPEC" "query file not found: $QUERY_FILE"
fi

if ! jq empty "$QUERY_FILE" >/dev/null 2>&1; then
  die "$EXIT_INVALID_SPEC" "query file is not valid JSON: $QUERY_FILE"
fi

if [[ "$OUT_REQUESTED" == "true" && -z "$OUT_FILE" ]]; then
  if [[ "$QUERY_FILE" == */* ]]; then
    OUT_FILE="${QUERY_FILE%/*}/result_${QUERY_FILE##*/}"
  else
    OUT_FILE="result_$QUERY_FILE"
  fi
fi

MCP_URL="${MCP_URL:-https://next.obudget.org/mcp}"
MAX_QUERY_ATTEMPTS="${MAX_QUERY_ATTEMPTS:-3}"
TOP_TITLE="$(jq -r '.title // empty' "$QUERY_FILE")"
TOP_HUMAN_REQUEST="$(jq -r '.human_request // empty' "$QUERY_FILE")"
TOP_NOTES="$(jq -r '.notes // empty' "$QUERY_FILE")"

HAS_MULTI=false
if jq -e '(.queries? | type) == "array"' "$QUERY_FILE" >/dev/null; then
  HAS_MULTI=true
fi

if [[ "$HAS_MULTI" == "true" ]]; then
  QUERY_COUNT="$(jq -r '.queries | length' "$QUERY_FILE")"
  if [[ "$QUERY_COUNT" -eq 0 ]]; then
    die "$EXIT_INVALID_SPEC" ".queries must contain at least one query."
  fi
  if ! jq -e 'all(.queries[]; (.name? | type == "string" and length > 0))' "$QUERY_FILE" >/dev/null; then
    die "$EXIT_INVALID_SPEC" "every entry in .queries must have a non-empty string name."
  fi
  DUPLICATE_NAMES="$(jq -r '[.queries[].name] | group_by(.)[] | select(length > 1) | .[0]' "$QUERY_FILE")"
  if [[ -n "$DUPLICATE_NAMES" ]]; then
    echo "Error: duplicate query names:" >&2
    printf '%s\n' "$DUPLICATE_NAMES" >&2
    echo "Error code: $EXIT_INVALID_SPEC" >&2
    exit "$EXIT_INVALID_SPEC"
  fi
else
  QUERY_COUNT=1
fi

query_required_field() {
  local index="$1"
  local field="$2"
  jq -er --argjson i "$index" --arg field "$field" '
    def query_obj:
      if (.queries? | type) == "array" then .queries[$i] else . end;
    query_obj[$field]
  ' "$QUERY_FILE"
}

query_string_field() {
  local index="$1"
  local field="$2"
  local default="$3"
  jq -r --argjson i "$index" --arg field "$field" --arg default "$default" '
    def query_obj:
      if (.queries? | type) == "array" then .queries[$i] else . end;
    query_obj[$field] // $default
  ' "$QUERY_FILE"
}

query_page_size() {
  local index="$1"
  jq -er --argjson i "$index" '
    def query_obj:
      if (.queries? | type) == "array" then .queries[$i] else . end;
    query_obj.page_size // .page_size // 50 | tonumber
  ' "$QUERY_FILE"
}

query_min_rows() {
  local index="$1"
  jq -er --argjson i "$index" '
    def query_obj:
      if (.queries? | type) == "array" then .queries[$i] else . end;
    query_obj.min_rows // .min_rows // 0 | tonumber
  ' "$QUERY_FILE"
}

query_columns() {
  local index="$1"
  jq -c --argjson i "$index" '
    def query_obj:
      if (.queries? | type) == "array" then .queries[$i] else . end;
    query_obj.columns // []
  ' "$QUERY_FILE"
}

query_expected_columns() {
  local index="$1"
  jq -c --argjson i "$index" '
    def query_obj:
      if (.queries? | type) == "array" then .queries[$i] else . end;
    query_obj.expected_columns // query_obj.columns // []
  ' "$QUERY_FILE"
}

query_name() {
  local index="$1"
  jq -r --argjson i "$index" '
    def query_obj:
      if (.queries? | type) == "array" then .queries[$i] else . end;
    query_obj.name // "result"
  ' "$QUERY_FILE"
}

validate_sql() {
  local name="$1"
  local query="$2"

  if ! [[ "$query" =~ ^[[:space:]]*([sS][eE][lL][eE][cC][tT]|[wW][iI][tT][hH])[[:space:]] ]]; then
    die "$EXIT_INVALID_SPEC" "query '$name' must start with SELECT or WITH."
  fi

  if [[ "$query" == *";"* ]]; then
    die "$EXIT_INVALID_SPEC" "query '$name' must not contain semicolons."
  fi
}

for ((index = 0; index < QUERY_COUNT; index++)); do
  NAME="$(query_name "$index")"
  if ! DATASET="$(query_required_field "$index" "dataset")"; then
    die "$EXIT_INVALID_SPEC" "query '$NAME' is missing required field: dataset"
  fi
  if ! QUERY="$(query_required_field "$index" "query")"; then
    die "$EXIT_INVALID_SPEC" "query '$NAME' is missing required field: query"
  fi
  if ! PAGE_SIZE="$(query_page_size "$index")"; then
    die "$EXIT_INVALID_SPEC" "query '$NAME' has an invalid page_size."
  fi
  if ! MIN_ROWS="$(query_min_rows "$index")"; then
    die "$EXIT_INVALID_SPEC" "query '$NAME' has an invalid min_rows."
  fi

  if [[ -z "$DATASET" ]]; then
    die "$EXIT_INVALID_SPEC" "query '$NAME' has an empty dataset."
  fi
  if [[ "$PAGE_SIZE" -lt 1 ]]; then
    die "$EXIT_INVALID_SPEC" "query '$NAME' page_size must be positive."
  fi
  if [[ "$MIN_ROWS" -lt 0 ]]; then
    die "$EXIT_INVALID_SPEC" "query '$NAME' min_rows must be zero or positive."
  fi
  if [[ "$MIN_ROWS" -gt "$PAGE_SIZE" ]]; then
    die "$EXIT_INVALID_SPEC" "query '$NAME' min_rows must not exceed page_size."
  fi
  validate_sql "$NAME" "$QUERY"
done

if ! INIT_RESPONSE="$(
  curl -sS -L -i "$MCP_URL" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"budget-query-runner","version":"1.0"}}}'
)"; then
  die "$EXIT_TRANSPORT" "failed to initialize MCP session."
fi

SESSION_ID="$(
  printf '%s\n' "$INIT_RESPONSE" |
    awk 'tolower($1) == "mcp-session-id:" {print $2}' |
    tr -d '\r' |
    tail -n 1
)"

if [[ -z "$SESSION_ID" ]]; then
  echo "Error: could not find MCP session id in initialize response." >&2
  printf '%s\n' "$INIT_RESPONSE" >&2
  echo "Error code: $EXIT_TRANSPORT" >&2
  exit "$EXIT_TRANSPORT"
fi

if ! curl -sS -L "$MCP_URL" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H "Mcp-Session-Id: $SESSION_ID" \
  --data '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}' \
  >/dev/null; then
  die "$EXIT_TRANSPORT" "failed to complete MCP initialization handshake."
fi

run_mcp_query() {
  local request_id="$1"
  local name="$2"
  local dataset="$3"
  local query="$4"
  local page_size="$5"
  local payload=""
  local data_json=""
  local query_response=""
  local warnings=""

  payload="$(
    jq -nc \
      --arg dataset "$dataset" \
      --arg query "$query" \
      --argjson page_size "$page_size" \
      --argjson request_id "$request_id" \
      '{
        jsonrpc: "2.0",
        id: $request_id,
        method: "tools/call",
        params: {
          name: "DatasetDBQuery",
          arguments: {
            dataset: $dataset,
            query: $query,
            page_size: $page_size
          }
        }
      }'
  )"

  for ((attempt = 1; attempt <= MAX_QUERY_ATTEMPTS; attempt++)); do
    if ! query_response="$(
      curl -sS -L "$MCP_URL" \
        -H 'Content-Type: application/json' \
        -H 'Accept: application/json, text/event-stream' \
        -H "Mcp-Session-Id: $SESSION_ID" \
        --data "$payload"
    )"; then
      if [[ "$attempt" -eq "$MAX_QUERY_ATTEMPTS" ]]; then
        die "$EXIT_TRANSPORT" "query '$name' failed while calling MCP server."
      fi
      sleep "$attempt"
      continue
    fi

    data_json="$(
      printf '%s\n' "$query_response" |
        sed -n 's/^data: //p' |
        tail -n 1
    )"

    if [[ -n "$data_json" ]]; then
      break
    fi

    sleep "$attempt"
  done

  if [[ -z "$data_json" ]]; then
    echo "Error: query '$name' MCP response did not include an SSE data payload." >&2
    printf '%s\n' "$query_response" >&2
    echo "Error code: $EXIT_TRANSPORT" >&2
    exit "$EXIT_TRANSPORT"
  fi

  if jq -e '.error != null' >/dev/null <<<"$data_json"; then
    echo "Error from MCP server in query '$name':" >&2
    jq -r '.error.message' <<<"$data_json" >&2
    echo "Error code: $EXIT_QUERY_ERROR" >&2
    exit "$EXIT_QUERY_ERROR"
  fi

  warnings="$(jq -r '.result.structuredContent.warnings // empty' <<<"$data_json")"
  if [[ -n "$warnings" ]]; then
    echo "Error: query '$name' returned warnings:" >&2
    printf '%s\n' "$warnings" >&2
    echo "Error code: $EXIT_QUERY_WARNING" >&2
    exit "$EXIT_QUERY_WARNING"
  fi

  printf '%s\n' "$data_json"
}

RESULTS_JSON="[]"
for ((index = 0; index < QUERY_COUNT; index++)); do
  NAME="$(query_name "$index")"
  DATASET="$(query_required_field "$index" "dataset")"
  QUERY="$(query_required_field "$index" "query")"
  PAGE_SIZE="$(query_page_size "$index")"
  QUERY_TITLE="$(query_string_field "$index" "title" "")"
  QUERY_HUMAN_REQUEST="$(query_string_field "$index" "human_request" "")"
  QUERY_NOTES="$(query_string_field "$index" "notes" "")"
  COLUMNS_JSON="$(query_columns "$index")"
  EXPECTED_COLUMNS_JSON="$(query_expected_columns "$index")"
  MIN_ROWS="$(query_min_rows "$index")"
  DATA_JSON="$(run_mcp_query "$((index + 2))" "$NAME" "$DATASET" "$QUERY" "$PAGE_SIZE")"
  ROWS_COUNT="$(jq -r '.result.structuredContent.rows | length' <<<"$DATA_JSON")"
  DOWNLOAD_URL="$(jq -r '.result.structuredContent.download_url // empty' <<<"$DATA_JSON")"

  if [[ "$ROWS_COUNT" -lt "$MIN_ROWS" ]]; then
    echo "Error: query '$NAME' returned too few rows: got $ROWS_COUNT, expected at least $MIN_ROWS." >&2
    echo "Error code: $EXIT_TOO_FEW_ROWS" >&2
    exit "$EXIT_TOO_FEW_ROWS"
  fi

  MISSING_COLUMNS="$(
    jq -r --argjson expected "$EXPECTED_COLUMNS_JSON" '
      .result.structuredContent.rows as $rows |
      $expected[] as $column |
      select(($rows | length) > 0 and (($rows | all(.[]; has($column))) | not)) |
      $column
    ' <<<"$DATA_JSON"
  )"
  if [[ -n "$MISSING_COLUMNS" ]]; then
    echo "Error: query '$NAME' result is missing expected column(s):" >&2
    printf '%s\n' "$MISSING_COLUMNS" >&2
    echo "Error code: $EXIT_MISSING_COLUMNS" >&2
    exit "$EXIT_MISSING_COLUMNS"
  fi

  if [[ "$COLUMNS_JSON" == "[]" ]]; then
    if [[ "$ROWS_COUNT" -eq 0 ]]; then
      COLUMNS_JSON="[]"
    else
      COLUMNS_JSON="$(jq -c '.result.structuredContent.rows[0] | keys' <<<"$DATA_JSON")"
    fi
  fi

  RESULT_JSON="$(
    jq -cn \
      --arg name "$NAME" \
      --arg title "$QUERY_TITLE" \
      --arg human_request "$QUERY_HUMAN_REQUEST" \
      --arg notes "$QUERY_NOTES" \
      --arg dataset "$DATASET" \
      --arg query "$QUERY" \
      --arg download_url "$DOWNLOAD_URL" \
      --argjson page_size "$PAGE_SIZE" \
      --argjson min_rows "$MIN_ROWS" \
      --argjson columns "$COLUMNS_JSON" \
      --argjson expected_columns "$EXPECTED_COLUMNS_JSON" \
      --argjson row_count "$ROWS_COUNT" \
      --argjson data "$DATA_JSON" \
      '{
        name: $name,
        title: $title,
        human_request: $human_request,
        notes: $notes,
        dataset: $dataset,
        query: $query,
        page_size: $page_size,
        min_rows: $min_rows,
        columns: $columns,
        expected_columns: $expected_columns,
        row_count: $row_count,
        rows: ($data.result.structuredContent.rows // []),
        download_url: $download_url
      }'
  )"
  RESULTS_JSON="$(jq -cn --argjson results "$RESULTS_JSON" --argjson result "$RESULT_JSON" '$results + [$result]')"
done

if [[ "$OUT_REQUESTED" == "true" ]]; then
  if [[ -d "$OUT_FILE" ]]; then
    die "$EXIT_INVALID_SPEC" "output path is a directory: $OUT_FILE"
  fi
  if ! exec > "$OUT_FILE"; then
    die "$EXIT_INVALID_SPEC" "failed to open output file: $OUT_FILE"
  fi
fi

if [[ "$CHECK_ONLY" == "true" ]]; then
  if [[ "$FORMAT" == "json" ]]; then
    jq -n \
      --arg query_file "$QUERY_FILE" \
      --arg title "$TOP_TITLE" \
      --arg human_request "$TOP_HUMAN_REQUEST" \
      --arg notes "$TOP_NOTES" \
      --argjson results "$RESULTS_JSON" '
        {
          ok: true,
          query_file: $query_file,
          title: $title,
          human_request: $human_request,
          notes: $notes,
          results: [
            $results[] | {
              name,
              dataset,
              page_size,
              min_rows,
              row_count,
              columns,
              expected_columns,
              download_url
            }
          ]
        }
      '
  else
    echo "OK: $QUERY_FILE"
    jq -r '
      .[] |
      "OK: \(.name) rows=\(.row_count) min_rows=\(.min_rows) columns=\(.columns | length) expected_columns=\(.expected_columns | length)"
    ' <<<"$RESULTS_JSON"
  fi
  exit 0
fi

emit_delimited() {
  local encoder="$1"

  if [[ "$QUERY_COUNT" -eq 1 ]]; then
    jq -r --arg encoder "$encoder" '
      .[0] as $result |
      if $encoder == "csv" then
        ($result.columns | @csv),
        ($result.rows[] | [ $result.columns[] as $column | .[$column] // "" ] | @csv)
      else
        ($result.columns | @tsv),
        ($result.rows[] | [ $result.columns[] as $column | .[$column] // "" ] | @tsv)
      end
    ' <<<"$RESULTS_JSON"
  else
    jq -r --arg encoder "$encoder" '
      def union_columns:
        reduce (.[].columns[]?) as $column
          ([]; if index($column) then . else . + [$column] end);

      union_columns as $columns |
      if $encoder == "csv" then
        (["result_name"] + $columns | @csv),
        (.[] as $result | $result.rows[] | [$result.name] + [ $columns[] as $column | .[$column] // "" ] | @csv)
      else
        (["result_name"] + $columns | @tsv),
        (.[] as $result | $result.rows[] | [$result.name] + [ $columns[] as $column | .[$column] // "" ] | @tsv)
      end
    ' <<<"$RESULTS_JSON"
  fi
}

case "$FORMAT" in
  json)
    if [[ "$QUERY_COUNT" -eq 1 ]]; then
      jq -n \
        --arg title "$TOP_TITLE" \
        --arg human_request "$TOP_HUMAN_REQUEST" \
        --arg notes "$TOP_NOTES" \
        --argjson results "$RESULTS_JSON" '
          $results[0] as $result |
          $result + {
            title: (if ($result.title // "") != "" then $result.title else $title end),
            human_request: (if ($result.human_request // "") != "" then $result.human_request else $human_request end),
            notes: (if ($result.notes // "") != "" then $result.notes else $notes end),
            results: $results
          }
        '
    else
      jq -n \
        --arg title "$TOP_TITLE" \
        --arg human_request "$TOP_HUMAN_REQUEST" \
        --arg notes "$TOP_NOTES" \
        --argjson results "$RESULTS_JSON" \
        '{
          title: $title,
          human_request: $human_request,
          notes: $notes,
          results: $results
        }'
    fi
    ;;
  tsv)
    emit_delimited "tsv"
    ;;
  csv)
    emit_delimited "csv"
    ;;
  pretty)
    if [[ "$HAS_MULTI" == "true" ]]; then
      if [[ -n "$TOP_TITLE" ]]; then
        echo "$TOP_TITLE"
      fi
      if [[ -n "$TOP_HUMAN_REQUEST" ]]; then
        echo "Request: $TOP_HUMAN_REQUEST"
      fi
      if [[ -n "$TOP_NOTES" ]]; then
        echo "Notes: $TOP_NOTES"
      fi
      echo
    fi

    for ((index = 0; index < QUERY_COUNT; index++)); do
      RESULT_JSON="$(jq -c --argjson i "$index" '.[$i]' <<<"$RESULTS_JSON")"
      NAME="$(jq -r '.name' <<<"$RESULT_JSON")"
      TITLE="$(jq -r '.title // empty' <<<"$RESULT_JSON")"
      HUMAN_REQUEST="$(jq -r '.human_request // empty' <<<"$RESULT_JSON")"
      NOTES="$(jq -r '.notes // empty' <<<"$RESULT_JSON")"
      DOWNLOAD_URL="$(jq -r '.download_url // empty' <<<"$RESULT_JSON")"
      ROWS_COUNT="$(jq -r '.rows | length' <<<"$RESULT_JSON")"

      if [[ "$HAS_MULTI" == "true" ]]; then
        echo "Table: $NAME"
      fi
      if [[ -n "$TITLE" ]]; then
        echo "$TITLE"
      fi
      if [[ -n "$HUMAN_REQUEST" ]]; then
        echo "Request: $HUMAN_REQUEST"
      fi
      if [[ -n "$NOTES" ]]; then
        echo "Notes: $NOTES"
      fi
      if [[ -n "$DOWNLOAD_URL" ]]; then
        echo "Download: $DOWNLOAD_URL"
      fi
      echo

      if [[ "$ROWS_COUNT" -eq 0 ]]; then
        echo "No rows returned."
      else
        jq -r '
          .columns as $columns |
          ($columns | @tsv),
          (.rows[] | [ $columns[] as $column | .[$column] // "" ] | @tsv)
        ' <<<"$RESULT_JSON"
      fi

      if [[ "$index" -lt $((QUERY_COUNT - 1)) ]]; then
        echo
      fi
    done
    ;;
esac
