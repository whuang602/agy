#!/usr/bin/env python3
"""
Antigravity (AGY) MCP Server Manager & Interactive TUI Configurator

Zero-dependency terminal UI and CLI suite to configure, inspect, test,
and persist Model Context Protocol (MCP) servers in ~/.gemini/config/mcp_config.json.
"""

import os
import sys
import json
import shutil
import termios
import tty
import select
import signal
import shlex
import urllib.parse
import re
import atexit
import stat
import tempfile
import fcntl
import unicodedata
import argparse
from datetime import datetime
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple, Set

DEFAULT_CONFIG_PATH = os.environ.get("AGY_MCP_CONFIG") or os.path.expanduser("~/.gemini/config/mcp_config.json")
DEFAULT_MCP_CACHE_DIR = os.path.expanduser("~/.gemini/antigravity-cli/mcp")
VERSION = "2.0.0"

# Known modeled server keys in McpServerModel
_KNOWN_SERVER_KEYS = {
    "command", "args", "env", "serverUrl", "url", "headers", "transport", "disabled",
    "authProviderType", "auth_provider", "oauth", "oauthClientId", "oauth_client_id",
    "oauthClientSecret", "oauth_client_secret", "authConfig",
    "toolConfig", "eager", "background", "timeoutSeconds", "timeout_seconds", "timeout",
    "bypassSandbox", "bypass_sandbox", "skipToolNamePrefix", "skip_tool_name_prefix"
}


# ==============================================================================
# 0. Errors & ANSI / Terminal Helpers
# ==============================================================================

class ConfigParseError(Exception):
    """Raised when mcp_config.json cannot be parsed or contains invalid structure."""
    def __init__(self, message: str, path: str = "", lineno: Optional[int] = None, colno: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.path = path
        self.lineno = lineno
        self.colno = colno

    def __str__(self) -> str:
        loc = []
        if self.path:
            loc.append(self.path)
        if self.lineno is not None:
            loc.append(f"line {self.lineno}")
        if self.colno is not None:
            loc.append(f"col {self.colno}")
        loc_str = f" ({', '.join(loc)})" if loc else ""
        return f"ConfigParseError{loc_str}: {self.message}"


def supports_color() -> bool:
    """Checks if the active terminal output supports ANSI color codes."""
    if "NO_COLOR" in os.environ:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    if not sys.stdout.isatty():
        return False
    return True


class Colors:
    _has_color = supports_color()

    RESET = "\033[0m" if _has_color else ""
    BOLD = "\033[1m" if _has_color else ""
    DIM = "\033[2m" if _has_color else ""
    UNDERLINE = "\033[4m" if _has_color else ""
    INVERT = "\033[7m" if _has_color else ""

    BLACK = "\033[30m" if _has_color else ""
    RED = "\033[31m" if _has_color else ""
    GREEN = "\033[32m" if _has_color else ""
    YELLOW = "\033[33m" if _has_color else ""
    BLUE = "\033[34m" if _has_color else ""
    MAGENTA = "\033[35m" if _has_color else ""
    CYAN = "\033[36m" if _has_color else ""
    WHITE = "\033[37m" if _has_color else ""
    GRAY = "\033[90m" if _has_color else ""

    BG_BLACK = "\033[40m" if _has_color else ""
    BG_BLUE = "\033[44m" if _has_color else ""
    BG_CYAN = "\033[46m" if _has_color else ""
    BG_WHITE = "\033[47m" if _has_color else ""

    CLEAR_SCREEN = "\033[2J\033[H"
    CLEAR_LINE = "\033[K"
    HIDE_CURSOR = "\033[?25l"
    SHOW_CURSOR = "\033[?25h"
    ALT_SCREEN_ON = "\033[?1049h"
    ALT_SCREEN_OFF = "\033[?1049l"

    @classmethod
    def reconfigure(cls, enabled: bool):
        cls._has_color = enabled
        cls.RESET = "\033[0m" if enabled else ""
        cls.BOLD = "\033[1m" if enabled else ""
        cls.DIM = "\033[2m" if enabled else ""
        cls.UNDERLINE = "\033[4m" if enabled else ""
        cls.INVERT = "\033[7m" if enabled else ""
        cls.BLACK = "\033[30m" if enabled else ""
        cls.RED = "\033[31m" if enabled else ""
        cls.GREEN = "\033[32m" if enabled else ""
        cls.YELLOW = "\033[33m" if enabled else ""
        cls.BLUE = "\033[34m" if enabled else ""
        cls.MAGENTA = "\033[35m" if enabled else ""
        cls.CYAN = "\033[36m" if enabled else ""
        cls.WHITE = "\033[37m" if enabled else ""
        cls.GRAY = "\033[90m" if enabled else ""
        cls.BG_BLACK = "\033[40m" if enabled else ""
        cls.BG_BLUE = "\033[44m" if enabled else ""
        cls.BG_CYAN = "\033[46m" if enabled else ""
        cls.BG_WHITE = "\033[47m" if enabled else ""


ANSI_REGEX = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def char_width(c: str) -> int:
    """Returns visual cell width of character accounting for East Asian fullwidth."""
    w = unicodedata.east_asian_width(c)
    if w in ('W', 'F'):
        return 2
    return 1


def visible_len(s: str) -> int:
    """Calculates visible cell length ignoring ANSI escape sequences."""
    clean = ANSI_REGEX.sub('', s)
    return sum(char_width(c) for c in clean)


def truncate_visible(s: str, max_len: int) -> str:
    """Truncates string to max_len visible terminal cells without breaking ANSI escapes."""
    if max_len <= 0:
        return ""
    cur_len = 0
    out = []
    i = 0
    n = len(s)
    has_ansi = False
    while i < n:
        m = ANSI_REGEX.match(s, i)
        if m:
            out.append(m.group(0))
            has_ansi = True
            i = m.end()
            continue
        c = s[i]
        w = char_width(c)
        if cur_len + w > max_len:
            break
        out.append(c)
        cur_len += w
        i += 1
    res = "".join(out)
    if has_ansi and not res.endswith("\033[0m"):
        res += "\033[0m"
    return res


def pad_visible(s: str, width: int, align: str = "left") -> str:
    """Pads string to visual column width taking ANSI escapes into account."""
    vlen = visible_len(s)
    pad = max(0, width - vlen)
    if align == "right":
        return (" " * pad) + s
    elif align == "center":
        left = pad // 2
        right = pad - left
        return (" " * left) + s + (" " * right)
    return s + (" " * pad)


_CTRL_RE = re.compile(r'[\x00-\x08\x0b-\x1f\x7f\x9b\x1b]')

def sanitize_display(s: str) -> str:
    """Replaces terminal control characters and escape sequences with \ufffd."""
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    return _CTRL_RE.sub('\ufffd', s)


_SENSITIVE_QUERY_PARAMS = {
    "token", "access_token", "api_key", "key", "secret", "password", "sig", "signature"
}

_PUBLIC_HEADERS_ALLOWLIST = {
    "accept",
    "accept-encoding",
    "accept-language",
    "content-type",
    "user-agent",
    "cache-control",
    "mcp-protocol-version",
}

_RFC9110_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+-.^_`|~]+$")


def _redact_single_url(url_str: str) -> str:
    try:
        parts = urllib.parse.urlsplit(url_str)
        if not parts.scheme:
            return url_str
        new_netloc = parts.netloc
        if "@" in parts.netloc:
            userinfo, host = parts.netloc.rsplit("@", 1)
            if ":" in userinfo:
                user, _ = userinfo.split(":", 1)
                new_userinfo = f"{user}:••••••••"
            else:
                new_userinfo = "••••••••"
            new_netloc = f"{new_userinfo}@{host}"

        new_query = parts.query
        if parts.query:
            query_pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
            new_pairs = []
            for k, v in query_pairs:
                if k.lower() in _SENSITIVE_QUERY_PARAMS:
                    new_pairs.append((k, "••••••••"))
                else:
                    new_pairs.append((k, v))
            new_query = "&".join(f"{k}={v}" if v else k for k, v in new_pairs)

        new_parts = parts._replace(netloc=new_netloc, query=new_query)
        return urllib.parse.urlunsplit(new_parts)
    except Exception:
        return url_str


def redact_url_or_cmd(text: str) -> str:
    """Redacts credentials and sensitive query parameters in URLs and commands."""
    if not isinstance(text, str):
        return text
    url_pattern = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s\"'<>]+")
    return url_pattern.sub(lambda m: _redact_single_url(m.group(0)), text)


def redact_secretish(text: str) -> str:
    """Masks token-shaped and secret-shaped CLI flags (--token=..., --api-key ..., etc.)."""
    if not isinstance(text, str):
        return text
    flag_pattern = r"(?i)(--(?:(?:access[-_]?)?token|api[-_]?key|auth[-_]?token|secret|password|key))"
    text = re.sub(
        flag_pattern + r"=(\"[^\"]*\"|\'[^\']*\'|[^\s]+)",
        rf"\g<1>=••••••••",
        text
    )
    text = re.sub(
        flag_pattern + r"(\s+)(\"[^\"]*\"|\'[^\']*\'|(?!-)[^\s]+)",
        rf"\g<1>\g<2>••••••••",
        text
    )
    return text


def redact_server_dict(data: dict) -> dict:
    """Creates a deep copy of server dictionary with secrets masked for display."""
    d = json.loads(json.dumps(data))
    if "oauth" in d and isinstance(d["oauth"], dict):
        if d["oauth"].get("clientSecret"):
            d["oauth"]["clientSecret"] = "●●●●●●●●"
    if d.get("oauthClientSecret"):
        d["oauthClientSecret"] = "●●●●●●●●"
    if "env" in d and isinstance(d["env"], dict):
        for k in d["env"]:
            d["env"][k] = "●●●●●●●●"
    if "headers" in d and isinstance(d["headers"], dict):
        for k in d["headers"]:
            if k.lower() not in _PUBLIC_HEADERS_ALLOWLIST:
                d["headers"][k] = "●●●●●●●●"
    if "serverUrl" in d and isinstance(d["serverUrl"], str):
        d["serverUrl"] = redact_url_or_cmd(d["serverUrl"])
    if "args" in d and isinstance(d["args"], list):
        d["args"] = [redact_url_or_cmd(str(a)) for a in d["args"]]
    return d



# ==============================================================================
# 1. Data Models
# ==============================================================================

@dataclass
class McpServerModel:
    """
    Data model representing a Model Context Protocol (MCP) server configuration.
    Supports stdio and http transports, native Google ADC, OAuth 2.0, lazy tool loading,
    background execution, and unknown key preservation.
    """
    name: str
    transport: str = "stdio"                 # "stdio" or "http"
    command: str = ""                         # For stdio
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    server_url: str = ""                      # For http / sse
    headers: Dict[str, str] = field(default_factory=dict)
    auth_provider: str = "none"               # "none", "google_credentials", "oauth", etc.
    oauth_client_id: str = ""
    oauth_client_secret: str = ""
    eager: bool = False                       # False = lazy tool loading (token savings)
    background: str = "OFF"                   # "OFF" or "ALWAYS"
    timeout_seconds: int = 60
    bypass_sandbox: bool = False
    skip_tool_name_prefix: bool = False
    disabled: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)
    explicit_timeout: bool = False
    explicit_transport: bool = False

    def __post_init__(self):
        if self.timeout_seconds != 60:
            self.explicit_timeout = True

    @staticmethod
    def _str_list(v: Any) -> List[str]:
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        return [str(x) for x in v]

    @staticmethod
    def _str_map(v: Any) -> Dict[str, str]:
        if not isinstance(v, dict):
            return {}
        return {str(k): str(x) for k, x in v.items()}

    @staticmethod
    def _header_map(v: Any) -> Dict[str, str]:
        if not isinstance(v, dict):
            return {}
        result: Dict[str, str] = {}
        for k, x in v.items():
            k_str = str(k)
            x_str = str(x) if x is not None else ""
            if not _RFC9110_HEADER_NAME_RE.fullmatch(k_str):
                continue
            if any(c in x_str for c in ('\x00', '\r', '\n')):
                continue
            result[k_str] = x_str
        return result

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "McpServerModel":
        if not isinstance(data, dict):
            data = {}

        # Capture unmodeled keys to preserve third-party/future AGY extensions (C1)
        extra = {k: v for k, v in data.items() if k not in _KNOWN_SERVER_KEYS}

        url = str(data.get("serverUrl", data.get("url", "")) or "")
        explicit_transport = data.get("transport") in ("stdio", "http")
        if explicit_transport:
            transport = str(data["transport"])
        else:
            transport = "http" if url else "stdio"

        # Authentication provider
        auth_provider = str(data.get("authProviderType", data.get("auth_provider", "none")) or "none")

        # OAuth credentials: read both native nested oauth object and legacy flat keys (C3)
        oauth_obj = data.get("oauth") if isinstance(data.get("oauth"), dict) else {}
        auth_cfg = data.get("authConfig") if isinstance(data.get("authConfig"), dict) else {}
        oauth_client_id = (
            oauth_obj.get("clientId")
            or oauth_obj.get("client_id")
            or data.get("oauthClientId")
            or data.get("oauth_client_id")
            or auth_cfg.get("clientId")
            or auth_cfg.get("oauthClientId", "")
        )
        oauth_client_secret = (
            oauth_obj.get("clientSecret")
            or oauth_obj.get("client_secret")
            or data.get("oauthClientSecret")
            or data.get("oauth_client_secret")
            or auth_cfg.get("clientSecret")
            or auth_cfg.get("oauthClientSecret", "")
        )

        # Tool configuration (lazy loading & background execution)
        tool_config = data.get("toolConfig") if isinstance(data.get("toolConfig"), dict) else {}
        eager = tool_config.get("eager", data.get("eager", False))
        background = tool_config.get("background", data.get("background", "OFF"))
        if background not in ("OFF", "ALWAYS"):
            background = "OFF"

        # Timeout handling without magic sentinel loss (M9)
        explicit_timeout = any(k in data for k in ("timeoutSeconds", "timeout_seconds", "timeout"))
        raw_timeout = data.get("timeoutSeconds", data.get("timeout_seconds", data.get("timeout", 60)))
        try:
            timeout_seconds = int(raw_timeout)
        except (ValueError, TypeError):
            timeout_seconds = 60

        bypass_sandbox = bool(data.get("bypassSandbox", data.get("bypass_sandbox", False)))
        skip_tool_name_prefix = bool(data.get("skipToolNamePrefix", data.get("skip_tool_name_prefix", False)))
        disabled = bool(data.get("disabled", False))

        command = str(data.get("command", "") or "")
        args = cls._str_list(data.get("args"))
        env = cls._str_map(data.get("env"))
        headers = cls._header_map(data.get("headers"))

        return cls(
            name=name,
            transport=transport,
            command=command,
            args=args,
            env=env,
            server_url=url,
            headers=headers,
            auth_provider=auth_provider,
            oauth_client_id=str(oauth_client_id or ""),
            oauth_client_secret=str(oauth_client_secret or ""),
            eager=bool(eager),
            background=background,
            timeout_seconds=timeout_seconds,
            bypass_sandbox=bypass_sandbox,
            skip_tool_name_prefix=skip_tool_name_prefix,
            disabled=disabled,
            extra=extra,
            explicit_timeout=explicit_timeout,
            explicit_transport=explicit_transport,
        )

    def to_dict(self) -> dict:
        result: Dict[str, Any] = {}
        if self.disabled:
            result["disabled"] = True

        if self.explicit_transport:
            result["transport"] = self.transport


        if self.transport == "stdio":
            result["command"] = self.command
            if self.args:
                result["args"] = list(self.args)
            if self.env:
                result["env"] = dict(self.env)
        else:
            result["serverUrl"] = self.server_url
            if self.headers:
                result["headers"] = dict(self.headers)

        if self.auth_provider == "google_credentials":
            result["authProviderType"] = "google_credentials"
        elif self.auth_provider == "oauth":
            result["authProviderType"] = "oauth"
            oauth_dict: Dict[str, str] = {}
            if self.oauth_client_id:
                oauth_dict["clientId"] = self.oauth_client_id
            if self.oauth_client_secret:
                oauth_dict["clientSecret"] = self.oauth_client_secret
            if oauth_dict:
                result["oauth"] = oauth_dict
        elif self.auth_provider not in ("none", ""):
            result["authProviderType"] = self.auth_provider

        # AGY Tooling & Context settings: only emit when eager or background != 'OFF' (M9)
        tool_cfg: Dict[str, Any] = {}
        if self.eager:
            tool_cfg["eager"] = True
        if self.background != "OFF":
            tool_cfg["background"] = self.background

        if tool_cfg:
            result["toolConfig"] = tool_cfg

        if self.bypass_sandbox:
            result["bypassSandbox"] = True

        if self.skip_tool_name_prefix:
            result["skipToolNamePrefix"] = True

        if self.explicit_timeout or self.timeout_seconds != 60:
            result["timeoutSeconds"] = self.timeout_seconds

        # Merge unmodeled keys first, allowing modeled keys to take precedence (C1)
        return {**self.extra, **result}


@dataclass
class Diagnostic:
    level: str  # "ERROR", "WARNING", "INFO", "PASS"
    message: str
    field: str = ""


@dataclass
class ToolInfo:
    name: str
    description: str
    parameters_summary: str
    raw_schema: dict = field(default_factory=dict)


@dataclass
class RecipeTemplate:
    id: str
    title: str
    badge: str
    description: str
    server: McpServerModel


# ==============================================================================
# 2. Curated Recipes Catalog (M17)
# ==============================================================================

RECIPES_CATALOG: List[RecipeTemplate] = [
    RecipeTemplate(
        id="github",
        title="GitHub",
        badge="npx",
        description="Inspect repositories, search code, file issues, and create pull requests.",
        server=McpServerModel(
            name="github",
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-github@0.6.2"],
            env={"GITHUB_PERSONAL_ACCESS_TOKEN": "<your-token-here>"},
            eager=False,
        )
    ),
    RecipeTemplate(
        id="google-developer-knowledge",
        title="Google Developer Knowledge",
        badge="HTTP/ADC",
        description="Query official Google developer documentation corpus via Google ADC.",
        server=McpServerModel(
            name="google-developer-knowledge",
            transport="http",
            server_url="https://developerknowledge.googleapis.com/mcp",
            auth_provider="google_credentials",
            eager=False,
        )
    ),
    RecipeTemplate(
        id="filesystem",
        title="Local Filesystem",
        badge="npx",
        description="Secure file access for directory listing, file reading, and file writing.",
        server=McpServerModel(
            name="filesystem",
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem@0.6.2", "<workspace-root>"],
            eager=False,
        )
    ),
    RecipeTemplate(
        id="postgres",
        title="PostgreSQL Database Connector",
        badge="npx",
        description="Read-only schema inspection and SQL queries against PostgreSQL databases.",
        server=McpServerModel(
            name="postgres",
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-postgres@0.6.2", "postgresql://localhost/mydb"],
            eager=False,
        )
    ),
    RecipeTemplate(
        id="sqlite",
        title="SQLite Database Explorer",
        badge="uvx",
        description="Lightweight local SQLite database introspection and queries via uvx.",
        server=McpServerModel(
            name="sqlite",
            transport="stdio",
            command="uvx",
            args=["mcp-server-sqlite", "--db-path", "./test.db"],
            eager=False,
        )
    ),
    RecipeTemplate(
        id="puppeteer",
        title="Puppeteer Web Browser",
        badge="npx",
        description="Headless browser automation, web scraping, and JavaScript evaluation.",
        server=McpServerModel(
            name="puppeteer",
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-puppeteer@0.6.2"],
            eager=False,
        )
    ),
    RecipeTemplate(
        id="chrome-devtools-mcp",
        title="Chrome DevTools Protocol",
        badge="npx",
        description="Inspect pages, evaluate scripts, take heap snapshots, and analyze performance.",
        server=McpServerModel(
            name="chrome-devtools-mcp",
            transport="stdio",
            command="npx",
            args=["-y", "chrome-devtools-mcp@latest"],
            eager=False,
        )
    ),
    RecipeTemplate(
        id="memory",
        title="Knowledge Graph & Memory",
        badge="npx",
        description="Persistent entity and relation memory storage across agent sessions.",
        server=McpServerModel(
            name="memory",
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-memory@0.6.2"],
            eager=False,
        )
    ),
    RecipeTemplate(
        id="fetch",
        title="HTTP Fetch & Markdown Extraction",
        badge="uvx",
        description="Fast web page retrieval and HTML-to-markdown content conversion.",
        server=McpServerModel(
            name="fetch",
            transport="stdio",
            command="uvx",
            args=["mcp-server-fetch"],
            eager=False,
        )
    ),
]


# ==============================================================================
# 3. Persistence Manager (ConfigManager) (C2, C8, M1, M2, M3, M13)
# ==============================================================================

class ConfigManager:
    """
    Atomic persistence manager for ~/.gemini/config/mcp_config.json.
    Ensures safe writes via atomic file swaps, timestamped backups, and flock concurrency protection.
    """
    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH):
        expanded = os.path.expanduser(config_path)
        # Symlink resolution to protect dotfile repos (M2)
        if os.path.islink(expanded) or os.path.exists(expanded):
            self.config_path = os.path.realpath(expanded)
        else:
            self.config_path = os.path.abspath(expanded)
        self.backup_path = self.config_path + ".bak"
        self.lock_path = os.path.join(os.path.dirname(self.config_path), ".mcp_config.lock")
        self._lock_depth = 0
        self._lock_fd = None

    @contextmanager
    def _config_lock(self):
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        if self._lock_depth == 0:
            parent_dir = os.path.dirname(self.config_path)
            if parent_dir:
                os.makedirs(parent_dir, mode=0o700, exist_ok=True)
            self._lock_fd = os.open(self.lock_path, flags, 0o600)
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
        self._lock_depth += 1
        try:
            yield
        finally:
            self._lock_depth -= 1
            if self._lock_depth == 0 and self._lock_fd is not None:
                try:
                    fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(self._lock_fd)
                    self._lock_fd = None

    def load_raw_json(self) -> Dict[str, Any]:
        """Loads raw JSON config dict, refusing to swallow syntax errors (C2)."""
        if not os.path.exists(self.config_path):
            return {"mcpServers": {}}

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            raise ConfigParseError(f"Cannot read config file: {e}", path=self.config_path)

        if not content.strip():
            return {"mcpServers": {}}

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise ConfigParseError(f"Malformed JSON: {e.msg}", path=self.config_path, lineno=e.lineno, colno=e.colno)

        if not isinstance(data, dict):
            raise ConfigParseError(f"Root of config file must be a JSON object, got {type(data).__name__}", path=self.config_path)

        return data

    def load_config(self) -> Dict[str, McpServerModel]:
        """Loads and parses all server definitions, raising ConfigParseError on corruption (M13)."""
        data = self.load_raw_json()
        servers_dict = data.get("mcpServers", {})
        if not isinstance(servers_dict, dict):
            raise ConfigParseError(f"'mcpServers' must be a JSON dictionary, got {type(servers_dict).__name__}", path=self.config_path)

        models: Dict[str, McpServerModel] = {}
        for name, cfg in servers_dict.items():
            if not isinstance(cfg, dict):
                raise ConfigParseError(f"Configuration for server '{name}' must be a JSON dictionary, got {type(cfg).__name__}", path=self.config_path)
            try:
                models[name] = McpServerModel.from_dict(name, cfg)
            except Exception as e:
                raise ConfigParseError(f"Failed to parse server '{name}': {e}", path=self.config_path)
        return models

    def save_config(self, servers: Dict[str, McpServerModel]) -> None:
        """Atomically saves server configurations with timestamped backup and safe permissions (C8, M1)."""
        with self._config_lock():
            parent_dir = os.path.dirname(self.config_path)
            os.makedirs(parent_dir, mode=0o700, exist_ok=True)
            try:
                cur_st = os.stat(parent_dir)
                cur_mode = stat.S_IMODE(cur_st.st_mode)
                if (cur_mode & 0o077) != 0:
                    os.chmod(parent_dir, cur_mode & 0o700)
            except OSError:
                pass

            # Ensure existing file is parseable before overwriting (C2)
            raw = self.load_raw_json()
            raw["mcpServers"] = {name: model.to_dict() for name, model in sorted(servers.items())}

            # Multi-version timestamped backup with rolling retention of 5 (M1)
            if os.path.exists(self.config_path):
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                ts_backup_path = f"{self.config_path}.bak.{ts}"
                try:
                    shutil.copy2(self.config_path, ts_backup_path)
                    os.chmod(ts_backup_path, 0o600)
                    # Keep latest pointer backup
                    shutil.copy2(self.config_path, self.backup_path)
                    os.chmod(self.backup_path, 0o600)
                except Exception as e:
                    raise OSError(f"Backup to {ts_backup_path} failed: {e}. Aborting save to protect configuration.")

                # Prune older timestamped backups
                try:
                    base_name = os.path.basename(self.config_path)
                    prefix = base_name + ".bak."
                    bak_files = [
                        os.path.join(parent_dir, f)
                        for f in os.listdir(parent_dir)
                        if f.startswith(prefix)
                    ]
                    bak_files.sort()
                    if len(bak_files) > 5:
                        for old_bak in bak_files[:-5]:
                            try:
                                os.remove(old_bak)
                            except OSError:
                                pass
                except OSError:
                    pass

            # Write to temporary file with 0o600 permissions (C8)
            fd, tmp_path = tempfile.mkstemp(dir=parent_dir, prefix=".mcp_config.", suffix=".tmp")
            try:
                target_mode = 0o600
                if os.path.exists(self.config_path):
                    try:
                        cur_mode = stat.S_IMODE(os.stat(self.config_path).st_mode)
                        target_mode = cur_mode & 0o600
                    except OSError:
                        pass
                try:
                    os.fchmod(fd, target_mode)
                except OSError:
                    pass

                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(raw, f, indent=2)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())

                os.replace(tmp_path, self.config_path)

                # Flush parent directory metadata
                try:
                    dirfd = os.open(parent_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                    try:
                        os.fsync(dirfd)
                    finally:
                        os.close(dirfd)
                except (OSError, AttributeError):
                    pass
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

    def get_server(self, name: str) -> Optional[McpServerModel]:
        return self.load_config().get(name)

    def set_server(self, server: McpServerModel) -> None:
        with self._config_lock():
            servers = self.load_config()
            servers[server.name] = server
            self.save_config(servers)

    def remove_server(self, name: str) -> bool:
        with self._config_lock():
            servers = self.load_config()
            if name in servers:
                del servers[name]
                self.save_config(servers)
                return True
            return False

    def enable_server(self, name: str) -> bool:
        with self._config_lock():
            servers = self.load_config()
            if name in servers:
                if not servers[name].disabled:
                    return True  # No-op if already enabled
                servers[name].disabled = False
                self.save_config(servers)
                return True
            return False

    def disable_server(self, name: str) -> bool:
        with self._config_lock():
            servers = self.load_config()
            if name in servers:
                if servers[name].disabled:
                    return True  # No-op if already disabled
                servers[name].disabled = True
                self.save_config(servers)
                return True
            return False



# ==============================================================================
# 4. Discovered Tool Inspector (ToolSchemaReader) (M10)
# ==============================================================================

class ToolSchemaReader:
    """
    Reads and summarizes cached MCP tool schemas discovered by Antigravity
    under ~/.gemini/antigravity-cli/mcp/<server_name>/*.json.
    """
    def __init__(self, base_dir: str = DEFAULT_MCP_CACHE_DIR):
        self.base_dir = os.path.abspath(os.path.expanduser(base_dir))

    def _candidate_dirs(self, server_name: str) -> List[str]:
        candidates = [
            server_name,
            server_name.replace("-", "_"),
            server_name.replace("_", "-"),
        ]
        found = []
        seen = set()
        for cand in candidates:
            p = os.path.join(self.base_dir, cand)
            if p not in seen and os.path.isdir(p):
                seen.add(p)
                found.append(p)

        if not found and os.path.isdir(self.base_dir):
            try:
                normalized_srv = server_name.lower().replace("_", "-")
                for entry in os.listdir(self.base_dir):
                    normalized_entry = entry.lower().replace("_", "-")
                    # Exact normalized match only (M10)
                    if normalized_entry == normalized_srv:
                        p = os.path.join(self.base_dir, entry)
                        if os.path.isdir(p) and p not in seen:
                            seen.add(p)
                            found.append(p)
            except Exception:
                pass

        return found

    def get_tools(self, server_name: str) -> List[ToolInfo]:
        dirs = self._candidate_dirs(server_name)
        tools: List[ToolInfo] = []
        seen_names = set()

        for d in dirs:
            try:
                for fname in sorted(os.listdir(d)):
                    if fname.endswith(".json"):
                        fpath = os.path.join(d, fname)
                        try:
                            with open(fpath, "r", encoding="utf-8") as f:
                                data = json.load(f)
                            if isinstance(data, dict):
                                name = data.get("name", os.path.splitext(fname)[0])
                                if name in seen_names:
                                    continue
                                seen_names.add(name)
                                desc = (data.get("description") or "").strip()
                                # Accept both inputSchema and parameters (M10)
                                params = data.get("inputSchema") or data.get("parameters", {})
                                props = params.get("properties", {}) if isinstance(params, dict) else {}
                                reqs = params.get("required", []) if isinstance(params, dict) else []
                                if props:
                                    parts = []
                                    for p_name, p_info in props.items():
                                        p_type = p_info.get("type", "any") if isinstance(p_info, dict) else "any"
                                        req_mark = "*" if p_name in reqs else ""
                                        parts.append(f"{p_name}{req_mark}: {p_type}")
                                    summary = ", ".join(parts)
                                else:
                                    summary = "No parameters"
                                tools.append(ToolInfo(
                                    name=name,
                                    description=desc,
                                    parameters_summary=summary,
                                    raw_schema=data
                                ))
                        except Exception:
                            continue
            except Exception:
                continue

        return sorted(tools, key=lambda t: t.name)

    def get_tool_count(self, server_name: str) -> int:
        return len(self.get_tools(server_name))


# ==============================================================================
# 5. Preflight Diagnostics (PreflightChecker) (M18)
# ==============================================================================

class PreflightChecker:
    """
    Validates server configurations, syntax, path presence, and security best practices.
    """
    @staticmethod
    def validate_server(server: McpServerModel, config_path: Optional[str] = None) -> List[Diagnostic]:
        diags: List[Diagnostic] = []

        # 1. Identifier validation (L10)
        if not server.name or not server.name.strip():
            diags.append(Diagnostic("ERROR", "Server identifier cannot be empty.", field="name"))
        else:
            clean_name = server.name.strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]+", clean_name):
                diags.append(Diagnostic("ERROR", "Server identifier must contain only alphanumeric ASCII characters, '-', and '_'.", field="name"))
            else:
                diags.append(Diagnostic("PASS", f"Valid server identifier: '{clean_name}'.", field="name"))

        # 2. Transport & Command / URL
        if server.transport == "stdio":
            if not server.command or not server.command.strip():
                diags.append(Diagnostic("ERROR", "Executable command is required for stdio transport.", field="command"))
            else:
                cmd = server.command.strip()
                # Whitespace in command is a common pitfall (M18)
                if any(c.isspace() for c in cmd):
                    diags.append(Diagnostic("ERROR", f"Command '{cmd}' contains whitespace. Put arguments into the Command Arguments field instead.", field="command"))
                else:
                    resolved = shutil.which(cmd)
                    if resolved:
                        diags.append(Diagnostic("PASS", f"Executable found in PATH: {resolved}", field="command"))
                    else:
                        diags.append(Diagnostic("WARNING", f"Command '{cmd}' not found in current $PATH. Ensure it is installed before running AGY.", field="command"))

            if server.args:
                safe_args = redact_secretish(" ".join(shlex.quote(redact_url_or_cmd(a)) for a in server.args))
                diags.append(Diagnostic("INFO", f"Arguments ({len(server.args)} items): {safe_args}", field="args"))
                for arg in server.args:
                    if "<your-" in arg or "TOKEN_HERE" in arg.upper() or "<workspace-root>" in arg:
                        diags.append(Diagnostic("WARNING", f"Argument '{arg}' contains an unconfigured placeholder value.", field="args"))

            for k, v in server.env.items():
                if not k.strip():
                    diags.append(Diagnostic("ERROR", "Environment variable name cannot be empty.", field="env"))
                elif not re.fullmatch(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
                    diags.append(Diagnostic("ERROR", f"Environment variable name '{k}' is invalid. Must match '^[A-Za-z_][A-Za-z0-9_]*$'.", field="env"))
                elif not v:
                    diags.append(Diagnostic("WARNING", f"Environment variable '{k}' has an empty value.", field="env"))
                elif "<your-" in v or "TOKEN_HERE" in v.upper():
                    diags.append(Diagnostic("WARNING", f"Environment variable '{k}' contains an unconfigured placeholder value.", field="env"))

        elif server.transport == "http":
            if server.command or server.args or server.env:
                diags.append(Diagnostic("WARNING", "HTTP transport is active, but stdio fields (command, args, or env) are populated and will be ignored.", field="transport"))
            if not server.server_url or not server.server_url.strip():
                diags.append(Diagnostic("ERROR", "Server URL is required for HTTP transport.", field="server_url"))
            else:
                raw_url = server.server_url.strip()
                parsed = urllib.parse.urlparse(raw_url)
                if parsed.scheme not in ("http", "https"):
                    diags.append(Diagnostic("ERROR", "Server URL scheme must be http:// or https://.", field="server_url"))
                elif not parsed.netloc:
                    diags.append(Diagnostic("ERROR", "Server URL host / domain is missing or invalid.", field="server_url"))
                else:
                    # Cleartext warning on credentials (M18)
                    if parsed.scheme == "http" and (server.auth_provider == "oauth" or any("AUTH" in h.upper() for h in server.headers)):
                        diags.append(Diagnostic("WARNING", "Cleartext HTTP transport transmits credentials unencrypted. Use HTTPS.", field="server_url"))
                    else:
                        diags.append(Diagnostic("PASS", f"Valid {parsed.scheme.upper()} endpoint: {parsed.netloc}", field="server_url"))

            for k, v in server.headers.items():
                if not k.strip():
                    diags.append(Diagnostic("ERROR", "HTTP header name cannot be empty.", field="headers"))
                elif any(c in k for c in ('\r', '\n', ':')) or any(c in v for c in ('\r', '\n')):
                    diags.append(Diagnostic("ERROR", f"HTTP header '{k}' contains illegal control characters.", field="headers"))
                elif not _RFC9110_HEADER_NAME_RE.fullmatch(k):
                    diags.append(Diagnostic("ERROR", f"HTTP header name '{k}' is not a valid RFC 9110 token.", field="headers"))
                elif not v:
                    diags.append(Diagnostic("WARNING", f"HTTP header '{k}' has an empty value.", field="headers"))

        else:
            diags.append(Diagnostic("ERROR", f"Invalid transport '{server.transport}'. Must be 'stdio' or 'http'.", field="transport"))

        # 3. Authentication
        if server.auth_provider == "google_credentials":
            if server.transport == "stdio":
                diags.append(Diagnostic("WARNING", "authProviderType 'google_credentials' has no effect on stdio transport.", field="auth_provider"))
            else:
                diags.append(Diagnostic("PASS", "Using Google Application Default Credentials (ADC) for Google Cloud / Workspace APIs.", field="auth_provider"))
        elif server.auth_provider == "oauth":
            if not server.oauth_client_id:
                diags.append(Diagnostic("WARNING", "OAuth 2.0 selected but OAuth Client ID is empty.", field="oauth_client_id"))
            else:
                diags.append(Diagnostic("PASS", "OAuth Client ID configured.", field="oauth_client_id"))
            if not server.oauth_client_secret:
                diags.append(Diagnostic("WARNING", "OAuth 2.0 selected but OAuth Client Secret is empty.", field="oauth_client_secret"))
        else:
            diags.append(Diagnostic("INFO", "No special authentication header configured.", field="auth_provider"))

        # 4. Tooling & Context (Lazy vs Eager)
        if server.eager:
            diags.append(Diagnostic("WARNING", "Eager tool loading enabled: Tool schemas injected into prompt every turn, consuming tokens.", field="eager"))
        else:
            diags.append(Diagnostic("PASS", "Lazy tool loading enabled: Schemas cached on disk and loaded on demand, saving prompt tokens.", field="eager"))

        if server.background == "ALWAYS":
            diags.append(Diagnostic("INFO", "Background execution enabled: Long-running tools run asynchronously without stalling turns.", field="background"))

        if server.bypass_sandbox:
            diags.append(Diagnostic("WARNING", "Sandbox bypass enabled: Process executes outside the Antigravity security sandbox.", field="bypass_sandbox"))

        if server.skip_tool_name_prefix:
            diags.append(Diagnostic("INFO", "Tool name prefix skipping enabled: Tools exposed directly without server namespace prefix.", field="skip_tool_name_prefix"))

        # 5. Timeout
        if server.timeout_seconds <= 0:
            diags.append(Diagnostic("ERROR", "Timeout seconds must be a positive integer.", field="timeout_seconds"))
        elif server.timeout_seconds > 3600:
            diags.append(Diagnostic("WARNING", f"Timeout is very high ({server.timeout_seconds}s). Unresponsive tools may stall execution.", field="timeout_seconds"))

        # 6. File permission security check (C8)
        if config_path and os.path.exists(config_path):
            try:
                mode = stat.S_IMODE(os.stat(config_path).st_mode)
                if mode & 0o077 != 0:
                    diags.append(Diagnostic("WARNING", f"Config file permissions ({oct(mode)}) allow group/world access. Recommended: 0600.", field="config"))
            except OSError:
                pass

        return diags

    @staticmethod
    def has_errors(diagnostics: List[Diagnostic]) -> bool:
        return any(d.level == "ERROR" for d in diagnostics)


# ==============================================================================
# 6. Terminal UI Formatting & Input Handling (C7, C9, M8)
# ==============================================================================

class Terminal:
    def __init__(self):
        self.fd = sys.stdin.fileno() if sys.stdin.isatty() else None
        self.orig_term = None
        self.is_raw = False
        atexit.register(self.exit_raw)

    def enter_raw(self):
        if self.fd is None or self.is_raw:
            return
        self.orig_term = termios.tcgetattr(self.fd)
        tty.setraw(self.fd)
        # Set is_raw immediately after setraw to ensure exit_raw cleans up (C7)
        self.is_raw = True
        # Disable bracketed paste (\033[?2004l) to avoid paste mangling (M8)
        sys.stdout.write(f"\033[?2004l{Colors.ALT_SCREEN_ON}{Colors.HIDE_CURSOR}")
        sys.stdout.flush()

    def exit_raw(self):
        if self.is_raw and self.orig_term is not None and self.fd is not None:
            # Re-enable bracketed paste on exit
            sys.stdout.write(f"{Colors.SHOW_CURSOR}{Colors.ALT_SCREEN_OFF}\033[?2004h")
            sys.stdout.flush()
            try:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.orig_term)
            except Exception:
                pass
            self.is_raw = False

    @staticmethod
    def get_size() -> Tuple[int, int]:
        sz = shutil.get_terminal_size((80, 24))
        return max(40, sz.columns), max(10, sz.lines)


class InputReader:
    _queue: List[str] = []

    @classmethod
    def has_queued_keys(cls) -> bool:
        return bool(cls._queue)

    @classmethod
    def get_key(cls, timeout: float = 0.05) -> str:
        if cls._queue:
            return cls._queue.pop(0)

        if not sys.stdin.isatty():
            return ""
        fd = sys.stdin.fileno()
        r, _, _ = select.select([fd], [], [], timeout)
        if not r:
            return ""

        try:
            # Unbuffered read on fd
            raw = os.read(fd, 128)
        except OSError:
            return ""

        if not raw:
            return ""

        # Standalone escape: wait up to 40ms to see if trailing sequence bytes arrive
        if raw == b"\x1b":
            r_more, _, _ = select.select([fd], [], [], 0.04)
            if r_more:
                try:
                    more = os.read(fd, 64)
                    raw += more
                except OSError:
                    pass

        # Parse raw byte stream into discrete keys in queue
        i = 0
        n = len(raw)
        while i < n:
            b = raw[i:i+1]
            if b == b"\x1b":
                rem = raw[i:]
                matched_key = None
                matched_len = 0

                for seq, k_name in [
                    (b"\x1b[A", "UP"), (b"\x1bOA", "UP"), (b"\x1b[[A", "UP"),
                    (b"\x1b[B", "DOWN"), (b"\x1bOB", "DOWN"), (b"\x1b[[B", "DOWN"),
                    (b"\x1b[C", "RIGHT"), (b"\x1bOC", "RIGHT"), (b"\x1b[[C", "RIGHT"),
                    (b"\x1b[D", "LEFT"), (b"\x1bOD", "LEFT"), (b"\x1b[[D", "LEFT"),
                    (b"\x1b[Z", "BACKTAB"),
                    (b"\x1b[H", "HOME"), (b"\x1bOH", "HOME"), (b"\x1b[1~", "HOME"), (b"\x1b[7~", "HOME"),
                    (b"\x1b[F", "END"), (b"\x1bOF", "END"), (b"\x1b[4~", "END"), (b"\x1b[8~", "END"),
                    (b"\x1b[3~", "DELETE"),
                    (b"\x1b[5~", "PAGEUP"), (b"\x1b[I", "PAGEUP"),
                    (b"\x1b[6~", "PAGEDOWN"), (b"\x1b[G", "PAGEDOWN"),
                ]:
                    if rem.startswith(seq):
                        matched_key = k_name
                        matched_len = len(seq)
                        break

                if matched_key:
                    cls._queue.append(matched_key)
                    i += matched_len
                else:
                    if rem == b"\x1b":
                        cls._queue.append("ESCAPE")
                        i += 1
                    elif rem.startswith(b"\x1b[") or rem.startswith(b"\x1bO"):
                        j = i + 2
                        while j < n and (raw[j:j+1].isdigit() or raw[j:j+1] == b";"):
                            j += 1
                        if j < n:
                            j += 1
                        cls._queue.append("ESCAPE")
                        i = j
                    else:
                        cls._queue.append("ESCAPE")
                        i += 1
            elif b in (b"\r", b"\n"):
                cls._queue.append("ENTER")
                i += 1
            elif b == b"\t":
                cls._queue.append("TAB")
                i += 1
            elif b in (b"\x7f", b"\x08"):
                cls._queue.append("BACKSPACE")
                i += 1
            elif b == b" ":
                cls._queue.append("SPACE")
                i += 1
            elif b == b"\x03":
                cls._queue.append("CTRL_C")
                i += 1
            else:
                byte_val = raw[i]
                if byte_val < 0x80:
                    char_len = 1
                elif (byte_val & 0xE0) == 0xC0:
                    char_len = 2
                elif (byte_val & 0xF0) == 0xE0:
                    char_len = 3
                elif (byte_val & 0xF8) == 0xF0:
                    char_len = 4
                else:
                    char_len = 1

                chunk = raw[i:i+char_len]
                try:
                    ch = chunk.decode("utf-8")
                except UnicodeDecodeError:
                    ch = chunk.decode("latin1", errors="replace")
                cls._queue.append(ch)
                i += len(chunk)

        if cls._queue:
            return cls._queue.pop(0)
        return ""




# ==============================================================================
# 7. Interactive TUI Application (McpManagerApp)
# ==============================================================================

class McpManagerApp:
    def __init__(self, config_manager: Optional[ConfigManager] = None):
        self.config_mgr = config_manager or ConfigManager()
        self.tool_reader = ToolSchemaReader()
        self.terminal = Terminal()

        self.view = "dashboard"  # "dashboard", "form", "recipes", "tools", "confirm_delete", "confirm_discard", "confirm_overwrite"
        self.running = True
        self.status_msg = ""
        self.status_is_error = False
        self._needs_redraw = True

        # Dashboard state & viewport (M6)
        self.servers: Dict[str, McpServerModel] = {}
        self.server_keys: List[str] = []
        self.dash_selected_idx = 0
        self.dash_scroll_offset = 0
        self.cached_tool_counts: Dict[str, int] = {}

        # Form Editor state (C4, M8)
        self.form_server: Optional[McpServerModel] = None
        self.form_original_name: Optional[str] = None
        self.form_is_new = False
        self.form_tab = 1  # 1 to 5
        self.form_field_idx = 0
        self.editing_field = False
        self.edit_buffer = ""
        self.mask_secrets = True
        self.form_dirty = False

        # Tab 2 (Env / Headers table) selection state (C5, M6)
        self.table_row_idx = 0
        self.table_col_idx = 0  # 0: key, 1: value, 2: remove action
        self.env_scroll_offset = 0

        # Recipes view state & viewport (M6)
        self.recipes_idx = 0
        self.recipes_scroll_offset = 0

        # Tools viewer state & viewport (M6)
        self.tools_list: List[ToolInfo] = []
        self.tools_server_name = ""
        self.tools_scroll_offset = 0

        # Modals state
        self.delete_target = ""
        self.overwrite_target = ""

    def load_data(self):
        """Loads configuration and caches tool counts to avoid 10 fps stat sweeps (M7)."""
        self.servers = self.config_mgr.load_config()
        self.server_keys = sorted(self.servers.keys())
        if self.dash_selected_idx >= len(self.server_keys):
            self.dash_selected_idx = max(0, len(self.server_keys) - 1)
        self.cached_tool_counts = {name: self.tool_reader.get_tool_count(name) for name in self.server_keys}

    def set_status(self, msg: str, is_error: bool = False):
        self.status_msg = msg
        self.status_is_error = is_error

    # --------------------------------------------------------------------------
    # Main Loop & Signal Handlers (C7, M7)
    # --------------------------------------------------------------------------
    def run(self):
        # Pre-flight parse check before entering raw mode (C2)
        try:
            self.load_data()
        except ConfigParseError as e:
            sys.stderr.write(f"\n{Colors.RED}{Colors.BOLD}Configuration Error:{Colors.RESET} {e}\n")
            sys.stderr.write(f"Please inspect or fix {self.config_mgr.config_path} before launching the TUI.\n\n")
            sys.exit(2)

        self.terminal.enter_raw()

        # Signal handlers
        def sigwinch_handler(signum, frame):
            self._needs_redraw = True

        def sigterm_handler(signum, frame):
            self.terminal.exit_raw()
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

        try:
            signal.signal(signal.SIGWINCH, sigwinch_handler)
            signal.signal(signal.SIGTERM, sigterm_handler)
            signal.signal(signal.SIGHUP, sigterm_handler)
        except Exception:
            pass

        try:
            while self.running:
                if self._needs_redraw and not InputReader.has_queued_keys():
                    self.render()
                    self._needs_redraw = False

                key = InputReader.get_key(timeout=0.05)
                if not key:
                    continue
                if key == "CTRL_C":
                    break

                self.handle_key(key)
                self._needs_redraw = True
        finally:
            self.terminal.exit_raw()

    # --------------------------------------------------------------------------
    # Key Dispatcher
    # --------------------------------------------------------------------------
    def handle_key(self, key: str):
        if self.view == "dashboard":
            self.handle_dashboard_key(key)
        elif self.view == "form":
            self.handle_form_key(key)
        elif self.view == "recipes":
            self.handle_recipes_key(key)
        elif self.view == "tools":
            self.handle_tools_key(key)
        elif self.view == "confirm_delete":
            self.handle_delete_key(key)
        elif self.view == "confirm_discard":
            self.handle_discard_key(key)
        elif self.view == "confirm_overwrite":
            self.handle_overwrite_key(key)

    # --------------------------------------------------------------------------
    # View 1: Dashboard Handlers & Renderer (M5, M6)
    # --------------------------------------------------------------------------
    def handle_dashboard_key(self, key: str):
        if key in ("q", "Q"):
            self.running = False
            return

        total = len(self.server_keys)
        if key in ("UP", "k", "K"):
            if total > 0:
                self.dash_selected_idx = max(0, self.dash_selected_idx - 1)
        elif key in ("DOWN", "j", "J"):
            if total > 0:
                self.dash_selected_idx = min(total - 1, self.dash_selected_idx + 1)
        elif key == "TAB":
            if total > 0:
                self.dash_selected_idx = (self.dash_selected_idx + 1) % total
        elif key == "BACKTAB":
            if total > 0:
                self.dash_selected_idx = (self.dash_selected_idx - 1) % total
        elif key == "SPACE":
            if total > 0 and self.dash_selected_idx < total:
                name = self.server_keys[self.dash_selected_idx]
                srv = self.servers[name]
                srv.disabled = not srv.disabled
                self.config_mgr.set_server(srv)
                self.load_data()
                state_str = "Disabled" if srv.disabled else "Enabled"
                self.set_status(f"Server '{name}' {state_str.lower()}.")
        elif key in ("e", "E", "ENTER"):
            if total > 0 and self.dash_selected_idx < total:
                name = self.server_keys[self.dash_selected_idx]
                srv = self.servers[name]
                self.form_server = McpServerModel.from_dict(srv.name, srv.to_dict())
                self.form_original_name = srv.name
                self.form_is_new = False
                self.form_tab = 1
                self.form_field_idx = 0
                self.editing_field = False
                self.form_dirty = False
                self.table_row_idx = 0
                self.table_col_idx = 0
                self.view = "form"
        elif key in ("a", "A"):
            self.form_server = McpServerModel(name="new-mcp-server", transport="stdio", command="npx")
            self.form_original_name = None
            self.form_is_new = True
            self.form_tab = 1
            self.form_field_idx = 0
            self.editing_field = False
            self.form_dirty = False
            self.table_row_idx = 0
            self.table_col_idx = 0
            self.view = "form"
        elif key in ("t", "T"):
            self.recipes_idx = 0
            self.recipes_scroll_offset = 0
            self.view = "recipes"
        elif key in ("v", "V"):
            if total > 0 and self.dash_selected_idx < total:
                name = self.server_keys[self.dash_selected_idx]
                self.tools_server_name = name
                self.tools_list = self.tool_reader.get_tools(name)
                self.tools_scroll_offset = 0
                self.view = "tools"
        elif key in ("d", "D"):
            if total > 0 and self.dash_selected_idx < total:
                self.delete_target = self.server_keys[self.dash_selected_idx]
                self.view = "confirm_delete"

    def render_dashboard(self, width: int, height: int) -> List[str]:
        lines = []
        c = Colors

        title = " ANTIGRAVITY (AGY) MCP SERVER MANAGER "
        lines.append(f"{c.BG_CYAN}{c.BLACK}{c.BOLD}{title.center(width)}{c.RESET}")

        config_path_disp = self.config_mgr.config_path
        if len(config_path_disp) > width - 24:
            config_path_disp = "..." + config_path_disp[-(width - 27):]
        sub_bar = f"{c.DIM} Config: {c.RESET}{c.CYAN}{config_path_disp}{c.RESET}  {c.DIM}| Total Servers: {len(self.server_keys)}{c.RESET}"
        lines.append(truncate_visible(sub_bar, width))
        lines.append(f"{c.GRAY}{'─' * width}{c.RESET}")

        # Responsive column layout strictly within width (M5)
        col_st = 9
        col_tr = 8
        col_tools = 9
        col_auth = 13
        fixed_sum = col_st + col_tr + col_tools + col_auth + 6  # 45 + margins
        remaining = max(24, width - fixed_sum)
        col_name = min(22, remaining // 2)
        col_cmd = max(12, remaining - col_name)

        th = (
            f"  {c.BOLD}{'STATUS':<{col_st}}"
            f"{'SERVER NAME':<{col_name}}"
            f"{'TRANS':<{col_tr}}"
            f"{'COMMAND / URL':<{col_cmd}}"
            f"{'TOOLS':<{col_tools}}"
            f"{'AUTH':<{col_auth}}{c.RESET}"
        )
        lines.append(truncate_visible(th, width))
        lines.append(f"{c.GRAY}{'─' * width}{c.RESET}")

        # Viewport scrolling (M6)
        table_height = max(3, height - 9)
        if not self.server_keys:
            empty_msg = "No MCP servers configured yet. Press [t] for Curated Recipes or [a] to Add."
            lines.append(f"{c.YELLOW}{empty_msg.center(width)}{c.RESET}")
            for _ in range(table_height - 1):
                lines.append("")
        else:
            # Adjust viewport offset to keep selected row visible
            if self.dash_selected_idx < self.dash_scroll_offset:
                self.dash_scroll_offset = self.dash_selected_idx
            elif self.dash_selected_idx >= self.dash_scroll_offset + table_height:
                self.dash_scroll_offset = self.dash_selected_idx - table_height + 1
            self.dash_scroll_offset = max(0, min(self.dash_scroll_offset, max(0, len(self.server_keys) - table_height)))

            visible_slice = self.server_keys[self.dash_scroll_offset: self.dash_scroll_offset + table_height]
            for idx_offset, name in enumerate(visible_slice):
                actual_idx = self.dash_scroll_offset + idx_offset
                srv = self.servers[name]
                is_sel = (actual_idx == self.dash_selected_idx)

                st_pill = f"{c.GREEN}{c.BOLD}[●] ON {c.RESET}" if not srv.disabled else f"{c.GRAY}[○] OFF{c.RESET}"
                tr_pill = f"{c.MAGENTA}stdio{c.RESET}" if srv.transport == "stdio" else f"{c.BLUE}http {c.RESET}"

                if srv.transport == "stdio":
                    cmd_val = f"{srv.command} {' '.join(srv.args)}".strip()
                else:
                    cmd_val = srv.server_url
                cmd_val = sanitize_display(redact_secretish(redact_url_or_cmd(cmd_val)))  # Redact credentials & flags

                t_count = self.cached_tool_counts.get(name, 0)
                t_str = f"{t_count:>2} tools" if t_count > 0 else f"{c.DIM} 0 tools{c.RESET}"

                if srv.auth_provider == "google_credentials":
                    auth_str = f"{c.GREEN}Google ADC{c.RESET}"
                elif srv.auth_provider == "oauth":
                    auth_str = f"{c.YELLOW}OAuth 2.0{c.RESET}"
                else:
                    auth_str = f"{c.DIM}none{c.RESET}"

                disp_name = sanitize_display(name)
                if visible_len(disp_name) > col_name - 2:
                    disp_name = truncate_visible(disp_name, col_name - 3) + ".."

                if visible_len(cmd_val) > col_cmd - 2:
                    cmd_val = truncate_visible(cmd_val, col_cmd - 3) + ".."


                prefix = f"{c.CYAN}{c.BOLD}❯{c.RESET} " if is_sel else "  "
                disp_title = f"{c.BOLD}{c.CYAN}{c.UNDERLINE}{disp_name}{c.RESET}" if is_sel else f"{disp_name}"
                disp_cmd = f"{c.WHITE}{c.BOLD}{cmd_val}{c.RESET}" if is_sel else f"{c.DIM}{cmd_val}{c.RESET}"

                row_str = (
                    f"{prefix}"
                    f"{pad_visible(st_pill, col_st)}"
                    f"{pad_visible(disp_title, col_name)}"
                    f"{pad_visible(tr_pill, col_tr)}"
                    f"{pad_visible(disp_cmd, col_cmd)}"
                    f"{pad_visible(t_str, col_tools)}"
                    f"{pad_visible(auth_str, col_auth)}"
                )

                lines.append(truncate_visible(row_str, width))


            for _ in range(table_height - len(visible_slice)):
                lines.append("")

        lines.append(f"{c.GRAY}{'─' * width}{c.RESET}")
        keymap = (
            f" {c.BOLD}[Space]{c.RESET} Toggle  "
            f"{c.BOLD}[e/Enter]{c.RESET} Edit  "
            f"{c.BOLD}[a]{c.RESET} Add  "
            f"{c.BOLD}[t]{c.RESET} Recipes  "
            f"{c.BOLD}[v]{c.RESET} Tools  "
            f"{c.BOLD}[d]{c.RESET} Delete  "
            f"{c.BOLD}[q]{c.RESET} Quit"
        )
        lines.append(truncate_visible(keymap, width))

        if self.status_msg:
            status_color = f"{c.RED}{c.BOLD}" if self.status_is_error else f"{c.GREEN}{c.BOLD}"
            st_line = f" {status_color}▶ {self.status_msg}{c.RESET}"
        else:
            scroll_hint = " [↑/↓] Navigate" + (f" (showing {self.dash_scroll_offset + 1}-{min(len(self.server_keys), self.dash_scroll_offset + table_height)} of {len(self.server_keys)})" if len(self.server_keys) > table_height else "")
            st_line = f" {c.DIM}{scroll_hint}{c.RESET}"
        lines.append(truncate_visible(st_line, width))

        return lines

    # --------------------------------------------------------------------------
    # View 2: Multi-Tab Form Editor (C4, C5, M4, M8, M16)
    # --------------------------------------------------------------------------
    def handle_form_key(self, key: str):
        if not self.form_server:
            self.view = "dashboard"
            return

        # If currently editing inline text
        if self.editing_field:
            non_text_keys = {
                "UP", "DOWN", "LEFT", "RIGHT", "TAB", "BACKTAB",
                "HOME", "END", "DELETE", "PAGEUP", "PAGEDOWN",
                "ENTER", "ESCAPE", "BACKSPACE", "CTRL_C", "SPACE"
            }
            if key == "ENTER":
                self.commit_edit_buffer()
                self.editing_field = False
                self.form_dirty = True
            elif key == "ESCAPE":
                self.editing_field = False
            elif key == "BACKSPACE":
                self.edit_buffer = self.edit_buffer[:-1]
            elif key in ("SPACE", " "):
                self.edit_buffer += " "
            elif key not in non_text_keys and all(c.isprintable() for c in key):
                self.edit_buffer += key
            return

        # Wire [m] globally in form editor to toggle secret masking (M4, M16)
        if key in ("m", "M"):
            self.mask_secrets = not self.mask_secrets
            state = "masked" if self.mask_secrets else "visible"
            self.set_status(f"Secret values are now {state}.")
            return

        # Jump between tabs
        if key in ("1", "2", "3", "4", "5"):
            self.form_tab = int(key)
            self.form_field_idx = 0
            return
        if key in ("s", "S"):
            self.save_form()
            return
        if key == "ESCAPE":
            if self.form_dirty:
                self.view = "confirm_discard"
            else:
                self.view = "dashboard"
                self.set_status("Form closed.")
            return

        # Tab navigation
        if key == "TAB":
            self.form_tab = (self.form_tab % 5) + 1
            self.form_field_idx = 0
            return
        if key == "BACKTAB":
            self.form_tab = 5 if self.form_tab == 1 else self.form_tab - 1
            self.form_field_idx = 0
            return

        # Tab specific dispatch
        if self.form_tab == 1:
            self.handle_tab1_key(key)
        elif self.form_tab == 2:
            self.handle_tab2_key(key)
        elif self.form_tab == 3:
            self.handle_tab3_key(key)
        elif self.form_tab == 4:
            self.handle_tab4_key(key)
        elif self.form_tab == 5:
            self.handle_tab5_key(key)

    def handle_tab1_key(self, key: str):
        srv = self.form_server
        is_stdio = (srv.transport == "stdio")
        total_fields = 8 if is_stdio else 7

        if key in ("UP", "k", "K"):
            self.form_field_idx = (self.form_field_idx - 1) % total_fields
        elif key in ("DOWN", "j", "J"):
            self.form_field_idx = (self.form_field_idx + 1) % total_fields
        elif key in ("SPACE", "ENTER"):
            idx = self.form_field_idx
            if idx == 0:
                self.start_edit_buffer(srv.name)
            elif idx == 1:
                srv.transport = "http" if srv.transport == "stdio" else "stdio"
                self.table_row_idx = 0  # Reset table indices on transport toggle (C5)
                self.table_col_idx = 0
                self.form_dirty = True
            elif is_stdio:
                if idx == 2:
                    self.start_edit_buffer(srv.command)
                elif idx == 3:
                    self.start_edit_buffer(" ".join(shlex.quote(a) for a in srv.args))
                elif idx == 4:
                    self.start_edit_buffer(str(srv.timeout_seconds))
                elif idx == 5:
                    srv.bypass_sandbox = not srv.bypass_sandbox
                    self.form_dirty = True
                elif idx == 6:
                    srv.skip_tool_name_prefix = not srv.skip_tool_name_prefix
                    self.form_dirty = True
                elif idx == 7:
                    srv.disabled = not srv.disabled
                    self.form_dirty = True
            else:
                if idx == 2:
                    self.start_edit_buffer(srv.server_url)
                elif idx == 3:
                    self.start_edit_buffer(str(srv.timeout_seconds))
                elif idx == 4:
                    srv.bypass_sandbox = not srv.bypass_sandbox
                    self.form_dirty = True
                elif idx == 5:
                    srv.skip_tool_name_prefix = not srv.skip_tool_name_prefix
                    self.form_dirty = True
                elif idx == 6:
                    srv.disabled = not srv.disabled
                    self.form_dirty = True

    def handle_tab2_key(self, key: str):
        srv = self.form_server
        is_stdio = (srv.transport == "stdio")
        target_dict = srv.env if is_stdio else srv.headers
        keys_list = list(target_dict.keys())
        total_rows = len(keys_list)

        # Defensive index clamping (C5)
        self.table_row_idx = max(0, min(self.table_row_idx, total_rows))
        self.table_col_idx = max(0, min(self.table_col_idx, 2))

        if key == "UP":
            self.table_row_idx = max(0, self.table_row_idx - 1)
        elif key == "DOWN":
            self.table_row_idx = min(total_rows, self.table_row_idx + 1)
        elif key == "LEFT":
            self.table_col_idx = max(0, self.table_col_idx - 1)
        elif key == "RIGHT":
            self.table_col_idx = min(2, self.table_col_idx + 1)
        elif key in ("ENTER", "SPACE"):
            if self.table_row_idx == total_rows:
                # Add item
                new_key = f"VAR_{len(target_dict) + 1}" if is_stdio else f"Header-{len(target_dict) + 1}"
                target_dict[new_key] = "value"
                self.table_col_idx = 0
                self.form_dirty = True
            else:
                cur_key = keys_list[self.table_row_idx]
                if self.table_col_idx == 0:
                    self.start_edit_buffer(cur_key)
                elif self.table_col_idx == 1:
                    self.start_edit_buffer(target_dict[cur_key])
                elif self.table_col_idx == 2:
                    del target_dict[cur_key]
                    self.form_dirty = True
                    if self.table_row_idx >= len(target_dict):
                        self.table_row_idx = max(0, len(target_dict) - 1)

    def handle_tab3_key(self, key: str):
        srv = self.form_server
        total_fields = 3 if srv.auth_provider == "oauth" else 1

        if key == "UP":
            self.form_field_idx = (self.form_field_idx - 1) % total_fields
        elif key == "DOWN":
            self.form_field_idx = (self.form_field_idx + 1) % total_fields
        elif key in ("SPACE", "ENTER"):
            if self.form_field_idx == 0:
                providers = ["none", "google_credentials", "oauth"]
                cur_idx = providers.index(srv.auth_provider) if srv.auth_provider in providers else 0
                srv.auth_provider = providers[(cur_idx + 1) % len(providers)]
                self.form_dirty = True
            elif self.form_field_idx == 1:
                self.start_edit_buffer(srv.oauth_client_id)
            elif self.form_field_idx == 2:
                self.start_edit_buffer(srv.oauth_client_secret)

    def handle_tab4_key(self, key: str):
        srv = self.form_server
        total_fields = 3
        if key in ("UP", "k", "K"):
            self.form_field_idx = (self.form_field_idx - 1) % total_fields
        elif key in ("DOWN", "j", "J"):
            self.form_field_idx = (self.form_field_idx + 1) % total_fields
        elif key in ("SPACE", "ENTER"):
            if self.form_field_idx == 0:
                srv.eager = not srv.eager
                self.form_dirty = True
            elif self.form_field_idx == 1:
                srv.background = "ALWAYS" if srv.background == "OFF" else "OFF"
                self.form_dirty = True
            elif self.form_field_idx == 2:
                srv.skip_tool_name_prefix = not srv.skip_tool_name_prefix
                self.form_dirty = True

    def handle_tab5_key(self, key: str):
        if key in ("ENTER", "s", "S"):
            self.save_form()
        elif key in ("c", "C", "ESCAPE"):
            if self.form_dirty:
                self.view = "confirm_discard"
            else:
                self.view = "dashboard"
                self.set_status("Form closed.")

    def start_edit_buffer(self, val: str):
        self.edit_buffer = val
        self.editing_field = True

    def commit_edit_buffer(self):
        srv = self.form_server
        val = self.edit_buffer.strip()

        if self.form_tab == 1:
            idx = self.form_field_idx
            is_stdio = (srv.transport == "stdio")
            if idx == 0:
                srv.name = val
            elif is_stdio:
                if idx == 2:
                    srv.command = val
                elif idx == 3:
                    try:
                        srv.args = shlex.split(val)
                    except Exception:
                        srv.args = val.split()
                elif idx == 4:
                    try:
                        srv.timeout_seconds = int(val)
                        srv.explicit_timeout = True
                    except ValueError:
                        pass
            else:
                if idx == 2:
                    srv.server_url = val
                elif idx == 3:
                    try:
                        srv.timeout_seconds = int(val)
                        srv.explicit_timeout = True
                    except ValueError:
                        pass

        elif self.form_tab == 2:
            is_stdio = (srv.transport == "stdio")
            target_dict = srv.env if is_stdio else srv.headers
            keys_list = list(target_dict.keys())
            if self.table_row_idx < len(keys_list):
                old_key = keys_list[self.table_row_idx]
                if self.table_col_idx == 0:
                    if val and val != old_key:
                        target_dict[val] = target_dict.pop(old_key)
                elif self.table_col_idx == 1:
                    target_dict[old_key] = self.edit_buffer

        elif self.form_tab == 3:
            if self.form_field_idx == 1:
                srv.oauth_client_id = val
            elif self.form_field_idx == 2:
                srv.oauth_client_secret = self.edit_buffer

    def save_form(self):
        srv = self.form_server
        diags = PreflightChecker.validate_server(srv, config_path=self.config_mgr.config_path)
        if PreflightChecker.has_errors(diags):
            self.form_tab = 5
            self.set_status("Cannot save: Please fix the preflight errors shown below.", is_error=True)
            return

        # Check for collision / overwrite warning (C4)
        target_name = srv.name
        is_rename = (self.form_original_name is not None and self.form_original_name != target_name)
        if (self.form_is_new or is_rename) and target_name in self.servers:
            self.overwrite_target = target_name
            self.view = "confirm_overwrite"
            return

        self._execute_save()

    def _execute_save(self):
        srv = self.form_server
        try:
            # If renamed, remove original server key first (C4)
            if self.form_original_name and self.form_original_name != srv.name:
                self.config_mgr.remove_server(self.form_original_name)

            self.config_mgr.set_server(srv)
            self.load_data()
            self.form_dirty = False
            self.view = "dashboard"
            self.set_status(f"Server '{srv.name}' saved successfully.")
        except Exception as e:
            self.set_status(f"Error saving server: {e}", is_error=True)

    def render_form(self, width: int, height: int) -> List[str]:
        lines = []
        c = Colors
        srv = self.form_server

        mode_str = "ADD NEW MCP SERVER" if self.form_is_new else f"EDIT MCP SERVER: {srv.name}"
        lines.append(f"{c.BG_BLUE}{c.WHITE}{c.BOLD}{(' ' + mode_str + ' ').center(width)}{c.RESET}")

        tabs = [
            (1, "General"),
            (2, "Env & Headers"),
            (3, "Authentication"),
            (4, "Tooling & Context"),
            (5, "Preflight & Preview"),
        ]
        tab_line = " "
        for num, label in tabs:
            if num == self.form_tab:
                tab_line += f"{c.INVERT}{c.BOLD} [{num}] {label} {c.RESET} "
            else:
                tab_line += f"{c.DIM} [{num}] {label} {c.RESET} "
        lines.append(truncate_visible(tab_line, width))
        lines.append(f"{c.GRAY}{'─' * width}{c.RESET}")

        content_lines: List[str] = []
        if self.form_tab == 1:
            content_lines = self._render_tab1(srv, width)
        elif self.form_tab == 2:
            content_lines = self._render_tab2(srv, width, height)
        elif self.form_tab == 3:
            content_lines = self._render_tab3(srv, width)
        elif self.form_tab == 4:
            content_lines = self._render_tab4(srv, width)
        elif self.form_tab == 5:
            content_lines = self._render_tab5(srv, width, height)

        max_content = max(5, height - 7)
        for cl in content_lines[:max_content]:
            lines.append(truncate_visible(cl, width))
        for _ in range(max_content - len(content_lines[:max_content])):
            lines.append("")

        lines.append(f"{c.GRAY}{'─' * width}{c.RESET}")
        if self.editing_field:
            footer = f" {c.GREEN}{c.BOLD}EDITING:{c.RESET} [Enter] Commit  [Esc] Cancel  (Type to edit)"
        else:
            footer = (
                f" {c.BOLD}[1-5]{c.RESET} Tabs  "
                f"{c.BOLD}[↑/↓]{c.RESET} Select  "
                f"{c.BOLD}[Enter/Space]{c.RESET} Edit  "
                f"{c.BOLD}[m]{c.RESET} Mask  "
                f"{c.BOLD}[s]{c.RESET} Save  "
                f"{c.BOLD}[Esc]{c.RESET} Cancel"
            )
        lines.append(truncate_visible(footer, width))

        if self.status_msg:
            status_color = f"{c.RED}{c.BOLD}" if self.status_is_error else f"{c.GREEN}{c.BOLD}"
            st_line = f" {status_color}▶ {self.status_msg}{c.RESET}"
        else:
            dirty_indicator = f" {c.YELLOW}[Modified]{c.RESET}" if self.form_dirty else ""
            st_line = f" {c.DIM}Preflight validation ensures safe persistence.{c.RESET}{dirty_indicator}"
        lines.append(truncate_visible(st_line, width))

        return lines

    def _render_tab1(self, srv: McpServerModel, width: int) -> List[str]:
        c = Colors
        out = []
        is_stdio = (srv.transport == "stdio")

        def item(idx: int, label: str, val_disp: str, hint: str = ""):
            is_sel = (self.form_field_idx == idx)
            prefix = f"{c.CYAN}{c.BOLD}❯ " if is_sel else "  "
            if is_sel and self.editing_field:
                val_rendered = f"{c.BG_WHITE}{c.BLACK}{self.edit_buffer}█{c.RESET}"
            elif is_sel:
                val_rendered = f"{c.INVERT} {val_disp} {c.RESET}"
            else:
                val_rendered = f"{c.BOLD}{val_disp}{c.RESET}"

            line = f"{prefix}{label:<22} : {val_rendered}"
            if hint:
                line += f"  {c.DIM}{hint}{c.RESET}"
            return line

        out.append(item(0, "Server Name", sanitize_display(srv.name), "Alphanumeric ASCII identifier"))
        tr_badge = f"[{srv.transport.upper()}]"
        out.append(item(1, "Transport Mechanism", tr_badge, "stdio (local CLI) or http (remote endpoint)"))

        if is_stdio:
            cmd_resolved = shutil.which(srv.command) if srv.command else None
            path_badge = f"{c.GREEN}[✓ In PATH]{c.RESET}" if cmd_resolved else f"{c.YELLOW}[✗ Not in $PATH]{c.RESET}"
            out.append(item(2, "Executable Command", sanitize_display(srv.command) or "<empty>", path_badge))

            args_str = sanitize_display(redact_secretish(" ".join(shlex.quote(redact_url_or_cmd(a)) for a in srv.args))) if srv.args else "<none>"
            out.append(item(3, "Command Arguments", args_str, f"Parsed: {len(srv.args)} args"))
            out.append(item(4, "Timeout (Seconds)", str(srv.timeout_seconds), "Default: 60s"))
            out.append(item(5, "Bypass Sandbox", "[x] YES" if srv.bypass_sandbox else "[ ] NO", "Run outside sandbox"))
            out.append(item(6, "Skip Name Prefix", "[x] YES" if srv.skip_tool_name_prefix else "[ ] NO", "Direct tool names"))
            out.append(item(7, "Server State", "[x] DISABLED" if srv.disabled else "[ ] ENABLED", "Toggle active state"))
        else:
            safe_url = sanitize_display(redact_url_or_cmd(srv.server_url)) if srv.server_url else "<empty>"
            out.append(item(2, "Server URL", safe_url, "HTTP / HTTPS endpoint"))
            out.append(item(3, "Timeout (Seconds)", str(srv.timeout_seconds), "Default: 60s"))
            out.append(item(4, "Bypass Sandbox", "[x] YES" if srv.bypass_sandbox else "[ ] NO", "Run outside sandbox"))
            out.append(item(5, "Skip Name Prefix", "[x] YES" if srv.skip_tool_name_prefix else "[ ] NO", "Direct tool names"))
            out.append(item(6, "Server State", "[x] DISABLED" if srv.disabled else "[ ] ENABLED", "Toggle active state"))

        return out

    def _render_tab2(self, srv: McpServerModel, width: int, height: int) -> List[str]:
        c = Colors
        out = []
        is_stdio = (srv.transport == "stdio")
        target_dict = srv.env if is_stdio else srv.headers
        label_type = "Environment Variables (env)" if is_stdio else "HTTP Headers (headers)"

        mask_badge = f"{c.YELLOW}[m] Masked: ●●●●{c.RESET}" if self.mask_secrets else f"{c.CYAN}[m] Plaintext{c.RESET}"
        out.append(f" {c.BOLD}{label_type}{c.RESET}  {mask_badge}  {c.DIM}(Press [m] to toggle masking){c.RESET}")
        out.append(f"{c.GRAY}{'─' * (width - 2)}{c.RESET}")

        keys_list = list(target_dict.keys())
        total_rows = len(keys_list)
        # Defensive clamp (C5)
        self.table_row_idx = max(0, min(self.table_row_idx, total_rows))
        self.table_col_idx = max(0, min(self.table_col_idx, 2))

        # Viewport scrolling for large env maps (M6)
        visible_row_count = max(3, height - 12)
        if self.table_row_idx < self.env_scroll_offset:
            self.env_scroll_offset = self.table_row_idx
        elif self.table_row_idx >= self.env_scroll_offset + visible_row_count:
            self.env_scroll_offset = self.table_row_idx - visible_row_count + 1
        self.env_scroll_offset = max(0, min(self.env_scroll_offset, max(0, total_rows - visible_row_count)))

        if not keys_list:
            out.append(f"  {c.DIM}No items configured yet. Select '[+ Add Item]' below.{c.RESET}")
        else:
            visible_keys = keys_list[self.env_scroll_offset: self.env_scroll_offset + visible_row_count]
            for r_offset, k in enumerate(visible_keys):
                r_idx = self.env_scroll_offset + r_offset
                v = target_dict[k]
                disp_k = sanitize_display(k)
                disp_v = "●●●●●●●●" if self.mask_secrets else sanitize_display(v)

                is_row_sel = (self.table_row_idx == r_idx)
                k_sel = is_row_sel and (self.table_col_idx == 0)
                v_sel = is_row_sel and (self.table_col_idx == 1)
                del_sel = is_row_sel and (self.table_col_idx == 2)

                if k_sel and self.editing_field:
                    k_str = f"{c.BG_WHITE}{c.BLACK}{self.edit_buffer}█{c.RESET}"
                elif k_sel:
                    k_str = f"{c.INVERT} {disp_k} {c.RESET}"
                else:
                    k_str = f"{c.BOLD}{disp_k}{c.RESET}"

                if v_sel and self.editing_field:
                    v_str = f"{c.BG_WHITE}{c.BLACK}{self.edit_buffer}█{c.RESET}"
                elif v_sel:
                    v_str = f"{c.INVERT} {disp_v} {c.RESET}"
                else:
                    v_str = f"{disp_v}"

                del_str = f"{c.RED}{c.BOLD}[Delete]{c.RESET}" if del_sel else f"{c.DIM}[Delete]{c.RESET}"
                row_prefix = f"{c.CYAN}❯{c.RESET} " if is_row_sel else "  "
                out.append(f"{row_prefix}{k_str:<26} = {v_str:<28} {del_str}")

        is_add_sel = (self.table_row_idx == total_rows)
        add_badge = f"{c.INVERT} [+ Add Item] {c.RESET}" if is_add_sel else f"{c.GREEN}[+ Add Item]{c.RESET}"
        out.append("")
        out.append(f"  {add_badge}  {c.DIM}Press Enter or Space to append a key/value pair{c.RESET}")
        return out

    def _render_tab3(self, srv: McpServerModel, width: int) -> List[str]:
        c = Colors
        out = []

        out.append(f" {c.BOLD}Authentication Provider Configuration{c.RESET}")
        out.append(f"{c.GRAY}{'─' * (width - 2)}{c.RESET}")

        is_sel_0 = (self.form_field_idx == 0)
        p_badge = f"{c.INVERT} {srv.auth_provider} {c.RESET}" if is_sel_0 else f"{c.BOLD}{srv.auth_provider}{c.RESET}"
        out.append(f"  Auth Provider       : {p_badge}  {c.DIM}(Press Space/Enter to cycle){c.RESET}")
        out.append("")

        if srv.auth_provider == "google_credentials":
            out.append(f"  {c.GREEN}{c.BOLD}✓ AGY Native Integration: Google Application Default Credentials (ADC){c.RESET}")
            out.append(f"  {c.DIM}Automatically acquires and injects OAuth2 Bearer tokens via local gcloud ADC.{c.RESET}")
        elif srv.auth_provider == "oauth":
            out.append(f"  {c.YELLOW}{c.BOLD}OAuth 2.0 Client Flow (Native schema: oauth.clientId, oauth.clientSecret){c.RESET}")

            safe_client_id = sanitize_display(srv.oauth_client_id)
            unmasked_sec = sanitize_display(srv.oauth_client_secret)

            is_sel_1 = (self.form_field_idx == 1)
            if is_sel_1 and self.editing_field:
                id_disp = f"{c.BG_WHITE}{c.BLACK}{self.edit_buffer}█{c.RESET}"
            elif is_sel_1:
                id_disp = f"{c.INVERT} {safe_client_id or '<empty>'} {c.RESET}"
            else:
                id_disp = f"{c.BOLD}{safe_client_id or '<empty>'}{c.RESET}"
            out.append(f"  OAuth Client ID     : {id_disp}")

            is_sel_2 = (self.form_field_idx == 2)
            masked_sec = "●●●●●●●●" if (self.mask_secrets and srv.oauth_client_secret) else (unmasked_sec or "<empty>")
            if is_sel_2 and self.editing_field:
                sec_disp = f"{c.BG_WHITE}{c.BLACK}{self.edit_buffer}█{c.RESET}"
            elif is_sel_2:
                sec_disp = f"{c.INVERT} {masked_sec} {c.RESET}"
            else:
                sec_disp = f"{c.BOLD}{masked_sec}{c.RESET}"
            out.append(f"  OAuth Client Secret : {sec_disp}  {c.DIM}(Press [m] to toggle masking){c.RESET}")
        else:
            out.append(f"  {c.DIM}No authentication configured. Standard transport headers only.{c.RESET}")

        return out


    def _render_tab4(self, srv: McpServerModel, width: int) -> List[str]:
        c = Colors
        out = []

        out.append(f" {c.BOLD}Tooling, Context & Concurrency Optimization{c.RESET}")
        out.append(f"{c.GRAY}{'─' * (width - 2)}{c.RESET}")

        is_sel_0 = (self.form_field_idx == 0)
        lazy_status = f"{c.GREEN}[x] LAZY LOADING (Recommended){c.RESET}" if not srv.eager else f"{c.YELLOW}[ ] EAGER LOADING (Context Bloat){c.RESET}"
        if is_sel_0:
            lazy_status = f"{c.INVERT} {lazy_status} {c.RESET}"
        out.append(f"  Tool Discovery Mode  : {lazy_status}")
        out.append(f"    {c.DIM}Lazy mode caches schemas locally and loads on demand, saving up to 80% prompt tokens.{c.RESET}")
        out.append("")

        is_sel_1 = (self.form_field_idx == 1)
        bg_status = f"{c.CYAN}[x] ALWAYS (Asynchronous){c.RESET}" if srv.background == "ALWAYS" else f"{c.DIM}[ ] OFF (Synchronous){c.RESET}"
        if is_sel_1:
            bg_status = f"{c.INVERT} {bg_status} {c.RESET}"
        out.append(f"  Background Execution : {bg_status}")
        out.append(f"    {c.DIM}When ALWAYS, long-running tools run asynchronously without stalling agent turns.{c.RESET}")
        out.append("")

        is_sel_2 = (self.form_field_idx == 2)
        prefix_status = f"{c.YELLOW}[x] SKIP PREFIX (Direct Names){c.RESET}" if srv.skip_tool_name_prefix else f"{c.GREEN}[ ] PREFIX WITH SERVER NAME{c.RESET}"
        if is_sel_2:
            prefix_status = f"{c.INVERT} {prefix_status} {c.RESET}"
        out.append(f"  Tool Namespacing     : {prefix_status}")
        out.append(f"    {c.DIM}When skipped, tools are exposed directly as 'tool_name' without server namespace.{c.RESET}")

        return out

    def _render_tab5(self, srv: McpServerModel, width: int, height: int) -> List[str]:
        c = Colors
        out = []

        out.append(f" {c.BOLD}Preflight Diagnostics & JSON Configuration Preview{c.RESET}")
        out.append(f"{c.GRAY}{'─' * (width - 2)}{c.RESET}")

        diags = PreflightChecker.validate_server(srv, config_path=self.config_mgr.config_path)
        has_err = PreflightChecker.has_errors(diags)

        out.append(f" {c.BOLD}Preflight Checks:{c.RESET}")
        for d in diags:
            if d.level == "PASS":
                badge = f"{c.GREEN}[✓ PASS]{c.RESET}"
            elif d.level == "INFO":
                badge = f"{c.BLUE}[ℹ INFO]{c.RESET}"
            elif d.level == "WARNING":
                badge = f"{c.YELLOW}[⚠ WARN]{c.RESET}"
            else:
                badge = f"{c.RED}{c.BOLD}[✗ FAIL]{c.RESET}"
            out.append(f"  {badge} {d.message}")

        out.append("")
        mask_notice = f" {c.DIM}(Secrets masked; press [m] to toggle){c.RESET}" if self.mask_secrets else ""
        out.append(f" {c.BOLD}JSON Payload Preview (~/.gemini/config/mcp_config.json):{c.RESET}{mask_notice}")

        # Redact secrets in JSON preview if mask_secrets is active (M4)
        raw_dict = srv.to_dict()
        preview_data = {srv.name: redact_server_dict(raw_dict) if self.mask_secrets else raw_dict}
        json_str = json.dumps(preview_data, indent=2)
        for line in json_str.splitlines():
            out.append(f"  {c.CYAN}{line}{c.RESET}")

        out.append("")
        if has_err:
            out.append(f"  {c.RED}{c.BOLD}Cannot save: Fix the preflight errors above before persisting.{c.RESET}")
        else:
            out.append(f"  {c.GREEN}{c.BOLD}[ Enter / 's' ] Save Configuration Now{c.RESET}    {c.DIM}[ Esc / 'c' ] Cancel{c.RESET}")

        return out

    # --------------------------------------------------------------------------
    # View 3: Curated Recipes Catalog (M6, M17)
    # --------------------------------------------------------------------------
    def handle_recipes_key(self, key: str):
        total = len(RECIPES_CATALOG)
        if key in ("q", "Q", "ESCAPE"):
            self.view = "dashboard"
        elif key in ("UP", "k", "K"):
            self.recipes_idx = max(0, self.recipes_idx - 1)
        elif key in ("DOWN", "j", "J"):
            self.recipes_idx = min(total - 1, self.recipes_idx + 1)
        elif key == "ENTER":
            chosen = RECIPES_CATALOG[self.recipes_idx]
            base = chosen.server
            self.form_server = McpServerModel.from_dict(base.name, base.to_dict())
            self.form_original_name = None
            self.form_is_new = True
            self.form_tab = 1
            self.form_field_idx = 0
            self.editing_field = False
            self.form_dirty = True
            self.table_row_idx = 0
            self.table_col_idx = 0
            self.view = "form"
            self.set_status(f"Loaded recipe '{chosen.title}'. Review and save.")

    def render_recipes(self, width: int, height: int) -> List[str]:
        lines = []
        c = Colors

        title = " CURATED MCP SERVER RECIPES & TEMPLATES "
        lines.append(f"{c.BG_CYAN}{c.BLACK}{c.BOLD}{title.center(width)}{c.RESET}")
        lines.append(f" {c.DIM}Select a production-ready template to preview, customize, and persist.{c.RESET}")
        lines.append(f"{c.GRAY}{'─' * width}{c.RESET}")

        list_height = max(5, height - 7)
        if self.recipes_idx < self.recipes_scroll_offset:
            self.recipes_scroll_offset = self.recipes_idx
        elif self.recipes_idx >= self.recipes_scroll_offset + list_height:
            self.recipes_scroll_offset = self.recipes_idx - list_height + 1
        self.recipes_scroll_offset = max(0, min(self.recipes_scroll_offset, max(0, len(RECIPES_CATALOG) - list_height)))

        visible_recipes = RECIPES_CATALOG[self.recipes_scroll_offset: self.recipes_scroll_offset + list_height]
        for idx_offset, recipe in enumerate(visible_recipes):
            actual_idx = self.recipes_scroll_offset + idx_offset
            is_sel = (actual_idx == self.recipes_idx)
            badge = f"{c.MAGENTA}[{recipe.badge}]{c.RESET}"

            if is_sel:
                row = f"{c.INVERT}❯ {recipe.title:<24} [{recipe.badge}]  {recipe.description}{c.RESET}"
            else:
                row = f"  {c.BOLD}{recipe.title:<24}{c.RESET} {badge:<14} {recipe.description}"
            lines.append(truncate_visible(row, width))

        for _ in range(list_height - len(visible_recipes)):
            lines.append("")

        lines.append(f"{c.GRAY}{'─' * width}{c.RESET}")
        lines.append(truncate_visible(f" {c.BOLD}[Enter]{c.RESET} Use Recipe  {c.BOLD}[↑/↓/j/k]{c.RESET} Navigate  {c.BOLD}[Esc/q]{c.RESET} Back", width))
        return lines

    # --------------------------------------------------------------------------
    # View 4: Discovered Tools Viewer (M6)
    # --------------------------------------------------------------------------
    def handle_tools_key(self, key: str):
        if key in ("q", "Q", "ESCAPE"):
            self.view = "dashboard"
        elif key in ("UP", "k", "K"):
            self.tools_scroll_offset = max(0, self.tools_scroll_offset - 1)
        elif key in ("DOWN", "j", "J"):
            self.tools_scroll_offset += 1

        elif key == "PAGEUP":
            self.tools_scroll_offset = max(0, self.tools_scroll_offset - 10)
        elif key == "PAGEDOWN":
            self.tools_scroll_offset += 10

    def render_tools(self, width: int, height: int) -> List[str]:
        lines = []
        c = Colors

        srv_name = sanitize_display(self.tools_server_name)
        title = f" DISCOVERED CACHED TOOLS: {srv_name} ({len(self.tools_list)} tools) "
        lines.append(f"{c.BG_CYAN}{c.BLACK}{c.BOLD}{title.center(width)}{c.RESET}")
        lines.append(f" {c.DIM}Schemas cached under ~/.gemini/antigravity-cli/mcp/{srv_name}/{c.RESET}")
        lines.append(f"{c.GRAY}{'─' * width}{c.RESET}")

        body_lines: List[str] = []
        if not self.tools_list:
            body_lines.append("")
            body_lines.append(f"  {c.YELLOW}No cached tool schemas found for '{srv_name}'.{c.RESET}")
            body_lines.append(f"  {c.DIM}Schemas are discovered and cached when AGY connects to the MCP server.{c.RESET}")
        else:
            for t in self.tools_list:
                t_name = sanitize_display(t.name)
                t_params = sanitize_display(t.parameters_summary)
                body_lines.append(f"  {c.GREEN}{c.BOLD}▶ {t_name}{c.RESET}  {c.CYAN}({t_params}){c.RESET}")
                if t.description:
                    first_line = sanitize_display(t.description.splitlines()[0])
                    body_lines.append(f"    {c.DIM}{first_line[:width - 6]}{c.RESET}")
                body_lines.append("")

        max_items = max(5, height - 7)
        # Bounded clamping for tools scroll offset (M6)
        max_scroll = max(0, len(body_lines) - max_items)
        self.tools_scroll_offset = max(0, min(self.tools_scroll_offset, max_scroll))

        visible_lines = body_lines[self.tools_scroll_offset: self.tools_scroll_offset + max_items]
        for vl in visible_lines:
            lines.append(truncate_visible(vl, width))
        for _ in range(max_items - len(visible_lines)):
            lines.append("")

        lines.append(f"{c.GRAY}{'─' * width}{c.RESET}")
        lines.append(truncate_visible(f" {c.BOLD}[↑/↓/PgUp/PgDn]{c.RESET} Scroll  {c.BOLD}[Esc/q]{c.RESET} Back to Dashboard", width))
        return lines

    # --------------------------------------------------------------------------
    # View 5: Modals (Delete, Discard, Overwrite) (C4, M8)
    # --------------------------------------------------------------------------
    def handle_delete_key(self, key: str):
        if key in ("y", "Y"):
            self.config_mgr.remove_server(self.delete_target)
            self.load_data()
            self.view = "dashboard"
            self.set_status(f"Server '{self.delete_target}' removed.")
        elif key in ("n", "N", "ESCAPE", "q", "Q"):
            self.view = "dashboard"
            self.set_status("Deletion canceled.")

    def handle_discard_key(self, key: str):
        if key in ("y", "Y"):
            self.form_dirty = False
            self.view = "dashboard"
            self.set_status("Form changes discarded.")
        elif key in ("n", "N", "ESCAPE"):
            self.view = "form"

    def handle_overwrite_key(self, key: str):
        if key in ("y", "Y"):
            self._execute_save()
        elif key in ("n", "N", "ESCAPE"):
            self.view = "form"
            self.set_status("Save canceled. Please rename the server to avoid collision.", is_error=True)

    def render_modal(self, title: str, message: str, hint: str, actions: str, width: int, height: int) -> List[str]:
        base_lines = self.render_dashboard(width, height) if self.view == "confirm_delete" else self.render_form(width, height)
        c = Colors

        title = sanitize_display(title)
        message = sanitize_display(message)
        hint = sanitize_display(hint)
        actions = sanitize_display(actions)

        modal_w = min(62, width - 4)
        modal_h = 7
        start_y = max(1, (height - modal_h) // 2)
        start_x = max(2, (width - modal_w) // 2)

        modal_box = [
            f"{c.BG_BLACK}{c.RED}{c.BOLD}┌{'─' * (modal_w - 2)}┐{c.RESET}",
            f"{c.BG_BLACK}{c.WHITE}{c.BOLD}│{title.center(modal_w - 2)}│{c.RESET}",
            f"{c.BG_BLACK}{c.GRAY}│{'─' * (modal_w - 2)}│{c.RESET}",
            f"{c.BG_BLACK}{c.WHITE}│{truncate_visible(message, modal_w - 4).center(modal_w - 2)}│{c.RESET}",
            f"{c.BG_BLACK}{c.DIM}│{truncate_visible(hint, modal_w - 4).center(modal_w - 2)}│{c.RESET}",
            f"{c.BG_BLACK}{c.WHITE}{c.BOLD}│{actions.center(modal_w - 2)}│{c.RESET}",
            f"{c.BG_BLACK}{c.RED}{c.BOLD}└{'─' * (modal_w - 2)}┘{c.RESET}",
        ]

        for idx, m_line in enumerate(modal_box):
            y = start_y + idx
            if 0 <= y < len(base_lines):
                base_lines[y] = f"{' ' * start_x}{m_line}"

        return base_lines

    # --------------------------------------------------------------------------
    # Main Render Function (C9, M5, M7)
    # --------------------------------------------------------------------------
    def render(self):
        w, h = Terminal.get_size()

        # Minimum width check (M5)
        if w < 70:
            notice = f"Terminal width ({w} cols) is too narrow. Minimum 70 cols required. Please expand your window."
            cleared = [Colors.CLEAR_SCREEN, "", notice, ""]
            sys.stdout.write("\033[H" + "\r\n".join(l + Colors.CLEAR_LINE for l in cleared))
            sys.stdout.flush()
            return

        if self.view == "dashboard":
            lines = self.render_dashboard(w, h)
        elif self.view == "form":
            lines = self.render_form(w, h)
        elif self.view == "recipes":
            lines = self.render_recipes(w, h)
        elif self.view == "tools":
            lines = self.render_tools(w, h)
        elif self.view == "confirm_delete":
            lines = self.render_modal(
                " CONFIRM SERVER REMOVAL ",
                f"Are you sure you want to remove: '{sanitize_display(self.delete_target)}'?",
                "An automated backup will be created before deletion.",
                "[y] Yes, Delete     [n] No, Cancel",
                w, h
            )
        elif self.view == "confirm_discard":
            lines = self.render_modal(
                " DISCARD UNSAVED CHANGES ",
                "You have modified this configuration.",
                "Discard all changes and return to the dashboard?",
                "[y] Yes, Discard     [n] No, Keep Editing",
                w, h
            )
        elif self.view == "confirm_overwrite":
            lines = self.render_modal(
                " CONFIRM OVERWRITE ",
                f"Server '{sanitize_display(self.overwrite_target)}' already exists.",
                "Saving will replace the existing configuration.",
                "[y] Overwrite     [n] Cancel & Rename",
                w, h
            )
        else:
            lines = [f"Unknown view: {self.view}"]

        # Render with \r\n to prevent staircase artifacts under tty.setraw() (C9)
        # Use \033[H and per-line \033[K to eliminate full-screen repaint flicker (M7)
        cleared_lines = [l + Colors.CLEAR_LINE for l in lines[:h]]
        output = "\033[H" + "\r\n".join(cleared_lines)
        sys.stdout.write(output)
        sys.stdout.flush()


# ==============================================================================
# 8. Non-Interactive CLI Interface (M11, M15)
# ==============================================================================

def run_cli_list(config_mgr: ConfigManager, as_json: bool = False, show_secrets: bool = False):
    try:
        servers = config_mgr.load_config()
    except ConfigParseError as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(2)

    if as_json:
        if not show_secrets:
            sys.stderr.write("Note: secret values are redacted. Pass --show-secrets to emit them.\n")
            out_dict = {name: redact_server_dict(srv.to_dict()) for name, srv in sorted(servers.items())}
        else:
            out_dict = {name: srv.to_dict() for name, srv in sorted(servers.items())}
        print(json.dumps(out_dict, indent=2, ensure_ascii=False))
        sys.exit(0)


    tool_reader = ToolSchemaReader()
    c = Colors

    if not servers:
        print("No MCP servers configured in " + config_mgr.config_path)
        sys.exit(0)

    col_name = 28
    col_st = 10
    col_tr = 11
    col_tools = 10
    col_auth = 16

    header = (
        f"{c.BOLD}{'NAME':<{col_name}} "
        f"{'STATUS':<{col_st}} "
        f"{'TRANSPORT':<{col_tr}} "
        f"{'TOOLS':<{col_tools}} "
        f"{'AUTH':<{col_auth}} "
        f"{'COMMAND / URL'}{c.RESET}"
    )
    print(header)
    print("─" * 105)

    for name in sorted(servers.keys()):
        srv = servers[name]
        status_disp = f"{c.GREEN}[●] ON {c.RESET}" if not srv.disabled else f"{c.GRAY}[○] OFF{c.RESET}"
        tr_disp = f"{c.MAGENTA}stdio{c.RESET}" if srv.transport == "stdio" else f"{c.BLUE}http {c.RESET}"
        t_count = tool_reader.get_tool_count(name)
        tool_disp = f"{t_count:>2} tools" if t_count > 0 else f"{c.DIM} 0 tools{c.RESET}"

        if srv.auth_provider == "google_credentials":
            auth_disp = f"{c.GREEN}Google ADC{c.RESET}"
        elif srv.auth_provider == "oauth":
            auth_disp = f"{c.YELLOW}OAuth 2.0{c.RESET}"
        else:
            auth_disp = f"{c.DIM}none{c.RESET}"

        target = f"{srv.command} {' '.join(srv.args)}".strip() if srv.transport == "stdio" else srv.server_url
        target = sanitize_display(redact_secretish(redact_url_or_cmd(target)))
        safe_name = sanitize_display(name)

        row = (
            f"{pad_visible(f'{c.BOLD}{safe_name}{c.RESET}', col_name)} "
            f"{pad_visible(status_disp, col_st)} "
            f"{pad_visible(tr_disp, col_tr)} "
            f"{pad_visible(tool_disp, col_tools)} "
            f"{pad_visible(auth_disp, col_auth)} "
            f"{target}"
        )
        print(row)
    sys.exit(0)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Antigravity (AGY) MCP Server Manager & Interactive TUI Configurator",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "-c", "--config",
        metavar="PATH",
        default=DEFAULT_CONFIG_PATH,
        help="Path to mcp_config.json file (default: ~/.gemini/config/mcp_config.json or $AGY_MCP_CONFIG)",
    )
    parser.add_argument(
        "-l", "--list",
        action="store_true",
        help="List all configured MCP servers with status and details.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Format output as raw JSON (used with --list).",
    )
    parser.add_argument(
        "--show-secrets",
        action="store_true",
        help="Emit secret values unredacted in --list --json / --dry-run output. Do NOT use in CI or shared logs.",
    )
    parser.add_argument(
        "-e", "--enable",
        metavar="NAME",
        help="Enable an MCP server in mcp_config.json.",
    )
    parser.add_argument(
        "-d", "--disable",
        metavar="NAME",
        help="Disable an MCP server in mcp_config.json.",
    )
    parser.add_argument(
        "-r", "--remove",
        metavar="NAME",
        help="Remove an MCP server from mcp_config.json.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate changes and print payload without writing to disk.",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"AGY MCP Manager v{VERSION}",
        help="Show version information and exit.",
    )
    return parser


def main():
    Colors.reconfigure(supports_color())
    parser = build_arg_parser()
    args = parser.parse_args()

    config_mgr = ConfigManager(args.config)

    # Interactive TUI mode (when no operation flags specified and both stdin/stdout are TTY)
    is_interactive = not (args.list or args.enable or args.disable or args.remove)
    if is_interactive:
        if sys.stdin.isatty() and sys.stdout.isatty():
            app = McpManagerApp(config_mgr)
            app.run()
            sys.exit(0)
        else:
            # Piped or non-interactive stdout: fallback to list
            run_cli_list(config_mgr, as_json=args.json, show_secrets=args.show_secrets)
            sys.exit(0)

    if args.list:
        run_cli_list(config_mgr, as_json=args.json, show_secrets=args.show_secrets)

    try:
        if args.enable:
            name = args.enable
            srv = config_mgr.get_server(name)
            if not srv:
                sys.stderr.write(f"Error: Server '{name}' not found in configuration.\n")
                sys.exit(1)
            if args.dry_run:
                srv.disabled = False
                payload = srv.to_dict() if args.show_secrets else redact_server_dict(srv.to_dict())
                print(f"[Dry Run] Would enable server '{name}':")
                print(json.dumps({name: payload}, indent=2, ensure_ascii=False))
                sys.exit(0)
            config_mgr.enable_server(name)
            print(f"✓ Enabled MCP server '{name}'.")
            sys.exit(0)

        if args.disable:
            name = args.disable
            srv = config_mgr.get_server(name)
            if not srv:
                sys.stderr.write(f"Error: Server '{name}' not found in configuration.\n")
                sys.exit(1)
            if args.dry_run:
                srv.disabled = True
                payload = srv.to_dict() if args.show_secrets else redact_server_dict(srv.to_dict())
                print(f"[Dry Run] Would disable server '{name}':")
                print(json.dumps({name: payload}, indent=2, ensure_ascii=False))
                sys.exit(0)
            config_mgr.disable_server(name)
            print(f"✓ Disabled MCP server '{name}'.")
            sys.exit(0)

        if args.remove:
            name = args.remove
            srv = config_mgr.get_server(name)
            if not srv:
                sys.stderr.write(f"Error: Server '{name}' not found in configuration.\n")
                sys.exit(1)
            if args.dry_run:
                print(f"[Dry Run] Would remove server '{name}' from configuration.")
                sys.exit(0)
            config_mgr.remove_server(name)
            print(f"✓ Removed MCP server '{name}'. Backup saved to {config_mgr.backup_path}.")
            sys.exit(0)


    except ConfigParseError as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(2)
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
