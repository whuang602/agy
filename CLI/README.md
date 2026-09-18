# Antigravity (AGY) CLI Tooling & Customizations

This directory contains customizations and tooling for the Google Antigravity (AGY) CLI.

## Quick Start: Interactive Setup

To interactively configure and install customizations (including Git status, session & round timers, and consumer account daily request quota):

https://github.com/user-attachments/assets/5c1a955e-ceef-4b76-9d6b-408d4d14f1e1

```bash
chmod +x CLI/customization/install.sh
./CLI/customization/install.sh
```

The interactive installer allows you to select which modules to enable, preview them in real time, and automatically configure your global `~/.gemini/antigravity-cli/settings.json`.

### Uninstalling Customizations

To cleanly remove all custom status line components and restore AGY's default behavior:

```bash
# Non-interactive CLI flag:
./CLI/customization/install.sh --uninstall
# or
./CLI/customization/install.sh -u

# Or launch interactive mode and press [u]:
./CLI/customization/install.sh
```

---

## Documentation

- **[Status Line Customization Suite](customization/README.md)**: Full guide covering:
  - **Unified Status Bar**: Composable Git, Timer, and Quota segments on a single responsive line with width-aware progressive degradation (compaction on 100/80-column terminals), non-overflowing fill, width clamping `[20, 1000]`, and clean empty suppression.
  - **Git Status Module**: Real-time branch (control-character sanitized), staged/unstaged changes, and atomic upstream divergence tracking (`rev-list`).
  - **Session & Round Timers**: Continuous elapsed session time and active/completed turn duration with zero `/tmp` fallback, pure bash regex path traversal sanitization, and atomic caching.
  - **Consumer Account Quota**: Dual-window (5-hour rolling limit + weekly capacity limit) display with model group awareness (Gemini vs. 3rd-party models, supporting string or object `.model`), deterministic integer-tenths percentage precision, dynamic 5-hour sliding window countdown activation/suppression (hidden when 100% idle), labeled single-bucket fallback, 14-day reset clamping with day tier (`1d 4h`), and clean enterprise/API key suppression.
  - **Automated Installer & Uninstaller**: Interactive checkbox TUI with live dual-window preview, safe path quoting with whitespace handling, portable relative workspace commands, atomic `settings.json` configuration with automatic `settings.json.bak` backup, third-party status line ownership protection, and clean uninstallation (`--uninstall` or `[u]`).
