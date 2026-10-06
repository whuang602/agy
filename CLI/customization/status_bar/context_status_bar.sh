#!/usr/bin/env bash
#
# AGY Custom Status Line - Context Window Tracker
#
# Tracks context window token consumption (input_tokens + cache_read_tokens)
# against model capacity (Gemini 1M, Claude 200k, GPT-4o 128k, fallback 1M).
# Can run standalone or as a segment within a unified status bar.
#

# --- Colors ---
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
GRAY="\033[90m"
RESET="\033[0m"

# --- Glyph ---
GLYPH="󰍛"

# --- Parse Arguments & Environment ---
SEGMENT_MODE=false
FORMAT_STYLE="${CONTEXT_STYLE:-compact}"  # "compact" (󰍛 65k (6.5%)) or "verbose" (ctx: 65k/1M (6.5%))
SUPPRESS_PENDING=false
CUSTOM_TRANSCRIPT=""
CUSTOM_TRANSCRIPT_SPECIFIED=false

while [ $# -gt 0 ]; do
    case "$1" in
        --segment)
            SEGMENT_MODE=true
            shift
            ;;
        --compact)
            FORMAT_STYLE="compact"
            shift
            ;;
        --verbose)
            FORMAT_STYLE="verbose"
            shift
            ;;
        --suppress-pending)
            SUPPRESS_PENDING=true
            shift
            ;;
        --transcript)
            CUSTOM_TRANSCRIPT_SPECIFIED=true
            if [ $# -ge 2 ]; then
                CUSTOM_TRANSCRIPT="$2"
                shift 2
            else
                shift
            fi
            ;;
        --transcript=*)
            CUSTOM_TRANSCRIPT_SPECIFIED=true
            CUSTOM_TRANSCRIPT="${1#*=}"
            shift
            ;;
        -h|--help)
            cat << 'EOF'
Usage: context_status_bar.sh [OPTIONS]

AGY Custom Status Line - Context Window Tracker

Options:
  --segment           Output segment format: <COLORED>\t<RAW>\t<SHORT_COLORED>\t<SHORT_RAW>
  --compact           Compact format (e.g. 󰍛 65k (6.5%)) [default]
  --verbose           Verbose format (e.g. ctx: 65k/1M (6.5%))
  --transcript PATH   Explicit transcript file to inspect
  --suppress-pending  Output nothing (exit 0) when pending or empty
  -h, --help          Show this help message

Environment Variables:
  AGY_CONTEXT_LIMIT   Explicit context window token limit override (e.g. 200000)
  STATUS_BAR_WIDTH    Override terminal width
  CONTEXT_STYLE       Default display format ("compact" or "verbose")
EOF
            exit 0
            ;;
        *)
            shift
            ;;
    esac
done

# --- Read stdin JSON payload if piped from AGY CLI ---
INPUT_JSON=""
if [ ! -t 0 ]; then
    INPUT_JSON=$(cat)
fi

# --- Guard jq presence: if jq is missing, render pending marker cleanly ---
if ! command -v jq >/dev/null 2>&1; then
    if [ "$SUPPRESS_PENDING" = true ]; then
        exit 0
    fi
    if [ "$SEGMENT_MODE" = true ]; then
        printf "%b\t%s\t%b\t%s\n" "${GRAY}${GLYPH} --${RESET}" "${GLYPH} --" "${GRAY}${GLYPH} --${RESET}" "${GLYPH} --"
    else
        echo -e "${GRAY}───[ ${RESET}${GRAY}${GLYPH} --${RESET}${GRAY} ]────────────────────────────────────────────────────────────────────────${RESET}"
    fi
    exit 0
fi

# =============================================================================
# MODEL CONTEXT WINDOW CAPACITY RESOLUTION
# =============================================================================
CONTEXT_LIMIT=""

# 1. Environment variable override takes highest priority
if [[ "${AGY_CONTEXT_LIMIT:-}" =~ ^[1-9][0-9]*$ ]]; then
    CONTEXT_LIMIT=$(( 10#${AGY_CONTEXT_LIMIT} ))
fi

# 2. Parse model from stdin JSON if limit not yet resolved
if [ -z "$CONTEXT_LIMIT" ] && [[ "$INPUT_JSON" =~ [^[:space:]] ]]; then
    MODEL_RAW=$(echo "$INPUT_JSON" | jq -r '
        (.model? | if type == "object" then ((.id? // "") + " " + (.display_name? // ""))
                   elif type == "string" then .
                   else "" end) // ""
    ' 2>/dev/null | tr '[:upper:]' '[:lower:]')

    if [ -n "$MODEL_RAW" ]; then
        if [[ "$MODEL_RAW" =~ claude|sonnet|opus|anthropic ]]; then
            CONTEXT_LIMIT=200000
        elif [[ "$MODEL_RAW" =~ gpt|o1|o3|codex|openai ]]; then
            CONTEXT_LIMIT=128000
        elif [[ "$MODEL_RAW" =~ gemini|flash|pro|google ]]; then
            CONTEXT_LIMIT=1000000
        fi
    fi
fi

# 3. Fallback default
[ -z "$CONTEXT_LIMIT" ] && CONTEXT_LIMIT=1000000

# =============================================================================
# DATA SOURCING: TOKEN USAGE RESOLUTION
# =============================================================================
USED_TOKENS=""
IS_AGY_HOST=false
if [[ "$INPUT_JSON" =~ [^[:space:]] ]]; then
    IS_AGY_HOST=true
fi

# 1. Stdin Payload Precedence: Check explicit tokens in stdin JSON
# (Only if custom transcript was not explicitly specified)
if [ "$CUSTOM_TRANSCRIPT_SPECIFIED" = false ] && [ "$IS_AGY_HOST" = true ]; then
    EXPLICIT_TOKENS=$(echo "$INPUT_JSON" | jq -r '
        (.context_tokens //
         (if (.tokens | type) == "number" then .tokens
          elif (.tokens | type) == "object" then
             (.tokens.total // (if .tokens.input != null then ((.tokens.input | tonumber? // 0) + ((.tokens.cache_read // 0) | tonumber? // 0)) else empty end))
          else empty end) //
         (if (.usage | type) == "object" then
             (.usage.total_tokens // (if .usage.input_tokens != null then ((.usage.input_tokens | tonumber? // 0) + ((.usage.cache_read_tokens // 0) | tonumber? // 0)) else empty end) // .usage.prompt_tokens)
          else empty end) //
         empty) | tonumber? // empty
    ' 2>/dev/null)

    if [[ "$EXPLICIT_TOKENS" =~ ^[0-9]+$ ]]; then
        USED_TOKENS="$EXPLICIT_TOKENS"
    fi
fi

# 2. Authoritative Transcript Inspection
if [ -z "$USED_TOKENS" ]; then
    TRANSCRIPT=""
    RESOLVE_MODE="none"

    if [ "$CUSTOM_TRANSCRIPT_SPECIFIED" = true ]; then
        if [ -n "$CUSTOM_TRANSCRIPT" ] && [ -f "$CUSTOM_TRANSCRIPT" ] && [ ! -d "$CUSTOM_TRANSCRIPT" ]; then
            TRANSCRIPT="$CUSTOM_TRANSCRIPT"
            RESOLVE_MODE="custom"
        else
            RESOLVE_MODE="pending"
        fi
    elif [ "$IS_AGY_HOST" = true ]; then
        CONV_RAW=$(echo "$INPUT_JSON" | jq -r '.conversation_id // .conversationId // .session_id // empty' 2>/dev/null)
        if [ -n "$CONV_RAW" ]; then
            # Strictly validate CONV_RAW: reject path traversal or malicious characters
            if [[ "$CONV_RAW" =~ ^[A-Za-z0-9_-]{1,64}$ ]]; then
                CANDIDATE="$HOME/.gemini/antigravity-cli/brain/$CONV_RAW/.system_generated/logs/transcript.jsonl"
                if [ -f "$CANDIDATE" ]; then
                    TRANSCRIPT="$CANDIDATE"
                    RESOLVE_MODE="cli"
                else
                    RESOLVE_MODE="pending"
                fi
            else
                RESOLVE_MODE="pending"
            fi
        else
            RESOLVE_MODE="pending"
        fi
    fi

    # Fallback when running standalone without stdin JSON
    if [ "$IS_AGY_HOST" = false ] && [ "$RESOLVE_MODE" = "none" ] && [ -z "$TRANSCRIPT" ]; then
        CANDIDATE=$(ls -td "$HOME/.gemini/antigravity-cli/brain"/*/.system_generated/logs/transcript.jsonl 2>/dev/null | head -n 1)
        if [ -n "$CANDIDATE" ] && [ -f "$CANDIDATE" ]; then
            MTIME=""
            if date -r "$CANDIDATE" +%s >/dev/null 2>&1; then
                MTIME=$(date -r "$CANDIDATE" +%s 2>/dev/null)
            elif stat -c %Y "$CANDIDATE" >/dev/null 2>&1; then
                MTIME=$(stat -c %Y "$CANDIDATE" 2>/dev/null)
            fi
            NOW=$(date +%s)
            MAX_AGE=${AGY_FALLBACK_MAX_AGE_SECS:-300}
            if [ -n "$MTIME" ] && (( NOW - MTIME <= MAX_AGE )); then
                seg="${CANDIDATE#"$HOME/.gemini/antigravity-cli/brain/"}"
                seg="${seg%%/*}"
                if [[ "$seg" =~ ^[A-Za-z0-9_-]{1,64}$ ]]; then
                    TRANSCRIPT="$CANDIDATE"
                    RESOLVE_MODE="fallback"
                fi
            fi
        fi
    fi

    # Extract tokens from transcript file
    if [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
        REV=()
        if command -v tac >/dev/null 2>&1; then
            REV=(tac)
        elif tail -r /dev/null >/dev/null 2>&1; then
            REV=(tail -r)
        fi

        EXTRACTED=""
        if [ ${#REV[@]} -gt 0 ]; then
            EXTRACTED=$("${REV[@]}" "$TRANSCRIPT" 2>/dev/null | jq -R -n -r '
                first(inputs | fromjson? | select(type=="object" and .type=="PLANNER_RESPONSE")) as $resp |
                if $resp == null then empty
                else
                    (($resp.input_tokens // $resp.tokens.input_tokens // $resp.usage.input_tokens // 0) | tonumber? // 0) as $in |
                    (($resp.cache_read_tokens // $resp.tokens.cache_read_tokens // $resp.usage.cache_read_tokens // 0) | tonumber? // 0) as $cache |
                    ($in + $cache)
                end
            ' 2>/dev/null)
        else
            EXTRACTED=$(jq -R -s -r '
                split("\n") | map(fromjson? | select(type=="object" and .type=="PLANNER_RESPONSE")) | last as $resp |
                if $resp == null then empty
                else
                    (($resp.input_tokens // $resp.tokens.input_tokens // $resp.usage.input_tokens // 0) | tonumber? // 0) as $in |
                    (($resp.cache_read_tokens // $resp.tokens.cache_read_tokens // $resp.usage.cache_read_tokens // 0) | tonumber? // 0) as $cache |
                    ($in + $cache)
                end
            ' "$TRANSCRIPT" 2>/dev/null)
        fi

        if [[ "$EXTRACTED" =~ ^[0-9]+$ ]]; then
            USED_TOKENS="$EXTRACTED"
        fi
    fi
fi

# =============================================================================
# FORMATTING & COLOR ASSIGNMENT
# =============================================================================
if [ -z "$USED_TOKENS" ]; then
    if [ "$SUPPRESS_PENDING" = true ]; then
        exit 0
    fi
    CONTENT="${GRAY}${GLYPH} --${RESET}"
    RAW_CONTENT="${GLYPH} --"
    SHORT_CONTENT="${GRAY}${GLYPH} --${RESET}"
    SHORT_RAW="${GLYPH} --"
else
    FORMATTED_DATA=$(jq -n -r \
        --argjson used "$USED_TOKENS" \
        --argjson limit "$CONTEXT_LIMIT" '
def format_tokens:
  if . >= 1000000 then
    ((. / 100000) | round) as $t |
    if ($t % 10) == 0 then "\($t / 10 | floor)M"
    else "\($t / 10 | floor).\($t % 10)M" end
  elif . >= 1000 then
    ((. / 100) | round) as $t |
    if ($t % 10) == 0 then "\($t / 10 | floor)k"
    else "\($t / 10 | floor).\($t % 10)k" end
  else
    tostring
  end;

def format_pct:
  (. * 10 | round) as $t |
  if ($t % 10) == 0 then "\($t / 10 | floor)%"
  else "\($t / 10 | floor).\($t % 10)%" end;

(($used * 100.0) / $limit) as $pct_num |
($pct_num | format_pct) as $pct_str |
($used | format_tokens) as $used_str |
($limit | format_tokens) as $limit_str |
(if $pct_num >= 85 then "red"
 elif $pct_num >= 60 then "yellow"
 else "green" end) as $color |
[$used_str, $limit_str, $pct_str, $color] | @tsv
    ' 2>/dev/null)

    USED_STR=$(echo "$FORMATTED_DATA" | cut -f1)
    LIMIT_STR=$(echo "$FORMATTED_DATA" | cut -f2)
    PCT_STR=$(echo "$FORMATTED_DATA" | cut -f3)
    COLOR_NAME=$(echo "$FORMATTED_DATA" | cut -f4)

    case "$COLOR_NAME" in
        red)    COLOR="$RED" ;;
        yellow) COLOR="$YELLOW" ;;
        green)  COLOR="$GREEN" ;;
        *)      COLOR="$GREEN" ;;
    esac

    SEG_COMPACT="${COLOR}${GLYPH} ${USED_STR} (${PCT_STR})${RESET}"
    RAW_COMPACT="${GLYPH} ${USED_STR} (${PCT_STR})"

    SEG_VERBOSE="${COLOR}ctx: ${USED_STR}/${LIMIT_STR} (${PCT_STR})${RESET}"
    RAW_VERBOSE="ctx: ${USED_STR}/${LIMIT_STR} (${PCT_STR})"

    SEG_SHORT="${COLOR}${GLYPH} ${USED_STR}${RESET}"
    RAW_SHORT="${GLYPH} ${USED_STR}"

    if [ "$FORMAT_STYLE" = "verbose" ]; then
        CONTENT="$SEG_VERBOSE"
        RAW_CONTENT="$RAW_VERBOSE"
    else
        CONTENT="$SEG_COMPACT"
        RAW_CONTENT="$RAW_COMPACT"
    fi

    SHORT_CONTENT="$SEG_SHORT"
    SHORT_RAW="$RAW_SHORT"
fi

# =============================================================================
# OUTPUT: SEGMENT OR STANDALONE
# =============================================================================
if [ "$SEGMENT_MODE" = true ]; then
    printf "%b\t%s\t%b\t%s\n" "$CONTENT" "$RAW_CONTENT" "$SHORT_CONTENT" "$SHORT_RAW"
    exit 0
fi

# Standalone border rendering
WIDTH=""
if [ -n "$INPUT_JSON" ]; then
    WIDTH=$(echo "$INPUT_JSON" | jq -r '.terminal.width // .columns // empty' 2>/dev/null)
fi
[ -z "$WIDTH" ] && [ -n "$STATUS_BAR_WIDTH" ] && WIDTH="$STATUS_BAR_WIDTH"
if [ -z "$WIDTH" ]; then
    if { true >/dev/null 2>&1 < /dev/tty; }; then
        WIDTH=$(stty size 2>/dev/null < /dev/tty | awk '{print $2}')
    fi
fi
[ -z "$WIDTH" ] && WIDTH=$(tput cols 2>/dev/null)
[ -z "$WIDTH" ] && WIDTH="${COLUMNS:-160}"
[[ "$WIDTH" =~ ^[0-9]+$ ]] || WIDTH=160
[ "$WIDTH" -lt 20 ] && WIDTH=20
[ "$WIDTH" -gt 1000 ] && WIDTH=1000

# Overhead: "───[ " (5) + " ]" (2) = 7 chars
RAW_TOTAL=$(( 7 + ${#RAW_CONTENT} ))

FILL_LEN=$(( WIDTH - RAW_TOTAL ))
[ "$FILL_LEN" -lt 0 ] && FILL_LEN=0

LINE_FILL=""
if [ "$FILL_LEN" -gt 0 ]; then
    printf -v LINE_FILL '%*s' "$FILL_LEN" ''
    LINE_FILL="${LINE_FILL// /─}"
fi

echo -e "${GRAY}───[ ${RESET}${CONTENT}${GRAY} ]${LINE_FILL}${RESET}"
