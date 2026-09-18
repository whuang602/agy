# AGY CLI Customizations: Status Line Modules

A modular suite of dynamic TUI status line customizations for Google Antigravity (AGY) CLI. These scripts display real-time Git status, total session elapsed time, prompt-to-action round durations, and consumer account daily request quota directly inside your terminal interface.

---

## Overview of Scripts

Each module can be used **standalone** (rendering its own bordered line) or combined seamlessly into a **single unified line** via `status_bar.sh`:

| Script                     | Purpose                                                                           | Standalone Preview                                                                                 |
| :------------------------- | :-------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------- |
| **`status_bar.sh`**        | **Unified status line** composing Git + Timers + Quota in separate `[ ]` brackets | `───[  main │ 0 staged │ 0 unstaged │ ✓ synced ]───[ 󱎫 14m 32s │ 󰔛 3.8s ]───[ 󱓞 91.5% (5h) · 80.8% (wk) │ 󰔟 1h 59m ]───` |
| **`git_status_bar.sh`**    | Real-time Git branch, staging, untracked & remote sync (ahead/behind/diverged)    | `───[  main │ 0 staged │ 0 unstaged │ ✓ synced ]───────────────────────────────────────────────────` |
| **`timer_status_bar.sh`**  | Session uptime + prompt-to-action completion round timer                          | `───[ 󱎫 14m 32s │ 󰔛 3.8s ]─────────────────────────────────────────────────────────────────────────`   |
| **`quota_status_bar.sh`**  | Consumer Google account dual-window request quota % and 5-hour reset countdown     | `───[ 󱓞 91.5% (5h) · 80.8% (wk) │ 󰔟 1h 59m ]───────────────────────────────────────────────────────`   |

---

## Features

### 1. Unified Status Bar (`status_bar.sh`)
- **Modular Segment Composition**: Composes up to three active segments (`[ Git ]───[ Timer ]───[ Quota ]`) connected on the same visual line.
- **Dynamic Segment Suppression**: Cleanly omits bracket capsules and separators for any disabled or empty segment (e.g. when Quota is suppressed on enterprise accounts or Git is outside a repository).
- **Width-Aware Progressive Degradation**: Automatically compacts segments on narrow terminal viewports (e.g., 100-col and 80-col terminals) to prevent line wrapping:
  1. Clamps `FILL_LEN` floor to 0 (never forces padding when space is tight).
  2. If line exceeds terminal width, drops the reset countdown (`│ 󰔟 ...`) to save ~12 columns.
  3. If still too wide, drops the weekly quota badge, retaining only 5h demand badge (`󱓞 91.5% (5h)`).
  4. If still too wide, drops the entire quota segment.
  5. If still too wide, drops the timer segment.
- **Clean Empty Suppression**: When all enabled segments are empty/suppressed (e.g. an enterprise user with quota-only), cleanly exits 0 with zero stdout instead of rendering a meaningless `───[ AGY ]───` placeholder.
- **Terminal Width Bounds**: Clamps terminal `WIDTH` to `[20, 1000]` across all scripts, eliminating memory exhaustion or hangs from unbounded terminal sizes.
- **Payload Forwarding**: Preserves and pipes the AGY CLI stdin JSON payload to downstream segment scripts.
- **Customization Flags**: Supports `--no-git`, `--no-timer`, `--no-quota`, `--digital`, `--verbose`, and `--compact`.

### 2. Consumer Account Quota (`quota_status_bar.sh`)
- **Dual-Window Display (5-Hour & Weekly Limits)**: Renders both the 5-hour rolling demand window (`5h`) and total weekly capacity window (`wk`) side-by-side separated by a dim middle dot (`·`): `󱓞 91.5% (5h) · 80.8% (wk) │ 󰔟 1h 59m`.
- **Model Group Awareness**: Automatically detects the active model group from `.model.id` or `.model.display_name` in incoming JSON (safely handling `.model` whether passed as a dictionary object or a plain string):
  - **Gemini Models** (e.g. `Gemini 3.8 Flash`, `Gemini Pro`): selects `gemini-5h` and `gemini-weekly`.
  - **Third-Party Models** (e.g. `Claude 3.5 Sonnet`, `GPT-4o`, `OpenAI`, `Anthropic`, `3p`): selects `3p-5h` and `3p-weekly`.
  - **Consumption Fallback**: If model is unspecified or unrecognized, prioritizes whichever group has active consumption (`remaining_fraction < 1.0`), defaulting cleanly to `gemini` if idle.
  - **Generic Fallback**: Seamlessly supports un-prefixed keys (`5h`, `5_hour`, `weekly`, `week`) and flat structures.
- **Deterministic Integer-Tenths Formatting**:
  - Eliminates floating-point dtoa stringification discrepancies across `jq` versions (e.g. `80.799999999999997%` on older jq 1.5):
    - Displays 1 decimal place when fractional (e.g. `91.5%`, `80.8%`) using pure integer modulo arithmetic.
    - Displays whole integer when whole (e.g. `100%`, `50%`, `0%`).
- **Dynamic Color Thresholds (Per Window)**:
  - Green (`≥ 50%` remaining)
  - Yellow (`20% – 49%` remaining)
  - Red (`< 20%` remaining, including exhausted `0%`)
- **Unified Sliding Window Activation & Timer Suppression**:
  - **Single Source of Truth**: Evaluates sliding window activation directly against the displayed percentage (`$shown < 100`), eliminating dual-threshold discrepancy bugs.
  - **Untriggered / Idle Window (100%)**: When the 5-hour quota is untouched (100% remaining, `$shown == 100`), the 5-hour sliding countdown timer is dynamically suppressed (`󱓞 100% (5h) · 100% (wk)` or `quota: 100% (5h) · 100% (wk)`).
  - **Triggered / Active Window (< 100%)**: Once requests consume 5-hour quota (`$shown < 100`), displays the 5-hour reset countdown alongside the quota badges (`󱓞 91.5% (5h) · 80.8% (wk) │ 󰔟 1h 59m`).
- **Reset Countdown Clamping & Day Tier**:
  - Parses `reset_in_seconds` or ISO-8601 `reset_time` into a readable countdown with day tier support (e.g. `1d 4h`, `󰔟 1h 59m`, `<1m`).
  - Clamps reset durations to a maximum of 14 days (1,209,600s) to prevent runaway column blowout.
- **Labeled Single-Bucket Fallback**: Gracefully falls back to single-badge display with window label if known (e.g. `󱓞 80.8% (wk) │ 󰔟 5d 12h` or `󱓞 85% │ 󰔟 4h`).
- **Exhausted vs. Unavailable Quota**:
  - **Exhausted (0%)**: If quota reaches 0% (`remaining_fraction == 0.0`), renders red badge warnings (not suppressed).
  - **Unavailable / Enterprise**: If quota is `null`, empty `{}`, or all buckets are disabled (`disabled: true`), exits code 0 with zero output to allow clean bracket omission.
- **Formatting Styles**:
  - `compact` (default): Glyph-rich display (`󱓞 91.5% (5h) · 80.8% (wk) │ 󰔟 1h 59m`).
  - `verbose`: Worded display (`quota: 91.5% (5h) · 80.8% (wk) │ resets in 1h 59m`).
- **Authoritative Stdin Stream**: Reads exclusively from stdin JSON piped by AGY CLI. Never reads from shared files or `/tmp`. Cleanly suppresses output if stdin is empty.

### 3. Session & Round Timer (`timer_status_bar.sh`)
- **Total Session Time (`󱎫`)**: Calculates the elapsed time from session start (step 0 timestamp) to the present moment.
- **Round Duration (`󰔛` / `󱐋`)**:
  - **While Running (`󱐋`)**: Shows live ticking time elapsed since the user submitted the current prompt (e.g., `󱐋 14s`).
  - **When Completed (`󰔛`)**: Measures the exact duration from the prompt timestamp to the moment the action completed (e.g., `󰔛 3m 42s` or `<1s`).
- **Continuous Seconds Retention**: Seconds are never truncated across hours or minutes (e.g., `1h 10m 26s`), ensuring the timer always visibly increments on updates.
- **Display Styles**:
  - `verbose` (default): Units format (e.g., `1h 10m 26s`, `3m 17s`, `42s`).
  - `digital`: Clean digital clock / stopwatch format (e.g., `01:10:26`, `03:17`). Set via `export TIMER_STYLE=digital` or `--digital`.
- **Hardened Security & Cache**:
  - Safe ISO timestamp parsing via Python `sys.argv[1]` preventing command injection.
  - **Path Traversal Defense via Pure Bash Regex**: Strictly sanitizes `conversation_id` (`${CONV_ID//[^a-zA-Z0-9_-]/_}`, capped at 64 chars) without external `md5sum` dependencies, ensuring cross-platform portability on macOS and Linux.
  - **Private Cache Directory**: Strictly private cache directory (`${XDG_CACHE_HOME:-~/.cache}/antigravity/` with enforced `0700` permissions and ownership verification).
  - **Zero `/tmp` Fallback**: If the private cache cannot be securely accessed, caching is cleanly disabled (`SESSION_CACHE=""`) without ever writing to shared `/tmp`.
  - **Atomic Unpredictable Writes**: Writes session start epoch atomically via unpredictable `mktemp "${CACHE_DIR}/tmp_XXXXXX"` (mode `0600`) and atomic POSIX `mv`.
  - **Strict Validation**: Positive integer regex validation (`^[1-9][0-9]*$`) on epochs, numeric validation on tool counts, and sanitized terminal width bounds (`[20, 1000]`).

### 4. Git Status Bar (`git_status_bar.sh`)
- **Branch Indicator**: Current branch (` main`) or short commit hash when detached, sanitized of ASCII control characters (`${BRANCH//[[:cntrl:]]/}`).
- **Staged Changes**: Highlights staged change count (`1 staged`).
- **Unstaged & Untracked**: Tracks modified and untracked file counts (`2 unstaged`).
- **Atomic Upstream Divergence Tracking**:
  - Uses atomic single-invocation `git rev-list --left-right --count @{upstream}...HEAD`.
  - `✓ synced` when identical to remote tracking branch (`0\t0`).
  - `↑ N` when ahead of remote tracking branch.
  - `↓ N` when behind remote tracking branch.
  - `↕ A/B` when diverged (both ahead and behind).
  - `local` when no remote tracking branch is configured.
  - `unknown` when git rev-list fails or exits non-zero (never false `✓ synced`).
- **Graceful Fallback**: Displays `Git: not a repository` when outside a Git directory while clamping terminal padding safely (`[20, 1000]`, non-overflowing fill).

---

## AGY CLI Status Line JSON Schema

The AGY CLI pipes a JSON payload to the status line command over stdin on every update cycle.

### Native AGY Dual-Window Bucket Map Format
In the AGY CLI Go core for consumer accounts (e.g. Google AI Pro), `quota` in `StatusLineData` is partitioned by rolling windows and model groups (`map[string]types.StatusLineQuotaBucket`):

```json
{
  "conversation_id": "c0ffee00-1234-5678-9abc-def012345678",
  "cwd": "/home/user/project",
  "model": {
    "id": "Gemini 3.8 Flash (Medium)",
    "display_name": "Gemini 3.8 Flash (Medium)"
  },
  "quota": {
    "gemini-5h": {
      "remaining_fraction": 0.9151903,
      "reset_time": "2026-09-17T19:55:09Z",
      "reset_in_seconds": 7148
    },
    "gemini-weekly": {
      "remaining_fraction": 0.80764735,
      "reset_time": "2026-09-23T04:39:02Z",
      "reset_in_seconds": 470581
    },
    "3p-5h": {
      "remaining_fraction": 1.0,
      "reset_time": "2026-09-17T22:43:58Z",
      "reset_in_seconds": 17277
    },
    "3p-weekly": {
      "remaining_fraction": 1.0,
      "reset_time": "2026-09-24T17:43:58Z",
      "reset_in_seconds": 604077
    }
  }
}
```

### Flat Format (Backward / Mock Compatibility)
A flat or single-bucket structure is also supported:

```json
{
  "conversation_id": "c0ffee00-1234-5678-9abc-def012345678",
  "cwd": "/home/user/project",
  "quota": {
    "remaining_fraction": 0.85,
    "remaining_percentage": 85,
    "reset_time": "2026-09-18T00:00:00Z",
    "reset_in_seconds": 14400,
    "disabled": false
  }
}
```

### Bucket Selection & Account Handling:
- **Model Group Resolution**: The active model (`.model.id` or `.model.display_name`) determines whether `gemini-*` or `3p-*` windows are rendered. If unconsumed, the 5-hour countdown is suppressed.
- **Sliding Window Trigger Detection**: The 5-hour rolling countdown timer is suppressed when the 5-hour quota is idle (100% untouched). The countdown activates dynamically only after requests consume quota within the 5-hour sliding window.
- **Exhausted Quota (0%)**: When `remaining_fraction == 0.0`, the script renders a prominent red `0%` warning badge.
- **Enterprise Accounts / API Keys**: On enterprise Google accounts or custom API key sessions where `quota` is absent, `null`, empty `{}` or all buckets have `disabled: true`, the script exits 0 with zero output, cleanly omitting the quota segment from the status line.

---

## Setup Instructions

### Option 1: Interactive TUI Setup (Recommended)

Run the interactive TUI installer to select which modules and options you want to enable via checkboxes:

```bash
chmod +x CLI/customization/install.sh
./CLI/customization/install.sh
```

The installer provides:
- **Interactive Checkbox Selection**: Choose Git status, Session & Round timers, Consumer Account Quota, Digital/Verbose styling, and installation targets.
- **TUI Keybindings**: Navigate with `[↑/k]` and `[↓/j]`, toggle with `[Space]`, install with `[Enter]`, uninstall all with `[u]`, and exit with `[q]`.
- **Live Preview**: See how the status line will render in real time as you toggle items.
- **POSIX Shell Escaping**: Paths and command arguments are safely escaped using `shlex.quote()` in Python and `printf '%q'` in Bash, preventing command injection on directories with spaces, quotes, or shell metacharacters.
- **Direct Array Execution**: Verification tests execute target scripts directly as argument arrays without `bash -c`.
- **Atomic Settings Updates**: Atomically updates `~/.gemini/antigravity-cli/settings.json` using Python temporary files and atomic POSIX renames, preserving existing configuration.
- **Safe Abort on Error**: Validates JSON syntax and aborts safely without modifying configuration if existing settings are corrupted.

#### Non-Interactive CLI Flags
You can automate installation or select specific modules with CLI flags:

```bash
# Install all modules (Git, Timer, Quota) globally
./CLI/customization/install.sh --all -y

# Install only Git status
./CLI/customization/install.sh --git-only -y

# Install only Consumer Account Quota
./CLI/customization/install.sh --quota-only -y

# Install Git and Timer without Quota
./CLI/customization/install.sh --no-quota -y

# Install with digital timer format
./CLI/customization/install.sh --all --digital -y

# Copy scripts without modifying settings.json
./CLI/customization/install.sh --no-settings -y

# Uninstall all customizations and revert settings.json
./CLI/customization/install.sh --uninstall
# or
./CLI/customization/install.sh -u
```

---

### Option 2: Manual Setup

> [!IMPORTANT]
> **Global Configuration Only**: AGY CLI loads runtime settings exclusively from the global configuration file:
> `~/.gemini/antigravity-cli/settings.json`
>
> Local `.agents/` directories are reserved for agent capabilities (skills, rules, plugins, and lifecycle hooks). The CLI does **not** read `.agents/settings.json`. To use custom status lines across all workspaces, place the scripts in a global path like `~/.gemini/antigravity-cli/scripts/`.

#### 1. Copy the Scripts

Install scripts globally so the status bar works in any workspace:

```bash
mkdir -p ~/.gemini/antigravity-cli/scripts
cp CLI/customization/*.sh ~/.gemini/antigravity-cli/scripts/
chmod +x ~/.gemini/antigravity-cli/scripts/*.sh
```

#### 2. Configure Global `settings.json`

Add the `statusLine` configuration to `~/.gemini/antigravity-cli/settings.json`:

##### Option A: Unified Status Bar (Git + Timer + Quota - Recommended)
```json
{
  "statusLine": {
    "type": "command",
    "command": "~/.gemini/antigravity-cli/scripts/status_bar.sh",
    "interval": 2,
    "enabled": true,
    "stack_with_default": true
  }
}
```

##### Option B: Standalone Quota Only
```json
{
  "statusLine": {
    "type": "command",
    "command": "~/.gemini/antigravity-cli/scripts/quota_status_bar.sh",
    "interval": 2,
    "enabled": true,
    "stack_with_default": true
  }
}
```

##### Option C: Standalone Timer Only
```json
{
  "statusLine": {
    "type": "command",
    "command": "~/.gemini/antigravity-cli/scripts/timer_status_bar.sh",
    "interval": 2,
    "enabled": true,
    "stack_with_default": true
  }
}
```

##### Option D: Standalone Git Status Only
```json
{
  "statusLine": {
    "type": "command",
    "command": "~/.gemini/antigravity-cli/scripts/git_status_bar.sh",
    "interval": 2,
    "enabled": true,
    "stack_with_default": true
  }
}
```

#### 3. Live Activation via Slash Command

You can also switch or test configurations live in the AGY CLI with the slash command:
```bash
/statusline ~/.gemini/antigravity-cli/scripts/status_bar.sh
```

---

## Configuration Options

| Option               | Type      | Description                                                              |
| :------------------- | :-------- | :----------------------------------------------------------------------- |
| `command`            | `string`  | The shell command executed to render the status line.                    |
| `interval`           | `number`  | Refresh frequency in seconds.                                            |
| `stack_with_default` | `boolean` | When `true`, stacks this status bar alongside AGY's default status line. |

---

## Uninstalling Customizations

To cleanly remove all status line customizations and restore AGY's default behavior:

### Non-Interactive Uninstall
```bash
./CLI/customization/install.sh --uninstall
# or
./CLI/customization/install.sh -u
```

### Interactive Uninstall
Launch `./CLI/customization/install.sh` and press `[u]`. You will be prompted to confirm:
```text
Are you sure you want to remove all AGY status line customizations? [y/N]:
```

The uninstall routine automatically:
1. Verifies ownership of `statusLine.command` before modifying `settings.json`. If a third-party command is configured, leaves it untouched and logs a warning.
2. Creates an automatic backup copy `~/.gemini/antigravity-cli/settings.json.bak` before any modification.
3. Atomically removes our `statusLine` configuration while preserving file permissions and all other configuration keys (`model`, `toolPermission`, `trustedWorkspaces`, etc.).
4. Deletes installed scripts (`status_bar.sh`, `git_status_bar.sh`, `timer_status_bar.sh`, `quota_status_bar.sh`) from `~/.gemini/antigravity-cli/scripts/` without disturbing other user scripts.
5. Deletes the same scripts from `.agents/scripts/` if present in the workspace, removing the directory if empty.
6. Purges temporary session cache files (`~/.cache/antigravity/session_*` and `agy_sess_*`).
7. Advises restarting `agy` to restore the default status line.

---

## Requirements

- **Bash** (v4.0+)
- **Python 3** (v3.8+)
- **jq** (for fast JSON transcript and stdin parsing)
- **Git**
- A **Nerd Font** or Powerline-compatible font (for ``, `󱎫`, `󰔛`, `󱐋`, `󱓞`, `󰔟`, `✓`, `⚠`, `↑`, `↓`, `↕`)
