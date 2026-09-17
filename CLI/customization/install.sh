#!/usr/bin/env bash
#
# Antigravity (AGY) CLI Customization Installer & TUI Configurator
#
# Allows users to interactively select which status line components and options
# to install and configure in their global settings.json.
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GLOBAL_SCRIPTS_DIR="$HOME/.gemini/antigravity-cli/scripts"
WORKSPACE_SCRIPTS_DIR="$PWD/.agents/scripts"
GLOBAL_SETTINGS_FILE="$HOME/.gemini/antigravity-cli/settings.json"

# --- Colors & ANSI Formatting ---
BOLD="\033[1m"
DIM="\033[2m"
CYAN="\033[36m"
GREEN="\033[32m"
YELLOW="\033[33m"
BLUE="\033[34m"
MAGENTA="\033[35m"
GRAY="\033[90m"
RED="\033[31m"
RESET="\033[0m"
CLEAR_LINE="\033[K"
HIDE_CURSOR="\033[?25l"
SHOW_CURSOR="\033[?25h"

# --- State / Options ---
# Index 0: Git Status
# Index 1: Session & Round Timers
# Index 2: Digital Timer Format
# Index 3: Stack With Default Bar
# Index 4: Global Installation (~/.gemini/antigravity-cli/scripts/)
# Index 5: Local Workspace Installation (.agents/scripts/)
# Index 6: Auto-configure settings.json
OPTIONS_LABEL=(
    "Git Status Module           Real-time branch, staged, unstaged & remote sync"
    "Session & Round Timers      Session duration & live prompt execution stopwatch"
    "Digital Timer Format        Display timers as 01:23:45 instead of 1h 23m 45s"
    "Stack With Default Bar      Keep AGY model/token status line visible"
    "Global Installation         Install to ~/.gemini/antigravity-cli/scripts/ (recommended)"
    "Local Workspace Copy        Copy to current project's .agents/scripts/"
    "Configure settings.json     Auto-update ~/.gemini/antigravity-cli/settings.json"
)

SELECTED=(1 1 0 1 1 0 1)
CURRENT_INDEX=0
TOTAL_ITEMS=${#OPTIONS_LABEL[@]}
NON_INTERACTIVE=false

# --- Parse Command Line Flags ---
print_help() {
    cat << EOF
Antigravity (AGY) CLI Customization Installer

Usage:
  ./install.sh [options]

Interactive Mode:
  Run without arguments in a terminal to launch the interactive TUI.

Options:
  --all               Install all modules and apply global settings (default recommended)
  --git-only          Install only Git status module
  --timer-only        Install only Session & Round timer module
  --digital           Enable digital timer style (HH:MM:SS)
  --verbose           Enable verbose timer style (e.g. 14m 32s) [default]
  --no-stack          Do not stack with default status bar
  --global            Install scripts globally to ~/.gemini/antigravity-cli/scripts/
  --workspace         Copy scripts locally to .agents/scripts/
  --no-settings       Do not modify ~/.gemini/antigravity-cli/settings.json
  -y, --yes           Non-interactive mode: accept selections and install immediately
  -h, --help          Show this help message
EOF
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --all)
            SELECTED=(1 1 0 1 1 0 1)
            NON_INTERACTIVE=true
            shift
            ;;
        --git-only)
            SELECTED=(1 0 0 1 1 0 1)
            NON_INTERACTIVE=true
            shift
            ;;
        --timer-only)
            SELECTED=(0 1 0 1 1 0 1)
            NON_INTERACTIVE=true
            shift
            ;;
        --digital)
            SELECTED[2]=1
            NON_INTERACTIVE=true
            shift
            ;;
        --verbose)
            SELECTED[2]=0
            NON_INTERACTIVE=true
            shift
            ;;
        --no-stack)
            SELECTED[3]=0
            NON_INTERACTIVE=true
            shift
            ;;
        --global)
            SELECTED[4]=1
            NON_INTERACTIVE=true
            shift
            ;;
        --workspace)
            SELECTED[5]=1
            NON_INTERACTIVE=true
            shift
            ;;
        --no-settings)
            SELECTED[6]=0
            NON_INTERACTIVE=true
            shift
            ;;
        -y|--yes)
            NON_INTERACTIVE=true
            shift
            ;;
        -h|--help)
            print_help
            ;;
        *)
            echo "Unknown option: $1"
            print_help
            ;;
    esac
done

# If stdin is not a terminal and not explicitly interactive, fallback to non-interactive
if [ ! -t 0 ] && [ "$NON_INTERACTIVE" = false ]; then
    if [ ! -c /dev/tty ]; then
        NON_INTERACTIVE=true
    fi
fi

# --- Helper: Render Live Preview ---
render_preview() {
    local has_git=${SELECTED[0]}
    local has_timer=${SELECTED[1]}
    local is_digital=${SELECTED[2]}
    local preview_line=""

    if [ "$has_git" -eq 1 ] && [ "$has_timer" -eq 1 ]; then
        if [ "$is_digital" -eq 1 ]; then
            preview_line="${GRAY}───[ ${CYAN} main ${GRAY}│ ${RESET}0 staged ${GRAY}│ ${RESET}0 unstaged ${GRAY}│ ${GREEN}✓ synced ${GRAY}]───[ ${MAGENTA}󱎫 00:14:32 ${GRAY}│ ${YELLOW}󰔛 00:00:03 ${GRAY}]───${RESET}"
        else
            preview_line="${GRAY}───[ ${CYAN} main ${GRAY}│ ${RESET}0 staged ${GRAY}│ ${RESET}0 unstaged ${GRAY}│ ${GREEN}✓ synced ${GRAY}]───[ ${MAGENTA}󱎫 14m 32s ${GRAY}│ ${YELLOW}󰔛 3.8s ${GRAY}]───${RESET}"
        fi
    elif [ "$has_git" -eq 1 ]; then
        preview_line="${GRAY}───[ ${CYAN} main ${GRAY}│ ${RESET}0 staged ${GRAY}│ ${RESET}0 unstaged ${GRAY}│ ${GREEN}✓ synced ${GRAY}]──────────────────────────${RESET}"
    elif [ "$has_timer" -eq 1 ]; then
        if [ "$is_digital" -eq 1 ]; then
            preview_line="${GRAY}───[ ${MAGENTA}󱎫 00:14:32 ${GRAY}│ ${YELLOW}󰔛 00:00:03 ${GRAY}]──────────────────────────────────────${RESET}"
        else
            preview_line="${GRAY}───[ ${MAGENTA}󱎫 14m 32s ${GRAY}│ ${YELLOW}󰔛 3.8s ${GRAY}]──────────────────────────────────────────${RESET}"
        fi
    else
        preview_line="${RED}(No status line modules selected)${RESET}"
    fi

    echo -e "$preview_line"
}

# --- Cleanup on Exit ---
cleanup() {
    printf "${SHOW_CURSOR}" >/dev/tty 2>/dev/null || true
    if [ -c /dev/tty ]; then
        stty echo icanon < /dev/tty 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# --- TUI Render Function ---
RENDERED_LINES=0

draw_tui() {
    # Move cursor up if already rendered to prevent flicker
    if [ "$RENDERED_LINES" -gt 0 ]; then
        printf "\033[%dA" "$RENDERED_LINES"
    fi

    local lines_out=0

    # Header
    echo -e "${BOLD}${CYAN}┌─────────────────────────────────────────────────────────────┐${RESET}${CLEAR_LINE}"
    echo -e "${BOLD}${CYAN}│        ${RESET}${BOLD}Antigravity (AGY) CLI Customization Setup${CYAN}            │${RESET}${CLEAR_LINE}"
    echo -e "${BOLD}${CYAN}│     ${RESET}${DIM}Interactive Status Line & Global Settings Configurator${CYAN}  │${RESET}${CLEAR_LINE}"
    echo -e "${BOLD}${CYAN}└─────────────────────────────────────────────────────────────┘${RESET}${CLEAR_LINE}"
    lines_out=$(( lines_out + 4 ))

    echo -e "${DIM}Navigation: [↑/k] Up  [↓/j] Down  [Space] Toggle  [Enter] Install  [q] Quit${RESET}${CLEAR_LINE}"
    echo -e "${CLEAR_LINE}"
    lines_out=$(( lines_out + 2 ))

    # Customization Items
    for i in "${!OPTIONS_LABEL[@]}"; do
        local marker=" "
        [ "${SELECTED[$i]}" -eq 1 ] && marker="${GREEN}✓${RESET}"

        local pointer="  "
        local line_style="${RESET}"
        if [ "$i" -eq "$CURRENT_INDEX" ]; then
            pointer="${CYAN}❯ ${RESET}"
            line_style="${BOLD}${CYAN}"
        fi

        echo -e "${pointer}[${marker}] ${line_style}${OPTIONS_LABEL[$i]}${RESET}${CLEAR_LINE}"
        lines_out=$(( lines_out + 1 ))
    done

    echo -e "${CLEAR_LINE}"
    echo -e "${BOLD}Live Preview:${RESET}${CLEAR_LINE}"
    echo -e "  $(render_preview)${CLEAR_LINE}"
    echo -e "${CLEAR_LINE}"
    lines_out=$(( lines_out + 4 ))

    RENDERED_LINES=$lines_out
}

# --- Interactive Event Loop ---
run_interactive_tui() {
    # Prepare terminal
    printf "${HIDE_CURSOR}" >/dev/tty 2>/dev/null || true
    stty -echo -icanon min 1 time 0 < /dev/tty 2>/dev/null || true

    while true; do
        draw_tui

        # Read single keypress or escape sequence from /dev/tty
        local key=""
        IFS= read -rsn1 key < /dev/tty 2>/dev/null || true

        if [[ "$key" == $'\x1b' ]]; then
            local next_keys=""
            read -rsn2 -t 0.1 next_keys < /dev/tty 2>/dev/null || true
            key+="$next_keys"
        fi

        case "$key" in
            $'\x1b[A'|k|K) # Up Arrow or k
                CURRENT_INDEX=$(( (CURRENT_INDEX - 1 + TOTAL_ITEMS) % TOTAL_ITEMS ))
                ;;
            $'\x1b[B'|j|J) # Down Arrow or j
                CURRENT_INDEX=$(( (CURRENT_INDEX + 1) % TOTAL_ITEMS ))
                ;;
            " ") # Space (toggle)
                SELECTED[$CURRENT_INDEX]=$(( 1 - SELECTED[$CURRENT_INDEX] ))
                ;;
            a|A) # Toggle all modules (0 and 1)
                local new_val=$(( 1 - SELECTED[0] ))
                SELECTED[0]=$new_val
                SELECTED[1]=$new_val
                ;;
            "") # Enter (Confirm)
                break
                ;;
            q|Q|$'\x03') # Quit or Ctrl+C
                echo -e "\n${YELLOW}Installation cancelled by user.${RESET}"
                exit 0
                ;;
        esac
    done

    cleanup
    echo -e "\n"
}

# --- Main Installation Logic ---
install_customizations() {
    local has_git=${SELECTED[0]}
    local has_timer=${SELECTED[1]}
    local is_digital=${SELECTED[2]}
    local stack_default=${SELECTED[3]}
    local install_global=${SELECTED[4]}
    local install_workspace=${SELECTED[5]}
    local update_settings=${SELECTED[6]}

    if [ "$has_git" -eq 0 ] && [ "$has_timer" -eq 0 ]; then
        echo -e "${RED}Error: Neither Git Status nor Session/Round Timers was selected.${RESET}"
        echo "Please select at least one status line module to install."
        exit 1
    fi

    if [ "$install_global" -eq 0 ] && [ "$install_workspace" -eq 0 ] && [ "$update_settings" -eq 1 ]; then
        echo -e "${YELLOW}Warning: Neither Global nor Workspace destination was selected.${RESET}"
        echo "Defaulting to Global installation (~/.gemini/antigravity-cli/scripts/)."
        install_global=1
    fi

    echo -e "${BOLD}${CYAN}Installing AGY CLI Customizations...${RESET}\n"

    # 1. Global Copy
    if [ "$install_global" -eq 1 ]; then
        mkdir -p "$GLOBAL_SCRIPTS_DIR"
        echo -e "  ${GREEN}✓${RESET} Target directory: ${BOLD}$GLOBAL_SCRIPTS_DIR${RESET}"

        cp "$SCRIPT_DIR/status_bar.sh" "$GLOBAL_SCRIPTS_DIR/" 2>/dev/null || true
        cp "$SCRIPT_DIR/git_status_bar.sh" "$GLOBAL_SCRIPTS_DIR/" 2>/dev/null || true
        cp "$SCRIPT_DIR/timer_status_bar.sh" "$GLOBAL_SCRIPTS_DIR/" 2>/dev/null || true
        chmod +x "$GLOBAL_SCRIPTS_DIR"/*.sh
        echo -e "  ${GREEN}✓${RESET} Copied status line modules to global scripts directory"
    fi

    # 2. Local Workspace Copy
    if [ "$install_workspace" -eq 1 ]; then
        mkdir -p "$WORKSPACE_SCRIPTS_DIR"
        echo -e "  ${GREEN}✓${RESET} Target directory: ${BOLD}$WORKSPACE_SCRIPTS_DIR${RESET}"

        cp "$SCRIPT_DIR/status_bar.sh" "$WORKSPACE_SCRIPTS_DIR/" 2>/dev/null || true
        cp "$SCRIPT_DIR/git_status_bar.sh" "$WORKSPACE_SCRIPTS_DIR/" 2>/dev/null || true
        cp "$SCRIPT_DIR/timer_status_bar.sh" "$WORKSPACE_SCRIPTS_DIR/" 2>/dev/null || true
        chmod +x "$WORKSPACE_SCRIPTS_DIR"/*.sh
        echo -e "  ${GREEN}✓${RESET} Copied status line modules to workspace .agents/scripts/"
    fi

    # 3. Determine Command to Configure
    local target_cmd=""
    local base_path=""

    if [ "$install_global" -eq 1 ]; then
        base_path="~/.gemini/antigravity-cli/scripts"
    else
        base_path=".agents/scripts"
    fi

    if [ "$has_git" -eq 1 ] && [ "$has_timer" -eq 1 ]; then
        target_cmd="$base_path/status_bar.sh"
        [ "$is_digital" -eq 1 ] && target_cmd="$target_cmd --digital"
    elif [ "$has_git" -eq 1 ]; then
        target_cmd="$base_path/git_status_bar.sh"
    elif [ "$has_timer" -eq 1 ]; then
        target_cmd="$base_path/timer_status_bar.sh"
        [ "$is_digital" -eq 1 ] && target_cmd="$target_cmd --digital"
    fi

    # 4. Update Global Settings JSON
    if [ "$update_settings" -eq 1 ]; then
        echo -e "  ${GREEN}✓${RESET} Updating global settings: ${BOLD}$GLOBAL_SETTINGS_FILE${RESET}"

        python3 - << EOF
import json, os

settings_path = os.path.expanduser("$GLOBAL_SETTINGS_FILE")
os.makedirs(os.path.dirname(settings_path), exist_ok=True)

data = {}
if os.path.exists(settings_path):
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Warning: could not parse existing settings.json ({e}), initializing clean settings.")

data["statusLine"] = {
    "type": "command",
    "command": "$target_cmd",
    "interval": 2,
    "enabled": True,
    "stack_with_default": bool($stack_default)
}

with open(settings_path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)

print("  \033[32m✓\033[0m Successfully configured statusLine in settings.json")
EOF
    fi

    # 5. Live Test Run
    echo -e "\n${BOLD}Verification Test Run:${RESET}"
    local test_exec="${target_cmd/#\~/$HOME}"
    if [ -x "$test_exec" ] || [[ "$test_exec" == *" "* ]]; then
        echo -e "  Executing: ${DIM}$test_exec${RESET}"
        echo -ne "  Output:    "
        bash -c "$test_exec" || true
    fi

    # 6. Final Summary
    echo -e "\n${BOLD}${GREEN}Setup Complete!${RESET}"
    echo -e "─────────────────────────────────────────────────────────────"
    echo -e "Configured Command: ${CYAN}$target_cmd${RESET}"
    echo -e "Settings File:      ${CYAN}$GLOBAL_SETTINGS_FILE${RESET}"
    echo -e "Stack With Default: ${CYAN}$([ "$stack_default" -eq 1 ] && echo "true" || echo "false")${RESET}"
    echo -e "─────────────────────────────────────────────────────────────"
    echo -e "Restart ${BOLD}agy${RESET} or run ${CYAN}/statusline $target_cmd${RESET} to activate immediately.\n"
}

# --- Execution Entrypoint ---
if [ "$NON_INTERACTIVE" = true ]; then
    install_customizations
else
    run_interactive_tui
    install_customizations
fi
