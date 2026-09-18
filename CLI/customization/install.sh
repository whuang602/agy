#!/usr/bin/env bash
#
# Antigravity (AGY) CLI Customization Installer & TUI Configurator
#
# Allows users to interactively select which status line components and options
# to install and configure in their global settings.json.
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GLOBAL_SCRIPTS_DIR="${GLOBAL_SCRIPTS_DIR:-$HOME/.gemini/antigravity-cli/scripts}"
WORKSPACE_SCRIPTS_DIR="${WORKSPACE_SCRIPTS_DIR:-$PWD/.agents/scripts}"
GLOBAL_SETTINGS_FILE="${GLOBAL_SETTINGS_FILE:-$HOME/.gemini/antigravity-cli/settings.json}"

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
# Index 0: Git Status Module
# Index 1: Session & Round Timers
# Index 2: Consumer Account Quota
# Index 3: Digital Timer Format
# Index 4: Stack With Default Bar
# Index 5: Global Installation (~/.gemini/antigravity-cli/scripts/)
# Index 6: Local Workspace Copy (.agents/scripts/)
# Index 7: Configure settings.json
OPTIONS_LABEL=(
    "Git Status Module           Real-time branch, staged, unstaged & remote sync"
    "Session & Round Timers      Session duration & live prompt execution stopwatch"
    "Consumer Account Quota      Display daily requests % & reset countdown"
    "Digital Timer Format        Display timers as 01:23:45 instead of 1h 23m 45s"
    "Stack With Default Bar      Keep AGY model/token status line visible"
    "Global Installation         Install to ~/.gemini/antigravity-cli/scripts/ (recommended)"
    "Local Workspace Copy        Copy to current project's .agents/scripts/"
    "Configure settings.json     Auto-update ~/.gemini/antigravity-cli/settings.json"
)

SELECTED=(1 1 1 0 1 1 0 1)
CURRENT_INDEX=0
TOTAL_ITEMS=${#OPTIONS_LABEL[@]}
NON_INTERACTIVE=false

# --- Parse Command Line Flags ---
print_help() {
    local exit_code="${1:-0}"
    cat << EOF
Antigravity (AGY) CLI Customization Installer

Usage:
  ./install.sh [options]

Interactive Mode:
  Run without arguments in a terminal to launch the interactive TUI.

Options:
  --all               Enable Git, Timer, and Quota modules [default]
  --git-only          Enable only Git status module
  --timer-only        Enable only Session & Round timer module
  --quota-only        Enable only Consumer Account Quota module
  --quota             Enable Consumer Account Quota module
  --no-quota          Disable Consumer Account Quota module
  --digital           Enable digital timer style (HH:MM:SS)
  --verbose           Enable verbose timer style (e.g. 14m 32s) [default]
  --stack             Stack with default status bar [default]
  --no-stack          Do not stack with default status bar
  --global            Install scripts globally to ~/.gemini/antigravity-cli/scripts/
  --workspace         Copy scripts locally to .agents/scripts/
  --no-settings       Do not modify ~/.gemini/antigravity-cli/settings.json
  -u, --uninstall     Remove status line configuration and installed scripts
  -y, --yes           Non-interactive mode: accept selections and install immediately
  -h, --help          Show this help message

Note:
  Consumer Account Quota automatically suppresses itself if using an Enterprise account or API key.
EOF
    exit "$exit_code"
}

# --- Uninstall Customizations ---
uninstall_customizations() {
    echo -e "${BOLD}${CYAN}Uninstalling AGY CLI Customizations...${RESET}\n"

    # 1. Remove statusLine from settings.json atomically via Python
    local settings_rc=0
    if ! command -v python3 >/dev/null 2>&1; then
        echo -e "  ${YELLOW}!${RESET} python3 not found; skipping settings.json cleanup." >&2
        settings_rc=127
    else
        local settings_path
        settings_path=$(python3 -c "import os, sys; print(os.path.expanduser(sys.argv[1]))" "$GLOBAL_SETTINGS_FILE" 2>/dev/null || echo "$GLOBAL_SETTINGS_FILE")
        export TARGET_SETTINGS_PATH="$settings_path"

        python3 - << 'PYEOF' || settings_rc=$?
import json, os, sys, tempfile, shutil

settings_path = os.environ.get("TARGET_SETTINGS_PATH")
if not settings_path or not os.path.exists(settings_path):
    print("  \033[90m-\033[0m No settings.json found to modify.")
    sys.exit(0)

try:
    with open(settings_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            print("  \033[90m-\033[0m settings.json is empty; nothing to remove.")
            sys.exit(0)
        data = json.loads(content)
        if not isinstance(data, dict):
            print("  \033[33m!\033[0m Warning: settings.json root is not an object. Leaving untouched.", file=sys.stderr)
            sys.exit(0)
except Exception as e:
    print(f"  \033[31m✗\033[0m Error reading settings.json: {e}", file=sys.stderr)
    sys.exit(1)

sl = data.get("statusLine")
ours = ("status_bar.sh", "git_status_bar.sh", "timer_status_bar.sh", "quota_status_bar.sh")
cmd = (sl or {}).get("command", "") if isinstance(sl, dict) else ""

if sl is None:
    print("  \033[90m-\033[0m No statusLine configuration found in settings.json")
elif not any(s in cmd for s in ours):
    print("  \033[33m!\033[0m Warning: statusLine does not reference AGY customization scripts; leaving it untouched.", file=sys.stderr)
else:
    try:
        shutil.copy2(settings_path, settings_path + ".bak")
    except Exception as e:
        print(f"  \033[33m!\033[0m Warning: Failed to create settings.json.bak: {e}", file=sys.stderr)

    del data["statusLine"]
    settings_dir = os.path.dirname(settings_path)
    temp_fd, temp_path = tempfile.mkstemp(prefix="settings.", suffix=".tmp", dir=settings_dir if settings_dir else None)
    try:
        try:
            orig_mode = os.stat(settings_path).st_mode
            os.chmod(temp_path, orig_mode)
        except Exception:
            pass
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, settings_path)
        print("  \033[32m✓\033[0m Removed statusLine configuration from settings.json")
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        print(f"  \033[31m✗\033[0m Failed to update settings.json atomically: {e}", file=sys.stderr)
        sys.exit(1)
PYEOF
    fi

    if [ "$settings_rc" -ne 0 ] && [ "$settings_rc" -ne 127 ]; then
        echo -e "  ${YELLOW}!${RESET} Warning: settings.json could not be updated (exit code $settings_rc)." >&2
    fi

    # 2. Remove customization scripts from global scripts directory
    local custom_scripts=("status_bar.sh" "git_status_bar.sh" "timer_status_bar.sh" "quota_status_bar.sh")
    local removed_global=0
    if [ -d "$GLOBAL_SCRIPTS_DIR" ]; then
        for script in "${custom_scripts[@]}"; do
            if [ -f "$GLOBAL_SCRIPTS_DIR/$script" ]; then
                rm -f "$GLOBAL_SCRIPTS_DIR/$script"
                removed_global=$(( removed_global + 1 ))
            fi
        done
        if [ "$removed_global" -gt 0 ]; then
            echo -e "  ${GREEN}✓${RESET} Removed $removed_global customization script(s) from ${BOLD}$GLOBAL_SCRIPTS_DIR${RESET}"
        else
            echo -e "  ${GRAY}-${RESET} No customization scripts found in ${BOLD}$GLOBAL_SCRIPTS_DIR${RESET}"
        fi
        rmdir "$GLOBAL_SCRIPTS_DIR" 2>/dev/null || true
    else
        echo -e "  ${GRAY}-${RESET} Global scripts directory does not exist: ${BOLD}$GLOBAL_SCRIPTS_DIR${RESET}"
    fi

    # 3. Remove customization scripts from workspace scripts directory if present
    local removed_workspace=0
    if [ -d "$WORKSPACE_SCRIPTS_DIR" ]; then
        for script in "${custom_scripts[@]}"; do
            if [ -f "$WORKSPACE_SCRIPTS_DIR/$script" ]; then
                rm -f "$WORKSPACE_SCRIPTS_DIR/$script"
                removed_workspace=$(( removed_workspace + 1 ))
            fi
        done
        if [ "$removed_workspace" -gt 0 ]; then
            echo -e "  ${GREEN}✓${RESET} Removed $removed_workspace customization script(s) from ${BOLD}$WORKSPACE_SCRIPTS_DIR${RESET}"
        else
            echo -e "  ${GRAY}-${RESET} No customization scripts found in ${BOLD}$WORKSPACE_SCRIPTS_DIR${RESET}"
        fi
        rmdir "$WORKSPACE_SCRIPTS_DIR" 2>/dev/null || true
        rmdir "$(dirname "$WORKSPACE_SCRIPTS_DIR")" 2>/dev/null || true
    fi

    # 4. Clean up session cache files
    local cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}/antigravity"
    if [ -d "$cache_dir" ]; then
        local removed_cache=0
        for f in "$cache_dir"/session_* "$cache_dir"/agy_sess_*; do
            if [ -f "$f" ]; then
                rm -f "$f"
                removed_cache=$(( removed_cache + 1 ))
            fi
        done
        if [ "$removed_cache" -gt 0 ]; then
            echo -e "  ${GREEN}✓${RESET} Removed $removed_cache session cache file(s) from ${BOLD}$cache_dir${RESET}"
        else
            echo -e "  ${GRAY}-${RESET} No session cache files found in ${BOLD}$cache_dir${RESET}"
        fi
    fi

    # 5. Summary Message
    echo -e "\n${BOLD}${GREEN}Uninstall Complete!${RESET}"
    echo -e "─────────────────────────────────────────────────────────────"
    echo -e "Removed status line configuration from settings.json."
    echo -e "Removed customization scripts and session caches."
    echo -e "─────────────────────────────────────────────────────────────"
    echo -e "Restart ${BOLD}agy${RESET} to restore default status line behavior.\n"
}

while [ $# -gt 0 ]; do
    case "$1" in
        -u|--uninstall)
            uninstall_customizations
            exit 0
            ;;
        --all)
            SELECTED[0]=1
            SELECTED[1]=1
            SELECTED[2]=1
            NON_INTERACTIVE=true
            shift
            ;;
        --git-only)
            SELECTED[0]=1
            SELECTED[1]=0
            SELECTED[2]=0
            NON_INTERACTIVE=true
            shift
            ;;
        --timer-only)
            SELECTED[0]=0
            SELECTED[1]=1
            SELECTED[2]=0
            NON_INTERACTIVE=true
            shift
            ;;
        --quota-only)
            SELECTED[0]=0
            SELECTED[1]=0
            SELECTED[2]=1
            NON_INTERACTIVE=true
            shift
            ;;
        --quota)
            SELECTED[2]=1
            NON_INTERACTIVE=true
            shift
            ;;
        --no-quota)
            SELECTED[2]=0
            NON_INTERACTIVE=true
            shift
            ;;
        --digital)
            SELECTED[3]=1
            NON_INTERACTIVE=true
            shift
            ;;
        --verbose)
            SELECTED[3]=0
            NON_INTERACTIVE=true
            shift
            ;;
        --stack)
            SELECTED[4]=1
            NON_INTERACTIVE=true
            shift
            ;;
        --no-stack)
            SELECTED[4]=0
            NON_INTERACTIVE=true
            shift
            ;;
        --global)
            SELECTED[5]=1
            SELECTED[6]=0
            NON_INTERACTIVE=true
            shift
            ;;
        --workspace)
            SELECTED[6]=1
            SELECTED[5]=0
            NON_INTERACTIVE=true
            shift
            ;;
        --no-settings)
            SELECTED[7]=0
            NON_INTERACTIVE=true
            shift
            ;;
        -y|--yes)
            NON_INTERACTIVE=true
            shift
            ;;
        -h|--help)
            print_help 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            print_help 1
            ;;
    esac
done

# If stdin is not a terminal and not explicitly interactive, fallback to non-interactive
if [ ! -t 0 ] && [ "$NON_INTERACTIVE" = false ]; then
    if ! { true >/dev/null 2>&1 < /dev/tty; }; then
        NON_INTERACTIVE=true
    fi
fi

# --- Helper: Render Live Preview ---
render_preview() {
    local has_git=${SELECTED[0]}
    local has_timer=${SELECTED[1]}
    local has_quota=${SELECTED[2]}
    local is_digital=${SELECTED[3]}

    local prev_segments=()
    if [ "$has_git" -eq 1 ]; then
        prev_segments+=("${CYAN} main ${GRAY}│ ${RESET}0 staged ${GRAY}│ ${RESET}0 unstaged ${GRAY}│ ${GREEN}✓ synced")
    fi
    if [ "$has_timer" -eq 1 ]; then
        if [ "$is_digital" -eq 1 ]; then
            prev_segments+=("${MAGENTA}󱎫 00:14:32 ${GRAY}│ ${YELLOW}󰔛 00:00:03")
        else
            prev_segments+=("${MAGENTA}󱎫 14m 32s ${GRAY}│ ${YELLOW}󰔛 3.8s")
        fi
    fi
    if [ "$has_quota" -eq 1 ]; then
        prev_segments+=("${GREEN}󱓞 91.5% (5h) ${GRAY}· ${GREEN}80.8% (wk) ${GRAY}│ 󰔟 1h 59m")
    fi

    if [ ${#prev_segments[@]} -eq 0 ]; then
        echo -e "${RED}(No status line modules selected)${RESET}"
        return 0
    fi

    local prev_line="${GRAY}───[ ${RESET}${prev_segments[0]}${GRAY} ]"
    for (( i=1; i<${#prev_segments[@]}; i++ )); do
        prev_line="${prev_line}───[ ${RESET}${prev_segments[$i]}${GRAY} ]"
    done
    prev_line="${prev_line}───${RESET}"

    echo -e "$prev_line"
}

# --- Cleanup on Exit & Signal Handling ---
cleanup() {
    if { true >/dev/null 2>&1 >/dev/tty; }; then
        printf "${SHOW_CURSOR}" 2>/dev/null >/dev/tty || true
    fi
    if { true >/dev/null 2>&1 < /dev/tty; }; then
        stty echo icanon 2>/dev/null < /dev/tty || true
    fi
}

on_interrupt() {
    cleanup
    echo -e "\n${YELLOW}Installation cancelled.${RESET}" >&2
    exit 130
}

trap cleanup EXIT
trap on_interrupt INT TERM

# --- TUI Render Function ---
RENDERED_LINES=0

draw_tui() {
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

    echo -e "${DIM}Navigation: [↑/k] Up  [↓/j] Down  [Space] Toggle  [Enter] Install  [u] Uninstall  [q] Quit${RESET}${CLEAR_LINE}"
    echo -e "${DIM}Note: Quota suppresses itself on Enterprise accounts or API keys.${RESET}${CLEAR_LINE}"
    echo -e "${CLEAR_LINE}"
    lines_out=$(( lines_out + 3 ))

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
    if ! { true >/dev/null 2>&1 < /dev/tty; }; then
        echo -e "${RED}Error: Interactive mode requires a controlling terminal (/dev/tty).${RESET}" >&2
        echo "Use non-interactive flags or --yes." >&2
        exit 1
    fi

    printf "${HIDE_CURSOR}" 2>/dev/null >/dev/tty || true
    stty -echo -icanon min 1 time 0 2>/dev/null < /dev/tty || true

    while true; do
        draw_tui

        local key=""
        if ! IFS= read -rsn1 key 2>/dev/null < /dev/tty; then
            cleanup
            echo -e "\n${RED}Input stream closed or EOF received. Aborting.${RESET}" >&2
            exit 1
        fi

        if [[ "$key" == $'\x1b' ]]; then
            local next_keys=""
            read -rsn2 -t 0.1 next_keys 2>/dev/null < /dev/tty || true
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
            a|A) # Toggle all modules (0, 1, and 2)
                local new_val=$(( 1 - SELECTED[0] ))
                SELECTED[0]=$new_val
                SELECTED[1]=$new_val
                SELECTED[2]=$new_val
                ;;
            u|U) # Uninstall
                cleanup
                echo -ne "\n${YELLOW}Are you sure you want to remove all AGY status line customizations? [y/N]: ${RESET}"
                local confirm=""
                read -r -n1 confirm < /dev/tty || true
                echo
                if [[ "$confirm" =~ ^[yY]$ ]]; then
                    uninstall_customizations
                    exit 0
                else
                    echo -e "${CYAN}Uninstall cancelled.${RESET}"
                    exit 0
                fi
                ;;
            "") # Enter (Confirm)
                break
                ;;
            q|Q) # Quit
                cleanup
                echo -e "\n${CYAN}Installation exited by user.${RESET}"
                exit 0
                ;;
            $'\x03') # Ctrl+C
                cleanup
                echo -e "\n${YELLOW}Installation cancelled by user.${RESET}"
                exit 130
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
    local has_quota=${SELECTED[2]}
    local is_digital=${SELECTED[3]}
    local stack_default=${SELECTED[4]}
    local install_global=${SELECTED[5]}
    local install_workspace=${SELECTED[6]}
    local update_settings=${SELECTED[7]}

    if [ "$has_git" -eq 0 ] && [ "$has_timer" -eq 0 ] && [ "$has_quota" -eq 0 ]; then
        echo -e "${RED}Error: No status line modules were selected.${RESET}"
        echo "Please select at least one of Git, Timer, or Quota to install."
        exit 1
    fi

    if [ "$install_global" -eq 0 ] && [ "$install_workspace" -eq 0 ] && [ "$update_settings" -eq 1 ]; then
        echo -e "${YELLOW}Warning: Neither Global nor Workspace destination was selected.${RESET}"
        echo "Defaulting to Global installation (~/.gemini/antigravity-cli/scripts/)."
        install_global=1
    fi

    # Pre-flight check for required tools
    for req in jq git python3; do
        if ! command -v "$req" >/dev/null 2>&1; then
            echo -e "${RED}Error: Required tool '$req' is not installed or not in PATH.${RESET}" >&2
            exit 1
        fi
    done

    # Verify source files exist
    for script_file in status_bar.sh git_status_bar.sh timer_status_bar.sh quota_status_bar.sh; do
        if [ ! -f "$SCRIPT_DIR/$script_file" ]; then
            echo -e "${RED}Error: Required script $SCRIPT_DIR/$script_file not found.${RESET}" >&2
            exit 1
        fi
    done

    echo -e "${BOLD}${CYAN}Installing AGY CLI Customizations...${RESET}\n"

    # 1. Global Copy
    if [ "$install_global" -eq 1 ]; then
        mkdir -p "$GLOBAL_SCRIPTS_DIR"
        echo -e "  ${GREEN}✓${RESET} Target directory: ${BOLD}$GLOBAL_SCRIPTS_DIR${RESET}"

        for script_file in status_bar.sh git_status_bar.sh timer_status_bar.sh quota_status_bar.sh; do
            cp "$SCRIPT_DIR/$script_file" "$GLOBAL_SCRIPTS_DIR/" || {
                echo -e "${RED}Error: Failed to copy $script_file to $GLOBAL_SCRIPTS_DIR${RESET}" >&2
                exit 1
            }
            chmod +x "$GLOBAL_SCRIPTS_DIR/$script_file"
        done
        echo -e "  ${GREEN}✓${RESET} Copied status line modules to global scripts directory"
    fi

    # 2. Local Workspace Copy
    if [ "$install_workspace" -eq 1 ]; then
        mkdir -p "$WORKSPACE_SCRIPTS_DIR"
        echo -e "  ${GREEN}✓${RESET} Target directory: ${BOLD}$WORKSPACE_SCRIPTS_DIR${RESET}"

        for script_file in status_bar.sh git_status_bar.sh timer_status_bar.sh quota_status_bar.sh; do
            cp "$SCRIPT_DIR/$script_file" "$WORKSPACE_SCRIPTS_DIR/" || {
                echo -e "${RED}Error: Failed to copy $script_file to $WORKSPACE_SCRIPTS_DIR${RESET}" >&2
                exit 1
            }
            chmod +x "$WORKSPACE_SCRIPTS_DIR/$script_file"
        done
        echo -e "  ${GREEN}✓${RESET} Copied status line modules to workspace .agents/scripts/"
    fi

    # 3. Determine Command to Configure
    local base_path=""
    local script_name=""
    local script_args=()

    if [ "$install_global" -eq 1 ]; then
        base_path="$GLOBAL_SCRIPTS_DIR"
    else
        base_path=".agents/scripts"
    fi

    local total_mods=$(( has_git + has_timer + has_quota ))

    if [ "$total_mods" -eq 1 ]; then
        if [ "$has_git" -eq 1 ]; then
            script_name="git_status_bar.sh"
        elif [ "$has_timer" -eq 1 ]; then
            script_name="timer_status_bar.sh"
            [ "$is_digital" -eq 1 ] && script_args+=("--digital")
        elif [ "$has_quota" -eq 1 ]; then
            script_name="quota_status_bar.sh"
        fi
    else
        script_name="status_bar.sh"
        [ "$has_git" -eq 0 ] && script_args+=("--no-git")
        [ "$has_timer" -eq 0 ] && script_args+=("--no-timer")
        [ "$has_quota" -eq 0 ] && script_args+=("--no-quota")
        [ "$is_digital" -eq 1 ] && script_args+=("--digital")
    fi

    local raw_script_path="$base_path/$script_name"

    # Safely escape command and arguments for shell display using printf '%q'
    local escaped_bin
    printf -v escaped_bin '%q' "$raw_script_path"
    local target_cmd="$escaped_bin"
    for arg in "${script_args[@]}"; do
        local escaped_arg
        printf -v escaped_arg '%q' "$arg"
        target_cmd="$target_cmd $escaped_arg"
    done

    # 4. Safely Update Global Settings JSON Atomically
    if [ "$update_settings" -eq 1 ]; then
        echo -e "  ${GREEN}✓${RESET} Updating global settings: ${BOLD}$GLOBAL_SETTINGS_FILE${RESET}"

        TARGET_SETTINGS_PATH=$(python3 -c "import os, sys; print(os.path.expanduser(sys.argv[1]))" "$GLOBAL_SETTINGS_FILE")
        export TARGET_SETTINGS_PATH
        export TARGET_BIN="$raw_script_path"
        TARGET_ARGS_JSON=$(python3 -c "import json, sys; print(json.dumps(sys.argv[1:]))" "${script_args[@]}")
        export TARGET_ARGS_JSON
        export STACK_DEFAULT="$stack_default"

        if ! python3 - << 'PYEOF'
import json, os, sys, tempfile, shlex, shutil

settings_path = os.environ.get("TARGET_SETTINGS_PATH")
target_bin = os.environ.get("TARGET_BIN")
target_args = json.loads(os.environ.get("TARGET_ARGS_JSON", "[]"))
stack_default = os.environ.get("STACK_DEFAULT") == "1"

# Build safely escaped command string using shlex.quote()
cmd_parts = [shlex.quote(target_bin)] + [shlex.quote(arg) for arg in target_args]
target_cmd = " ".join(cmd_parts)

settings_dir = os.path.dirname(settings_path)
if settings_dir:
    os.makedirs(settings_dir, exist_ok=True)

data = {}
orig_mode = None
if os.path.exists(settings_path):
    try:
        orig_mode = os.stat(settings_path).st_mode
        shutil.copy2(settings_path, settings_path + ".bak")
    except Exception as e:
        print(f"Warning: Failed to create settings.json.bak: {e}", file=sys.stderr)

    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content:
                data = json.loads(content)
                if not isinstance(data, dict):
                    print(f"Error: settings file at {settings_path} root is not a JSON object. Aborting to protect existing configuration.", file=sys.stderr)
                    sys.exit(1)
    except Exception as e:
        print(f"Error: Could not parse existing settings.json ({e}). Aborting to prevent data loss.", file=sys.stderr)
        sys.exit(1)

data["statusLine"] = {
    "type": "command",
    "command": target_cmd,
    "interval": 2,
    "enabled": True,
    "stack_with_default": stack_default
}

temp_fd, temp_path = tempfile.mkstemp(prefix="settings.", suffix=".tmp", dir=settings_dir if settings_dir else None)
try:
    if orig_mode is not None:
        try:
            os.chmod(temp_path, orig_mode)
        except Exception:
            pass
    with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_path, settings_path)
except Exception as e:
    if os.path.exists(temp_path):
        os.remove(temp_path)
    print(f"Error: Failed to write settings.json atomically ({e}). Aborting.", file=sys.stderr)
    sys.exit(1)

print("  \033[32m✓\033[0m Successfully configured statusLine in settings.json")
PYEOF
        then
            echo -e "${RED}Error: Failed to configure settings.json. Installation aborted.${RESET}" >&2
            exit 1
        fi
    fi

    # 5. Live Test Run (executed with sample JSON payload for realistic status bar output)
    echo -e "\n${BOLD}Verification Test Run:${RESET}"
    local -a test_cmd=("$raw_script_path" "${script_args[@]}")
    echo -e "  Executing: ${DIM}${test_cmd[*]}${RESET}"
    echo -ne "  Output:    "
    local sample_payload='{"cwd":"'"$PWD"'","model":{"id":"gemini-3.8-flash","display_name":"Gemini 3.8 Flash"},"quota":{"gemini-5h":{"remaining_fraction":0.915,"reset_in_seconds":7148},"gemini-weekly":{"remaining_fraction":0.808,"reset_in_seconds":470000}}}'
    echo "$sample_payload" | "${test_cmd[@]}" 2>/dev/null || "${test_cmd[@]}" < /dev/null || true

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
