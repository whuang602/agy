# AGY CLI Customization: Git Status Bar

A dynamic, modular TUI status line customization for Google Antigravity (AGY) CLI that displays real-time Git status directly in your terminal interface.

---

## Features

- **Branch Indicator**: Displays current branch name (` main`) or short commit hash when in detached HEAD state.
- **Staged Changes**: Highlights the count of staged changes ready for commit.
- **Unstaged & Untracked Changes**: Tracks modified and untracked files.
- **Upstream Sync Status**:
  - `✓ synced` when up to date with remote tracking branch.
  - `⚠ N unpushed` when ahead of remote.
  - `local` when no remote tracking branch is configured.
- **Adaptive Width**: Automatically fits your terminal width with clean box-drawing borders.
- **Graceful Fallback**: Displays a clean placeholder border when navigating outside a Git repository.

---

## Setup Instructions

### 1. Copy the Script to Your Workspace

Place `git_status_bar.sh` into your workspace's `.agents/scripts/` directory and ensure it has executable permissions:

```bash
mkdir -p .agents/scripts
cp git_status_bar.sh .agents/scripts/git_status_bar.sh
chmod +x .agents/scripts/git_status_bar.sh
```

### 2. Configure `settings.json`

Add the `statusLine` configuration to your AGY CLI settings file (either in your project workspace at `.agents/settings.json` or globally at `~/.gemini/antigravity-cli/settings.json`):

```json
{
  "statusLine": {
    "command": "bash .agents/scripts/git_status_bar.sh",
    "interval": 2,
    "stack_with_default": true
  }
}
```

#### Configuration Options

| Option | Type | Description |
| :--- | :--- | :--- |
| `command` | `string` | The shell command executed to render the status line. |
| `interval` | `number` | Refresh frequency in seconds. |
| `stack_with_default` | `boolean` | When `true`, stacks this status bar alongside AGY's default status line. |

---

## Requirements

- **Bash** (v4.0+)
- **Git**
- A **Nerd Font** or Powerline-compatible font (recommended for the branch icon `` and status symbols)
