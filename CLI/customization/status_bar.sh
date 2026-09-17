#!/usr/bin/env bash
#
# AGY Custom Status Line - Unified Status Bar
#
# Composes the Git status and Session & Round timers onto the same line.
# Dispatches to modular segment scripts (git_status_bar.sh and timer_status_bar.sh).
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Colors ---
GRAY="\033[90m"
RESET="\033[0m"

# --- Read stdin JSON payload if piped from AGY CLI ---
INPUT_JSON=""
if [ ! -t 0 ]; then
    INPUT_JSON=$(cat)
fi

# --- Parse Arguments ---
ENABLE_GIT=true
ENABLE_TIMER=true
TIMER_FLAG=""
TARGET_DIR=""

for arg in "$@"; do
    case "$arg" in
        --digital)
            TIMER_FLAG="--digital"
            ;;
        --verbose)
            TIMER_FLAG="--verbose"
            ;;
        --no-git)
            ENABLE_GIT=false
            ;;
        --no-timer)
            ENABLE_TIMER=false
            ;;
        *)
            if [ -z "$TARGET_DIR" ] && [ -d "$arg" ]; then
                TARGET_DIR="$arg"
            fi
            ;;
    esac
done

# --- Target Directory Fallback & stdin check ---
[ -z "$TARGET_DIR" ] && TARGET_DIR="$PWD"
if [ -n "$INPUT_JSON" ]; then
    CWD_FROM_JSON=$(echo "$INPUT_JSON" | jq -r '.cwd // empty' 2>/dev/null)
    [ -n "$CWD_FROM_JSON" ] && [ -d "$CWD_FROM_JSON" ] && TARGET_DIR="$CWD_FROM_JSON"
fi

# --- Detect True Terminal Width ---
WIDTH=""
if [ -c /dev/tty ]; then
    WIDTH=$(stty size < /dev/tty 2>/dev/null | awk '{print $2}')
fi
[ -z "$WIDTH" ] && WIDTH=$(tput cols 2>/dev/null)
[ -z "$WIDTH" ] && WIDTH="${COLUMNS:-160}"

# --- Retrieve Git Status Segment ---
GIT_OUT=""
if [ "$ENABLE_GIT" = true ] && [ -x "$SCRIPT_DIR/git_status_bar.sh" ]; then
    GIT_OUT=$(echo "$INPUT_JSON" | "$SCRIPT_DIR/git_status_bar.sh" --segment "$TARGET_DIR" 2>/dev/null)
fi

GIT_CONTENT=$(echo "$GIT_OUT" | cut -f1)
GIT_RAW=$(echo "$GIT_OUT" | cut -f2)

# --- Retrieve Timer Segment ---
TIMER_OUT=""
if [ "$ENABLE_TIMER" = true ] && [ -x "$SCRIPT_DIR/timer_status_bar.sh" ]; then
    TIMER_OUT=$(echo "$INPUT_JSON" | "$SCRIPT_DIR/timer_status_bar.sh" --segment $TIMER_FLAG 2>/dev/null)
fi

TIMER_CONTENT=$(echo "$TIMER_OUT" | cut -f1)
TIMER_RAW=$(echo "$TIMER_OUT" | cut -f2)

# =============================================================================
# COMBINE ON THE SAME LINE (SEPARATE BRACKETS)
# =============================================================================
if [ -n "$GIT_CONTENT" ] && [ -n "$TIMER_CONTENT" ]; then
    # Overhead: "───[ " (5) + " ]───[ " (7) + " ]" (2) = 14 chars
    RAW_TOTAL=$(( ${#GIT_RAW} + ${#TIMER_RAW} + 14 ))
    FILL_LEN=$(( WIDTH - RAW_TOTAL ))
    [ $FILL_LEN -lt 2 ] && FILL_LEN=2
    LINE_FILL=$(printf '─%.0s' $(seq 1 $FILL_LEN))

    echo -e "${GRAY}───[ ${RESET}${GIT_CONTENT}${GRAY} ]───[ ${RESET}${TIMER_CONTENT}${GRAY} ]${LINE_FILL}${RESET}"
elif [ -n "$GIT_CONTENT" ]; then
    # Overhead: "───[ " (5) + " ]" (2) = 7 chars
    RAW_TOTAL=$(( ${#GIT_RAW} + 7 ))
    FILL_LEN=$(( WIDTH - RAW_TOTAL ))
    [ $FILL_LEN -lt 2 ] && FILL_LEN=2
    LINE_FILL=$(printf '─%.0s' $(seq 1 $FILL_LEN))

    echo -e "${GRAY}───[ ${RESET}${GIT_CONTENT}${GRAY} ]${LINE_FILL}${RESET}"
elif [ -n "$TIMER_CONTENT" ]; then
    # Overhead: "───[ " (5) + " ]" (2) = 7 chars
    RAW_TOTAL=$(( ${#TIMER_RAW} + 7 ))
    FILL_LEN=$(( WIDTH - RAW_TOTAL ))
    [ $FILL_LEN -lt 2 ] && FILL_LEN=2
    LINE_FILL=$(printf '─%.0s' $(seq 1 $FILL_LEN))

    echo -e "${GRAY}───[ ${RESET}${TIMER_CONTENT}${GRAY} ]${LINE_FILL}${RESET}"
else
    CONTENT="AGY"
    RAW_TOTAL=$(( ${#CONTENT} + 7 ))
    FILL_LEN=$(( WIDTH - RAW_TOTAL ))
    [ $FILL_LEN -lt 2 ] && FILL_LEN=2
    LINE_FILL=$(printf '─%.0s' $(seq 1 $FILL_LEN))

    echo -e "${GRAY}───[ ${RESET}${CONTENT}${GRAY} ]${LINE_FILL}${RESET}"
fi
