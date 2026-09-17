#!/usr/bin/env bash
#
# AGY Custom Status Line - Session & Round Timer
#
# Computes total session duration and round duration (prompt to action completion).
# Can run standalone or as a segment within a unified status bar.
#

# --- Colors ---
CYAN="\033[36m"
GREEN="\033[32m"
YELLOW="\033[33m"
GRAY="\033[90m"
RESET="\033[0m"

# --- Parse Arguments & Environment ---
SEGMENT_MODE=false
CUSTOM_TRANSCRIPT=""
FORMAT_STYLE="${TIMER_STYLE:-verbose}"  # "verbose" (1h 02m 34s) or "digital" (01:02:34)

while [ $# -gt 0 ]; do
    case "$1" in
        --segment)
            SEGMENT_MODE=true
            shift
            ;;
        --digital)
            FORMAT_STYLE="digital"
            shift
            ;;
        --verbose)
            FORMAT_STYLE="verbose"
            shift
            ;;
        --transcript)
            CUSTOM_TRANSCRIPT="$2"
            shift 2
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

# --- Helper: Parse ISO-8601 UTC to Unix Epoch ---
parse_iso_to_epoch() {
    local iso="$1"
    [ -z "$iso" ] && return 1
    local epoch
    # GNU date
    epoch=$(date -d "$iso" +%s 2>/dev/null)
    if [ -n "$epoch" ]; then
        echo "$epoch"
        return 0
    fi
    # BSD date (macOS)
    epoch=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$iso" +%s 2>/dev/null)
    if [ -n "$epoch" ]; then
        echo "$epoch"
        return 0
    fi
    # Python3 fallback
    if command -v python3 >/dev/null 2>&1; then
        epoch=$(python3 -c "import datetime; print(int(datetime.datetime.fromisoformat('$iso'.replace('Z', '+00:00')).timestamp()))" 2>/dev/null)
        if [ -n "$epoch" ]; then
            echo "$epoch"
            return 0
        fi
    fi
    return 1
}

# --- Helper: Format seconds to continuous human-readable duration ---
# Always preserves seconds so the display never appears frozen.
format_duration() {
    local secs="$1"
    if [ -z "$secs" ] || [ "$secs" -le 0 ]; then
        if [ "$FORMAT_STYLE" = "digital" ]; then
            echo "00:00"
        else
            echo "<1s"
        fi
        return 0
    fi

    local h=$(( secs / 3600 ))
    local m=$(( (secs % 3600) / 60 ))
    local s=$(( secs % 60 ))

    if [ "$FORMAT_STYLE" = "digital" ]; then
        if [ "$h" -gt 0 ]; then
            printf "%02d:%02d:%02d" "$h" "$m" "$s"
        else
            printf "%02d:%02d" "$m" "$s"
        fi
    else
        if [ "$h" -gt 0 ]; then
            printf "%dh %02dm %02ds" "$h" "$m" "$s"
        elif [ "$m" -gt 0 ]; then
            printf "%dm %02ds" "$m" "$s"
        else
            printf "%ds" "$s"
        fi
    fi
}

# --- Locate Active Conversation & Transcript ---
TRANSCRIPT=""
CONV_ID=""
if [ -n "$INPUT_JSON" ]; then
    CONV_ID=$(echo "$INPUT_JSON" | jq -r '.conversation_id // empty' 2>/dev/null)
fi

if [ -n "$CUSTOM_TRANSCRIPT" ] && [ -f "$CUSTOM_TRANSCRIPT" ]; then
    TRANSCRIPT="$CUSTOM_TRANSCRIPT"
elif [ -n "$CONV_ID" ]; then
    CANDIDATE="$HOME/.gemini/antigravity-cli/brain/$CONV_ID/.system_generated/logs/transcript.jsonl"
    [ -f "$CANDIDATE" ] && TRANSCRIPT="$CANDIDATE"
fi

# Fallback: find the most recently modified transcript in AGY CLI brain directory
if [ -z "$TRANSCRIPT" ]; then
    TRANSCRIPT=$(ls -td "$HOME/.gemini/antigravity-cli/brain"/*/.system_generated/logs/transcript.jsonl 2>/dev/null | head -n 1)
    if [ -z "$CONV_ID" ] && [ -n "$TRANSCRIPT" ]; then
        CONV_ID=$(echo "$TRANSCRIPT" | awk -F'/brain/' '{print $2}' | awk -F'/' '{print $1}')
    fi
fi

NOW=$(date +%s)

# --- Compute Total Session Time (Continuously Incrementing) ---
SESSION_TEXT="--:--"
RAW_SESSION="--:--"
SESSION_CACHE="/tmp/agy_sess_${CONV_ID:-default}.start"

SESSION_EPOCH=""
if [ -f "$SESSION_CACHE" ]; then
    SESSION_EPOCH=$(<"$SESSION_CACHE")
fi

if [ -z "$SESSION_EPOCH" ] && [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
    FIRST_ISO=$(head -n 1 "$TRANSCRIPT" 2>/dev/null | jq -r .created_at 2>/dev/null)
    SESSION_EPOCH=$(parse_iso_to_epoch "$FIRST_ISO")
    if [ -n "$SESSION_EPOCH" ]; then
        echo "$SESSION_EPOCH" > "$SESSION_CACHE" 2>/dev/null
    fi
fi

if [ -n "$SESSION_EPOCH" ]; then
    SESSION_SECS=$(( NOW - SESSION_EPOCH ))
    [ $SESSION_SECS -lt 0 ] && SESSION_SECS=0
    SESSION_DUR=$(format_duration "$SESSION_SECS")
    SESSION_TEXT="${CYAN}󱎫 ${SESSION_DUR}${RESET}"
    RAW_SESSION="󱎫 ${SESSION_DUR}"
fi

# --- Compute Round Time (Prompt -> Action Complete) ---
ROUND_TEXT="${GRAY}󰔛 --${RESET}"
RAW_ROUND="󰔛 --"

if [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
    LAST_PROMPT_JSON=$(grep '"type":"USER_INPUT"' "$TRANSCRIPT" 2>/dev/null | tail -n 1)
    if [ -n "$LAST_PROMPT_JSON" ]; then
        PROMPT_ISO=$(echo "$LAST_PROMPT_JSON" | jq -r .created_at 2>/dev/null)
        PROMPT_EPOCH=$(parse_iso_to_epoch "$PROMPT_ISO")

        LAST_STEP_JSON=$(tail -n 1 "$TRANSCRIPT" 2>/dev/null)
        LAST_TYPE=$(echo "$LAST_STEP_JSON" | jq -r .type 2>/dev/null)
        LAST_TOOL_COUNT=$(echo "$LAST_STEP_JSON" | jq -r "if .tool_calls then (.tool_calls | length) else 0 end" 2>/dev/null)
        LAST_STEP_ISO=$(echo "$LAST_STEP_JSON" | jq -r .created_at 2>/dev/null)
        LAST_STEP_EPOCH=$(parse_iso_to_epoch "$LAST_STEP_ISO")

        if [ -n "$PROMPT_EPOCH" ]; then
            # If last step is MODEL response without tools, round is completed
            if [ "$LAST_TYPE" = "PLANNER_RESPONSE" ] && [ "$LAST_TOOL_COUNT" -eq 0 ]; then
                if [ -n "$LAST_STEP_EPOCH" ]; then
                    ROUND_SECS=$(( LAST_STEP_EPOCH - PROMPT_EPOCH ))
                else
                    ROUND_SECS=$(( NOW - PROMPT_EPOCH ))
                fi
                [ $ROUND_SECS -lt 0 ] && ROUND_SECS=0
                ROUND_DUR=$(format_duration "$ROUND_SECS")
                ROUND_TEXT="${GREEN}󰔛 ${ROUND_DUR}${RESET}"
                RAW_ROUND="󰔛 ${ROUND_DUR}"
            else
                # Round currently active/executing
                ROUND_SECS=$(( NOW - PROMPT_EPOCH ))
                [ $ROUND_SECS -lt 0 ] && ROUND_SECS=0
                ROUND_DUR=$(format_duration "$ROUND_SECS")
                ROUND_TEXT="${YELLOW}󱐋 ${ROUND_DUR}${RESET}"
                RAW_ROUND="󱐋 ${ROUND_DUR}"
            fi
        fi
    fi
fi

# =============================================================================
# ASSEMBLE TIMER SEGMENTS
# =============================================================================
SEP=" ${GRAY}│${RESET} "
RAW_SEP=" │ "

CONTENT="${SESSION_TEXT}${SEP}${ROUND_TEXT}"
RAW_CONTENT="${RAW_SESSION}${RAW_SEP}${RAW_ROUND}"

# If segment mode requested, output formatted content and raw text separated by tab
if [ "$SEGMENT_MODE" = true ]; then
    printf "%b\t%s\n" "$CONTENT" "$RAW_CONTENT"
    exit 0
fi

# =============================================================================
# STANDALONE BORDER LINE
# =============================================================================
# Detect terminal width
WIDTH=""
if [ -c /dev/tty ]; then
    WIDTH=$(stty size < /dev/tty 2>/dev/null | awk '{print $2}')
fi
[ -z "$WIDTH" ] && WIDTH=$(tput cols 2>/dev/null)
[ -z "$WIDTH" ] && WIDTH="${COLUMNS:-160}"

BORDER_CONTENT=" ${CONTENT} "
RAW_BORDER=" ${RAW_CONTENT} "

PREFIX_LEN=4  # length of "───["
SUFFIX_LEN=1  # length of "]"
RAW_TOTAL=$(( PREFIX_LEN + ${#RAW_BORDER} + SUFFIX_LEN ))

FILL_LEN=$(( WIDTH - RAW_TOTAL ))
[ $FILL_LEN -lt 2 ] && FILL_LEN=2

LINE_FILL=$(printf '─%.0s' $(seq 1 $FILL_LEN))

echo -e "${GRAY}───[${RESET}${BORDER_CONTENT}${GRAY}]${LINE_FILL}${RESET}"
