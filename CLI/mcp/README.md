# AGY MCP Server Manager

A zero-dependency terminal UI and CLI tool to configure, inspect, and manage Model Context Protocol (MCP) servers in Google Antigravity (`~/.gemini/config/mcp_config.json`).

---

## Quick Start

Launch the interactive terminal UI:

```bash
chmod +x CLI/mcp/manage.sh
./CLI/mcp/manage.sh
```

*(Alternatively: `python3 CLI/mcp/mcp_tui.py`)*

---

## Interactive TUI Controls

### Dashboard

| Key | Action |
| :--- | :--- |
| `↑` / `↓` or `k` / `j` | Navigate servers |
| `[Space]` | Toggle server ON / OFF |
| `[Enter]` / `[e]` | Edit server configuration |
| `[a]` | Add a new server |
| `[t]` | Browse curated recipes (GitHub, DevTools, SQLite, Postgres, etc.) |
| `[v]` | View & toggle discovered tools |
| `[d]` | Delete selected server |
| `[q]` | Quit |

### Form Editor

- **Tabs (`[1]`–`[4]` or `[Tab]`)**:
  - `[1] General`: Transport (`stdio` / `http`), command/URL, arguments, timeout.
  - `[2] Env/Headers`: Environment variables and HTTP headers (`[m]` toggles secret masking).
  - `[3] Auth`: Provider selection (`none`, `google_credentials` for ADC, or `oauth`).
  - `[4] Preflight`: Diagnostics check and JSON preview (`[t]` to test connection).
- **`[s]` / `[Enter]`**: Save configuration.
- **`[Esc]`**: Discard changes and return to dashboard.

### Tool Viewer (`[v]`)

- `[Space]`: Toggle individual tool ON / OFF.
- `[a]` / `[x]`: Enable / disable all tools.
- `[r]`: Refresh & probe tool schemas live.
- `[Esc]`: Return to dashboard.

---

## Scriptable CLI (Non-Interactive)

Run commands directly without launching the UI:

```bash
# List all configured servers
./CLI/mcp/manage.sh --list

# Enable or disable a server
./CLI/mcp/manage.sh --enable <server_name>
./CLI/mcp/manage.sh --disable <server_name>

# Remove a server
./CLI/mcp/manage.sh --remove <server_name>

# Output as JSON (secrets redacted by default)
./CLI/mcp/manage.sh --list --json

# Dry-run mutations without modifying disk
./CLI/mcp/manage.sh --enable <server_name> --dry-run
```

---

## Key Features

- **Token Optimization**: Defaults to Lazy Tool Loading (`toolConfig: { eager: false }`) to avoid filling prompt context with unused tool schemas.
- **Built-in Recipes**: 9 ready-to-run templates for GitHub, DevTools, Postgres, SQLite, Puppeteer, Memory, and more.
- **Native Auth**: First-class support for Google ADC (`authProviderType: "google_credentials"`) and OAuth 2.0.
- **Safe Persistence**: Atomic file swaps (`os.replace`) with automatic timestamped `.bak` backups.
