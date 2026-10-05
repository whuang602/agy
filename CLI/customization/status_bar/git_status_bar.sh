#!/usr/bin/env bash
#
# AGY Custom Status Line - Modular Border Bar (Git Status)
#

# --- Colors ---
MAGENTA="\033[35m"
GREEN="\033[32m"
YELLOW="\033[33m"
GRAY="\033[90m"
RED="\033[31m"
RESET="\033[0m"

# --- Parse Arguments ---
SEGMENT_MODE=false
TARGET_DIR=""

for arg in "$@"; do
    if [ "$arg" = "--segment" ]; then
        SEGMENT_MODE=true
    elif [ -z "$TARGET_DIR" ] && [ -d "$arg" ]; then
        TARGET_DIR="$arg"
    fi
done

[ -z "$TARGET_DIR" ] && TARGET_DIR="$PWD"

# --- Detect True Terminal Width ---
WIDTH=""
[ -n "$STATUS_BAR_WIDTH" ] && WIDTH="$STATUS_BAR_WIDTH"
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

# If not in a git repo, display a clean placeholder border
if ! git -C "$TARGET_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    CONTENT="${RED}Git: not a repository${RESET}"
    RAW_CONTENT="Git: not a repository"
    if [ "$SEGMENT_MODE" = true ]; then
        printf "%b\t%s\n" "$CONTENT" "$RAW_CONTENT"
        exit 0
    fi
    RAW_TOTAL=$(( 7 + ${#RAW_CONTENT} ))
    PAD_LEN=$(( WIDTH - RAW_TOTAL ))
    [ "$PAD_LEN" -lt 0 ] && PAD_LEN=0
    FILL=""
    if [ "$PAD_LEN" -gt 0 ]; then
        printf -v FILL '%*s' "$PAD_LEN" ''
        FILL="${FILL// /─}"
    fi
    echo -e "${GRAY}───[ ${RESET}${CONTENT}${GRAY} ]${FILL}${RESET}"
    exit 0
fi

# =============================================================================
# SEGMENT 1: Git Branch
# =============================================================================
BRANCH=$(git -C "$TARGET_DIR" branch --show-current 2>/dev/null)
[ -z "$BRANCH" ] && BRANCH=$(git -C "$TARGET_DIR" rev-parse --short HEAD 2>/dev/null || echo "detached")
BRANCH="${BRANCH//[[:cntrl:]]/}"

SEG_BRANCH="${MAGENTA} ${BRANCH}${RESET}"
RAW_BRANCH=" ${BRANCH}"

# =============================================================================
# SEGMENT 2: Staged Files
# =============================================================================
STAGED=$(git -C "$TARGET_DIR" diff --cached --name-only 2>/dev/null | grep -c . || true)
if [ "$STAGED" -gt 0 ]; then
    SEG_STAGED="${GREEN}${STAGED} staged${RESET}"
else
    SEG_STAGED="${GRAY}0 staged${RESET}"
fi
RAW_STAGED="${STAGED} staged"

# =============================================================================
# SEGMENT 3: Unstaged Files (Modified + Untracked)
# =============================================================================
MODIFIED=$(git -C "$TARGET_DIR" diff --name-only 2>/dev/null | grep -c . || true)
UNTRACKED=$(git -C "$TARGET_DIR" ls-files --others --exclude-standard 2>/dev/null | grep -c . || true)
TOTAL_UNSTAGED=$(( MODIFIED + UNTRACKED ))

if [ "$TOTAL_UNSTAGED" -gt 0 ]; then
    SEG_UNSTAGED="${YELLOW}${TOTAL_UNSTAGED} unstaged${RESET}"
else
    SEG_UNSTAGED="${GRAY}0 unstaged${RESET}"
fi
RAW_UNSTAGED="${TOTAL_UNSTAGED} unstaged"

# =============================================================================
# SEGMENT 4: Upstream Divergence Tracking (Ahead / Behind / Synced)
# =============================================================================
UPSTREAM=$(git -C "$TARGET_DIR" rev-parse --abbrev-ref @{upstream} 2>/dev/null || true)
if [ -n "$UPSTREAM" ]; then
    COUNTS=$(git -C "$TARGET_DIR" rev-list --left-right --count @{upstream}...HEAD 2>/dev/null)
    if [ $? -eq 0 ] && [ -n "$COUNTS" ]; then
        BEHIND=$(echo "$COUNTS" | awk '{print $1}')
        AHEAD=$(echo "$COUNTS" | awk '{print $2}')
        if [[ "$BEHIND" =~ ^[0-9]+$ ]] && [[ "$AHEAD" =~ ^[0-9]+$ ]]; then
            if [ "$AHEAD" -gt 0 ] && [ "$BEHIND" -gt 0 ]; then
                SEG_UNPUSHED="${YELLOW}↕ ${AHEAD}/${BEHIND}${RESET}"
                RAW_UNPUSHED="↕ ${AHEAD}/${BEHIND}"
            elif [ "$AHEAD" -gt 0 ]; then
                SEG_UNPUSHED="${GREEN}↑ ${AHEAD}${RESET}"
                RAW_UNPUSHED="↑ ${AHEAD}"
            elif [ "$BEHIND" -gt 0 ]; then
                SEG_UNPUSHED="${YELLOW}↓ ${BEHIND}${RESET}"
                RAW_UNPUSHED="↓ ${BEHIND}"
            elif [ "$AHEAD" -eq 0 ] && [ "$BEHIND" -eq 0 ]; then
                SEG_UNPUSHED="${GREEN}✓ synced${RESET}"
                RAW_UNPUSHED="✓ synced"
            else
                SEG_UNPUSHED="${GRAY}unknown${RESET}"
                RAW_UNPUSHED="unknown"
            fi
        else
            SEG_UNPUSHED="${GRAY}unknown${RESET}"
            RAW_UNPUSHED="unknown"
        fi
    else
        SEG_UNPUSHED="${GRAY}unknown${RESET}"
        RAW_UNPUSHED="unknown"
    fi
else
    SEG_UNPUSHED="${GRAY}local${RESET}"
    RAW_UNPUSHED="local"
fi

# =============================================================================
# ASSEMBLE BORDER LINE
# =============================================================================
SEP=" ${GRAY}│${RESET} "
RAW_SEP=" │ "

CONTENT="${SEG_BRANCH}${SEP}${SEG_STAGED}${SEP}${SEG_UNSTAGED}${SEP}${SEG_UNPUSHED}"
RAW_CONTENT="${RAW_BRANCH}${RAW_SEP}${RAW_STAGED}${RAW_SEP}${RAW_UNSTAGED}${RAW_SEP}${RAW_UNPUSHED}"

if [ "$SEGMENT_MODE" = true ]; then
    printf "%b\t%s\n" "$CONTENT" "$RAW_CONTENT"
    exit 0
fi

# Calculate trailing line fill to span full terminal width
RAW_TOTAL=$(( 7 + ${#RAW_CONTENT} ))

FILL_LEN=$(( WIDTH - RAW_TOTAL ))
[ "$FILL_LEN" -lt 0 ] && FILL_LEN=0

LINE_FILL=""
if [ "$FILL_LEN" -gt 0 ]; then
    printf -v LINE_FILL '%*s' "$FILL_LEN" ''
    LINE_FILL="${LINE_FILL// /─}"
fi

echo -e "${GRAY}───[ ${RESET}${CONTENT}${GRAY} ]${LINE_FILL}${RESET}"
