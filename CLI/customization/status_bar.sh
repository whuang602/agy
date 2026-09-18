#!/usr/bin/env bash
#
# AGY Custom Status Line - Unified Status Bar
#
# Composes Git status, Session & Round timers, and Consumer Account Quota
# onto the same line with modular bracket segments:
#   ───[ Git ]───[ Timer ]───[ Quota ]───
# Suppresses brackets cleanly for any missing or disabled segment.
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
ENABLE_QUOTA=true
TIMER_FLAG=""
QUOTA_FLAG=""
TARGET_DIR=""

for arg in "$@"; do
    case "$arg" in
        --digital)
            TIMER_FLAG="--digital"
            ;;
        --verbose)
            TIMER_FLAG="--verbose"
            QUOTA_FLAG="--verbose"
            ;;
        --compact)
            QUOTA_FLAG="--compact"
            ;;
        --no-git)
            ENABLE_GIT=false
            ;;
        --git)
            ENABLE_GIT=true
            ;;
        --no-timer)
            ENABLE_TIMER=false
            ;;
        --timer)
            ENABLE_TIMER=true
            ;;
        --no-quota)
            ENABLE_QUOTA=false
            ;;
        --quota)
            ENABLE_QUOTA=true
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

# --- Retrieve Git Status Segment ---
GIT_CONTENT=""
GIT_RAW=""
if [ "$ENABLE_GIT" = true ] && [ -x "$SCRIPT_DIR/git_status_bar.sh" ]; then
    GIT_OUT=$(echo "$INPUT_JSON" | "$SCRIPT_DIR/git_status_bar.sh" --segment "$TARGET_DIR" 2>/dev/null | head -n 1)
    GIT_CONTENT=$(echo "$GIT_OUT" | cut -f1)
    GIT_RAW=$(echo "$GIT_OUT" | cut -f2 -s)
fi

# --- Retrieve Timer Segment ---
TIMER_CONTENT=""
TIMER_RAW=""
if [ "$ENABLE_TIMER" = true ] && [ -x "$SCRIPT_DIR/timer_status_bar.sh" ]; then
    TIMER_OUT=$(echo "$INPUT_JSON" | "$SCRIPT_DIR/timer_status_bar.sh" --segment $TIMER_FLAG 2>/dev/null | head -n 1)
    TIMER_CONTENT=$(echo "$TIMER_OUT" | cut -f1)
    TIMER_RAW=$(echo "$TIMER_OUT" | cut -f2 -s)
fi

# --- Retrieve Quota Segment ---
QUOTA_CONTENT=""
QUOTA_RAW=""
QUOTA_CONTENT_NO_COUNTDOWN=""
QUOTA_RAW_NO_COUNTDOWN=""
QUOTA_CONTENT_5H=""
QUOTA_RAW_5H=""
if [ "$ENABLE_QUOTA" = true ] && [ -x "$SCRIPT_DIR/quota_status_bar.sh" ]; then
    QUOTA_OUT=$(echo "$INPUT_JSON" | "$SCRIPT_DIR/quota_status_bar.sh" --segment $QUOTA_FLAG 2>/dev/null | head -n 1)
    QUOTA_CONTENT=$(echo "$QUOTA_OUT" | cut -f1)
    QUOTA_RAW=$(echo "$QUOTA_OUT" | cut -f2 -s)
    QUOTA_CONTENT_NO_COUNTDOWN=$(echo "$QUOTA_OUT" | cut -f3 -s)
    QUOTA_RAW_NO_COUNTDOWN=$(echo "$QUOTA_OUT" | cut -f4 -s)
    QUOTA_CONTENT_5H=$(echo "$QUOTA_OUT" | cut -f5 -s)
    QUOTA_RAW_5H=$(echo "$QUOTA_OUT" | cut -f6 -s)
fi

# Fallback pattern stripping for quota compaction if tiered fields are not present
if [ -n "$QUOTA_RAW" ] && [ -z "$QUOTA_RAW_NO_COUNTDOWN" ]; then
    if [[ "$QUOTA_RAW" == *" │ "* ]]; then
        QUOTA_RAW_NO_COUNTDOWN="${QUOTA_RAW%% │ *}"
        QUOTA_CONTENT_NO_COUNTDOWN="${QUOTA_CONTENT%% │ *}"
    else
        QUOTA_RAW_NO_COUNTDOWN="$QUOTA_RAW"
        QUOTA_CONTENT_NO_COUNTDOWN="$QUOTA_CONTENT"
    fi
fi
if [ -n "$QUOTA_RAW_NO_COUNTDOWN" ] && [ -z "$QUOTA_RAW_5H" ]; then
    if [[ "$QUOTA_RAW_NO_COUNTDOWN" == *" · "* ]]; then
        QUOTA_RAW_5H="${QUOTA_RAW_NO_COUNTDOWN%% · *}"
        QUOTA_CONTENT_5H="${QUOTA_CONTENT_NO_COUNTDOWN%% · *}"
    else
        QUOTA_RAW_5H="$QUOTA_RAW_NO_COUNTDOWN"
        QUOTA_CONTENT_5H="$QUOTA_CONTENT_NO_COUNTDOWN"
    fi
fi

# Cleanly exit 0 if all segments are empty/suppressed (e.g. enterprise user with quota-only)
if [ -z "$GIT_CONTENT" ] && [ -z "$TIMER_CONTENT" ] && [ -z "$QUOTA_CONTENT" ]; then
    exit 0
fi

calc_raw_total() {
    local total=0
    for r in "$@"; do
        if [ -n "$r" ]; then
            total=$(( total + 7 + ${#r} ))
        fi
    done
    echo "$total"
}

CUR_GIT_CONTENT="$GIT_CONTENT"
CUR_GIT_RAW="$GIT_RAW"
CUR_TIMER_CONTENT="$TIMER_CONTENT"
CUR_TIMER_RAW="$TIMER_RAW"
CUR_QUOTA_CONTENT="$QUOTA_CONTENT"
CUR_QUOTA_RAW="$QUOTA_RAW"

RAW_TOTAL=$(calc_raw_total "$CUR_GIT_RAW" "$CUR_TIMER_RAW" "$CUR_QUOTA_RAW")

# Width-aware compaction / progressive degradation when line width exceeds terminal
if [ "$RAW_TOTAL" -ge "$WIDTH" ]; then
    # Step 1: If quota segment has a reset countdown, drop the countdown
    if [ -n "$CUR_QUOTA_CONTENT" ] && [ -n "$QUOTA_CONTENT_NO_COUNTDOWN" ] && [ "$CUR_QUOTA_RAW" != "$QUOTA_RAW_NO_COUNTDOWN" ]; then
        CUR_QUOTA_CONTENT="$QUOTA_CONTENT_NO_COUNTDOWN"
        CUR_QUOTA_RAW="$QUOTA_RAW_NO_COUNTDOWN"
        RAW_TOTAL=$(calc_raw_total "$CUR_GIT_RAW" "$CUR_TIMER_RAW" "$CUR_QUOTA_RAW")
    fi

    # Step 2: If still too wide, drop the weekly badge
    if [ "$RAW_TOTAL" -ge "$WIDTH" ] && [ -n "$CUR_QUOTA_CONTENT" ] && [ -n "$QUOTA_CONTENT_5H" ] && [ "$CUR_QUOTA_RAW" != "$QUOTA_RAW_5H" ]; then
        CUR_QUOTA_CONTENT="$QUOTA_CONTENT_5H"
        CUR_QUOTA_RAW="$QUOTA_RAW_5H"
        RAW_TOTAL=$(calc_raw_total "$CUR_GIT_RAW" "$CUR_TIMER_RAW" "$CUR_QUOTA_RAW")
    fi

    # Step 3: If still too wide, drop the quota segment
    if [ "$RAW_TOTAL" -ge "$WIDTH" ] && [ -n "$CUR_QUOTA_CONTENT" ]; then
        CUR_QUOTA_CONTENT=""
        CUR_QUOTA_RAW=""
        RAW_TOTAL=$(calc_raw_total "$CUR_GIT_RAW" "$CUR_TIMER_RAW" "$CUR_QUOTA_RAW")
    fi

    # Step 4: If still too wide, drop the timer segment
    if [ "$RAW_TOTAL" -ge "$WIDTH" ] && [ -n "$CUR_TIMER_CONTENT" ]; then
        CUR_TIMER_CONTENT=""
        CUR_TIMER_RAW=""
        RAW_TOTAL=$(calc_raw_total "$CUR_GIT_RAW" "$CUR_TIMER_RAW" "$CUR_QUOTA_RAW")
    fi
fi

# =============================================================================
# DYNAMICALLY COMPOSE ACTIVE SEGMENTS ON THE SAME LINE
# =============================================================================
ACTIVE_CONTENTS=()
ACTIVE_RAWS=()

[ -n "$CUR_GIT_CONTENT" ] && [ -n "$CUR_GIT_RAW" ] && { ACTIVE_CONTENTS+=("$CUR_GIT_CONTENT"); ACTIVE_RAWS+=("$CUR_GIT_RAW"); }
[ -n "$CUR_TIMER_CONTENT" ] && [ -n "$CUR_TIMER_RAW" ] && { ACTIVE_CONTENTS+=("$CUR_TIMER_CONTENT"); ACTIVE_RAWS+=("$CUR_TIMER_RAW"); }
[ -n "$CUR_QUOTA_CONTENT" ] && [ -n "$CUR_QUOTA_RAW" ] && { ACTIVE_CONTENTS+=("$CUR_QUOTA_CONTENT"); ACTIVE_RAWS+=("$CUR_QUOTA_RAW"); }

# If all segments are empty after degradation, cleanly exit 0
if [ ${#ACTIVE_CONTENTS[@]} -eq 0 ]; then
    exit 0
fi

LINE_STR="${GRAY}───[ ${RESET}${ACTIVE_CONTENTS[0]}${GRAY} ]"
RAW_TOTAL=$(( 7 + ${#ACTIVE_RAWS[0]} ))

for (( i=1; i<${#ACTIVE_CONTENTS[@]}; i++ )); do
    LINE_STR="${LINE_STR}───[ ${RESET}${ACTIVE_CONTENTS[$i]}${GRAY} ]"
    RAW_TOTAL=$(( RAW_TOTAL + 7 + ${#ACTIVE_RAWS[$i]} ))
done

FILL_LEN=$(( WIDTH - RAW_TOTAL ))
[ "$FILL_LEN" -lt 0 ] && FILL_LEN=0

LINE_FILL=""
if [ "$FILL_LEN" -gt 0 ]; then
    printf -v LINE_FILL '%*s' "$FILL_LEN" ''
    LINE_FILL="${LINE_FILL// /─}"
fi

echo -e "${LINE_STR}${LINE_FILL}${RESET}"
