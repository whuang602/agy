# AGY CLI Customizations: Status Line Modules

A modular suite of dynamic TUI status line customizations for Google Antigravity (AGY) CLI. These scripts display real-time Git status, total session elapsed time, and prompt-to-action round durations directly inside your terminal interface.

---

## Overview of Scripts

Each module can be used **standalone** (rendering its own bordered line) or combined seamlessly into a **single unified line** via `status_bar.sh`:

| Script                    | Purpose                                                                   | Standalone Preview                                                             |
| :------------------------ | :------------------------------------------------------------------------ | :----------------------------------------------------------------------------- |
| **`status_bar.sh`**       | **Unified status line** composing Git + Timers in separate `[ ]` brackets | `───[  main │ 0 staged │ 0 unstaged │ ✓ synced ]───[ 󱎫 14m 32s │ 󰔛 3.8s ]───` |
| **`git_status_bar.sh`**   | Real-time Git branch, staging, untracked & remote sync                    | `───[  main │ 0 staged │ 0 unstaged │ ✓ synced ]───────────────────────`      |
| **`timer_status_bar.sh`** | Session uptime + prompt-to-action completion round timer                  | `───[ 󱎫 14m 32s │ 󰔛 3.8s ]────────────────────────────────────────────`        |

---

## Features

### 1. Unified Status Bar (`status_bar.sh`)
- Combines the Git segment and the Timer segment into separate `[ ]` capsules connected on the **same visual line**.
- Dynamically queries true terminal width (via `/dev/tty`, `stty size`, or `tput cols`) and pads the box-drawing border (`───[...]────`) to span the entire terminal width.
- Preserves and passes the AGY CLI stdin JSON payload to downstream segment scripts.

### 2. Session & Round Timer (`timer_status_bar.sh`)
- **Total Session Time (`󱎫`)**: Calculates the elapsed time from session start (step 0 timestamp) to the present moment.
- **Round Duration (`󰔛` / `󱐋`)**:
  - **While Running (`󱐋`)**: Shows live ticking time elapsed since the user submitted the current prompt (e.g., `󱐋 14s`).
  - **When Completed (`󰔛`)**: Measures the exact duration from the prompt timestamp to the moment the action completed (e.g., `󰔛 3m 42s` or `<1s`).
- **Continuous Seconds Retention**: Seconds are never truncated across hours or minutes (e.g., `1h 10m 26s`), ensuring the timer always visibly increments on updates.
- **Display Styles**:
  - `verbose` (default): Units format (e.g., `1h 10m 26s`, `3m 17s`, `42s`).
  - `digital`: Clean digital clock / stopwatch format (e.g., `01:10:26`, `03:17`). Set via `export TIMER_STYLE=digital` or `--digital`.
- **Fast Caching**: Session start epoch is cached in `/tmp` for sub-millisecond status line rendering.
- **Zero Configuration Needed**: Automatically inspects the conversation transcript (`transcript.jsonl`) using the `conversation_id` passed via stdin from AGY CLI.
- **Standalone & Modular**: Run standalone for just timers, or pass `--segment` to output raw segment data for composition.

### 3. Git Status Bar (`git_status_bar.sh`)
- **Branch Indicator**: Current branch (` main`) or short commit hash when detached.
- **Staged Changes**: Highlights staged change count (`1 staged`).
- **Unstaged & Untracked**: Tracks modified and untracked file counts (`2 unstaged`).
- **Upstream Sync Status**:
  - `✓ synced` when up to date.
  - `⚠ N unpushed` when ahead of remote tracking branch.
  - `local` when no remote tracking branch is configured.
- **Graceful Fallback**: Displays `Git: not a repository` when outside a Git directory while allowing other segments to render uninterrupted.

---

## Setup Instructions

### Option 1: Interactive TUI Setup (Recommended)

Run the interactive TUI installer to select which modules and options you want to enable via checkboxes:

```bash
chmod +x CLI/customization/install.sh
./CLI/customization/install.sh
```

The installer provides:
- **Interactive Checkbox Selection**: Choose Git status, Session & Round timers, Digital/Verbose styling, and installation targets.
- **Live Preview**: See how the status line will render in real time as you toggle items.
- **Automated Configuration**: Installs scripts centrally to `~/.gemini/antigravity-cli/scripts/` and cleanly configures `~/.gemini/antigravity-cli/settings.json` while preserving all existing settings.

Non-interactive flags are also supported (e.g. `./install.sh --all -y`, `./install.sh --git-only`, `./install.sh --timer-only --digital`).

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

*(Alternatively, copy to `.agents/scripts/` within a specific repository if you only intend to use it in that repository).*

#### 2. Configure Global `settings.json`

Add the `statusLine` configuration to `~/.gemini/antigravity-cli/settings.json`:

##### Option A: Unified Status Bar (Same Line - Recommended)
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

##### Option B: Standalone Timer Only
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

##### Option C: Standalone Git Status Only
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

## Requirements

- **Bash** (v4.0+)
- **jq** (for fast JSON transcript and stdin parsing)
- **Git**
- A **Nerd Font** or Powerline-compatible font (for ``, `󱎫`, `󰔛`, `󱐋`, `✓`, `⚠`)
