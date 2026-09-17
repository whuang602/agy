#!/usr/bin/env bash
#
# AGY Custom Status Line - Modular Border Bar
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
# AGY runs this command in a background pipe, so we query /dev/tty directly
WIDTH=""
if [ -c /dev/tty ]; then
    WIDTH=$(stty size < /dev/tty 2>/dev/null | awk '{print $2}')
fi
[ -z "$WIDTH" ] && WIDTH=$(tput cols 2>/dev/null)
[ -z "$WIDTH" ] && WIDTH="${COLUMNS:-160}"

# If not in a git repo, display a clean placeholder border
if ! git -C "$TARGET_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    CONTENT="${RED}Git: not a repository${RESET}"
    RAW_CONTENT="Git: not a repository"
    if [ "$SEGMENT_MODE" = true ]; then
        printf "%b\t%s\n" "$CONTENT" "$RAW_CONTENT"
        exit 0
    fi
    PAD_LEN=$(( WIDTH - 6 - ${#RAW_CONTENT} ))
    [ $PAD_LEN -lt 2 ] && PAD_LEN=2
    FILL=$(printf '─%.0s' $(seq 1 $PAD_LEN))
    echo -e "${GRAY}───[ ${CONTENT} ${GRAY}]${FILL}${RESET}"
    exit 0
fi

# =============================================================================
# SEGMENT 1: Git Branch
# =============================================================================
BRANCH=$(git -C "$TARGET_DIR" branch --show-current 2>/dev/null)
[ -z "$BRANCH" ] && BRANCH=$(git -C "$TARGET_DIR" rev-parse --short HEAD 2>/dev/null || echo "detached")

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
# SEGMENT 4: Unpushed Live Commits
# =============================================================================
if git -C "$TARGET_DIR" rev-parse --abbrev-ref @{u} >/dev/null 2>&1; then
    UPSTREAM=$(git -C "$TARGET_DIR" rev-parse --abbrev-ref @{u} 2>/dev/null)
    AHEAD=$(git -C "$TARGET_DIR" rev-list @{u}..HEAD --count 2>/dev/null || echo 0)
    if [ "$AHEAD" -gt 0 ]; then
        SEG_UNPUSHED="${YELLOW}⚠ ${AHEAD} unpushed${RESET}"
        RAW_UNPUSHED="⚠ ${AHEAD} unpushed"
    else
        SEG_UNPUSHED="${GREEN}✓ synced${RESET}"
        RAW_UNPUSHED="✓ synced"
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
PREFIX_LEN=4  # length of "───["
SUFFIX_LEN=1  # length of "]"
RAW_TOTAL=$(( PREFIX_LEN + 1 + ${#RAW_CONTENT} + 1 + SUFFIX_LEN ))

FILL_LEN=$(( WIDTH - RAW_TOTAL ))
[ $FILL_LEN -lt 2 ] && FILL_LEN=2

LINE_FILL=$(printf '─%.0s' $(seq 1 $FILL_LEN))

echo -e "${GRAY}───[ ${RESET}${CONTENT} ${GRAY}]${LINE_FILL}${RESET}"
