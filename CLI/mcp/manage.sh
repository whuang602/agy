#!/usr/bin/env bash
#
# Antigravity (AGY) MCP Server Manager & Interactive TUI Configurator
#
# Shell wrapper and CLI command entrypoint for managing Model Context Protocol
# (MCP) servers in ~/.gemini/config/mcp_config.json.
#

set -euo pipefail

# Resolve symlinks to find genuine script directory (L5)
SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
TUI_SCRIPT="${SCRIPT_DIR}/mcp_tui.py"

# Preflight: Verify Python 3 presence and minimum version 3.8+ (L6)
if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 is required to run the AGY MCP Server Manager." >&2
    echo "Please install Python 3 (3.8+) and ensure it is available in your PATH." >&2
    exit 1
fi

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    echo "Error: Python 3.8 or newer is required to run the AGY MCP Server Manager." >&2
    exit 1
fi

# Verify TUI script exists (L4)
if [[ ! -f "$TUI_SCRIPT" ]]; then
    echo "Error: Cannot locate TUI script at '${TUI_SCRIPT}'." >&2
    exit 1
fi

# Delegate all argument processing, help text, and execution to mcp_tui.py (L1, L2, L7, M15)
exec python3 "$TUI_SCRIPT" "$@"
