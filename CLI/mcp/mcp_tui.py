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
import time
import subprocess
import urllib.parse
import re
import atexit
import stat
import tempfile
import fcntl
import unicodedata
import copy
import argparse
import hashlib
from datetime import datetime
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple, Set, Callable, Union

DEFAULT_CONFIG_PATH = os.environ.get("AGY_MCP_CONFIG") or os.path.expanduser("~/.gemini/config/mcp_config.json")
DEFAULT_MCP_CACHE_DIR = os.path.expanduser("~/.gemini/antigravity-cli/mcp")
VERSION = "2.0.0"

# Known modeled server keys in McpServerModel
_KNOWN_SERVER_KEYS = {
    "command", "args", "env", "serverUrl", "url", "headers", "transport", "disabled",
    "authProviderType", "auth_provider", "oauth", "oauthClientId", "oauth_client_id",
    "oauthClientSecret", "oauth_client_secret", "authConfig",
    "toolConfig", "eager", "background", "timeoutSeconds", "timeout_seconds", "timeout",
    "disabledTools", "disabled_tools", "enabledTools", "enabled_tools"
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


MASK_SECRET = "●●●●●●●●"
MASK_PLACEHOLDER = MASK_SECRET

_TOKEN_ALTS = (
    r"sk-[A-Za-z0-9_-]{8,}",
    r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{15,}",
    r"xox[baprs]-[0-9A-Za-z-]{20,}",
    r"bot[0-9]+:[A-Za-z0-9_-]+",
    r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
    r"AKIA[0-9A-Z]{16}",
)
_TOKEN_SEARCH_RE = re.compile(r"(?<![A-Za-z0-9_-])(?:" + "|".join(_TOKEN_ALTS) + ")")
_TARGETED_TOKEN_PATTERNS = [re.compile(f"^(?:{a})$") for a in _TOKEN_ALTS]

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

_SENSITIVE_FLAG_RE = re.compile(
    r"^--?(?!(?:no|disable|skip|without)[-_])(?:[a-zA-Z0-9]+[-_])*(?:token|secret|password|passwd|passphrase|api[-_]?key|auth|bearer|credential|cookie|session|private[-_]?key|sig|signature|pass|pat|pwd|key)$"
    r"|^--?[a-z][a-zA-Z0-9]*(?:Token|Secret|Password|Passphrase|ApiKey|PrivateKey|Credential|Cookie|Session|Sig|Signature|Pass|Pat|Pwd|Key)"
    r"|^--?(?:[a-zA-Z0-9]+[-_])*key$",
    re.IGNORECASE,
)

FLAG_ARG_RE = re.compile(
    r'(^|\s)(--?[A-Za-z0-9][A-Za-z0-9_-]*)(?:(=)("[^"]*"|\'[^\']*\'|\S+)|(\s+)(?!-)("[^"]*"|\'[^\']*\'|\S+))'
)
_STANDALONE_FLAG_RE = re.compile(r"^--?[A-Za-z0-9][A-Za-z0-9_.-]*$")
_STRUCTURAL_FLAGS = {"-H", "--header", "-header", "-u", "--user", "-U", "--proxy-user", "-e", "--env", "-env"}

_SENSITIVE_WORDS_EXACT = {
    'token', 'secret', 'password', 'passwd', 'key', 'apikey', 'credential',
    'auth', 'bearer', 'sig', 'signature', 'cert', 'certificate', 'private',
    'passphrase', 'code', 'session', 'pass', 'pat', 'pwd',
    'authorization', 'cookie', 'cookies', 'session_id', 'session-id'
}

_SENSITIVE_SUBSTRINGS_CLEANED = {
    'secret', 'password', 'passwd', 'passphrase', 'credential',
    'privatekey', 'apikey', 'accesstoken', 'clientsecret',
    'authorization', 'cookie', 'cookies', 'sessionid'
}

def _is_inside_git_repo(path: str) -> bool:
    try:
        cur = os.path.realpath(path)
        if not os.path.isdir(cur):
            cur = os.path.dirname(cur)
        while cur and cur != os.path.dirname(cur):
            if os.path.exists(os.path.join(cur, ".git")):
                return True
            cur = os.path.dirname(cur)
    except Exception:
        pass
    return False

def _find_unclosed_quote(buf: str) -> Optional[Tuple[int, str]]:
    """Finds index and quote char of the unclosed quote, or None if quotes are balanced."""
    state = None
    quote_idx = -1
    i = 0
    n = len(buf)
    while i < n:
        c = buf[i]
        if state is None:
            if c == "\\":
                i += 2
                continue
            elif c in ('"', "'"):
                state = c
                quote_idx = i
        elif state == '"':
            if c == "\\":
                i += 2
                continue
            elif c == '"':
                state = None
                quote_idx = -1
        elif state == "'":
            if c == "'":
                state = None
                quote_idx = -1
        i += 1
    if state is not None:
        return quote_idx, state
    return None

def _is_sensitive_param_name(name: str) -> bool:
    if not name or not isinstance(name, str):
        return False
    name_l = name.lower()
    if name_l in _SENSITIVE_WORDS_EXACT:
        return True
    parts = re.split(r'[^A-Za-z0-9]+', name)
    tokens: List[str] = []
    for part in parts:
        if not part:
            continue
        camel_parts = re.findall(r'[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+', part)
        if camel_parts:
            tokens.extend(w.lower() for w in camel_parts)
        else:
            tokens.append(part.lower())
    if any(t in _SENSITIVE_WORDS_EXACT for t in tokens):
        return True
    cleaned = re.sub(r'[^a-z0-9]', '', name_l)
    if any(s in cleaned for s in _SENSITIVE_SUBSTRINGS_CLEANED):
        return True
    return False


def _flag_name(arg: str) -> str:
    """Strips leading dashes and anything after = to extract the flag name."""
    if not isinstance(arg, str):
        return ""
    flag = arg.partition("=")[0].strip()
    return re.sub(r'^-+', '', flag)


def _is_sensitive_flag(flag: str) -> bool:
    """Checks if a CLI flag is sensitive, rejecting negation prefixes."""
    if not isinstance(flag, str) or not flag:
        return False
    norm = _flag_name(flag)
    if not norm:
        return False
    if re.match(r'^(?:no|disable|skip|without)[-_]', norm, re.IGNORECASE):
        return False
    if norm.lower() in ("maxtokens", "max-tokens"):
        return False
    return _is_sensitive_param_name(norm)


_SENSITIVE_KEY_RE = _SENSITIVE_FLAG_RE
_ATTACHED_USER_RE = re.compile(r'(?i)(^|\s)(-u|--user)(["\'])([^":]*):([^"\']*)(\3)')

def _mask_userinfo(user: str, pw: Optional[str] = None) -> str:
    """Masks credentials in userinfo slots, protecting tokens in user-slot."""
    if pw is None:
        return MASK_SECRET
    if pw == "":
        return f"{MASK_SECRET}:"
    user_dec = urllib.parse.unquote(user) if isinstance(user, str) else ""
    if len(pw) <= 1 or pw.lower() in {"x", "x-oauth-basic", "api_token", "token"}:
        return f"{MASK_SECRET}:{MASK_SECRET}"
    user_is_token = (
        _is_token_shaped(user)
        or _is_token_shaped(user_dec)
        or bool(_TOKEN_SEARCH_RE.search(user))
        or bool(_TOKEN_SEARCH_RE.search(user_dec))
    )
    if user_is_token:
        return f"{MASK_SECRET}:{MASK_SECRET}"
    else:
        return f"{user}:{MASK_SECRET}"

_ALLOWLISTED_KEYS = {
    "authProviderType", "transport", "command", "args", "env", "headers",
    "serverUrl", "url", "toolConfig", "eager", "background", "timeoutSeconds",
    "timeout", "disabled", "name",
    "clientId", "client_id", "oauthClientId", "oauth_client_id",
    "path", "maxTokens", "max_tokens", "tokens"
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

_RFC9110_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+\-.^_`|~]+$")


def _redact_header_str(h: str) -> str:
    """Redacts bearer tokens or sensitive header values in header string representations."""
    colon_idx = h.find(":")
    equal_idx = h.find("=")
    if colon_idx != -1 and equal_idx != -1:
        sep_idx = min(colon_idx, equal_idx)
    elif colon_idx != -1:
        sep_idx = colon_idx
    elif equal_idx != -1:
        sep_idx = equal_idx
    else:
        sep_idx = -1

    if sep_idx != -1:
        sep = h[sep_idx]
        name = h[:sep_idx]
        val = h[sep_idx + 1:]
        name_clean = name.strip()
        name_lower = name_clean.lower()
        if name_lower in ("authorization", "proxy-authorization"):
            bearer_match = re.match(r"^(\s*(?:Bearer|Basic|Token)\s+)(.+)$", val, re.IGNORECASE)
            if bearer_match:
                prefix = bearer_match.group(1)
                return f"{name}{sep}{prefix}{MASK_PLACEHOLDER}"
            else:
                leading_ws = len(val) - len(val.lstrip())
                return f"{name}{sep}{val[:leading_ws]}{MASK_PLACEHOLDER}"
        elif _is_sensitive_param_name(name_clean) or name_lower not in _PUBLIC_HEADERS_ALLOWLIST:
            leading_ws = len(val) - len(val.lstrip())
            return f"{name}{sep}{val[:leading_ws]}{MASK_PLACEHOLDER}"
        else:
            return h
    return redact_url_or_cmd(h)


_URL_QUERY_SENSITIVE_RE = re.compile(
    r'(?i)([?&#])((?:[a-zA-Z0-9]+[-_])*(?:token|secret|password|passwd|key|apikey|credential|auth|bearer|sig|signature|cert|certificate|private|passphrase|code|session|pat|pwd)(?:[-_][a-zA-Z0-9]*)?=)[^&#\s]*'
)


def _is_token_shaped(s: str) -> bool:
    """Checks if a string matches high-precision token or secret shapes."""
    if not isinstance(s, str) or not s:
        return False
    s_clean = s.strip("'\"")
    if not s_clean:
        return False
    if s_clean.startswith("-"):
        return False
    if any(pat.fullmatch(s_clean) for pat in _TARGETED_TOKEN_PATTERNS):
        return True
    if _UUID_RE.fullmatch(s_clean):
        return False
    if len(s_clean) >= 24 and re.fullmatch(r"[0-9a-zA-Z_-]+", s_clean):
        parts = s_clean.split("-")
        if len(parts) > 1 and all(len(p) <= 15 for p in parts):
            return False
        return True
    return False


def _redact_single_url(url_str: str) -> str:
    if not isinstance(url_str, str):
        return url_str
    s = url_str.strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9+.\-]*://\S+", s):
        return redact_secretish(url_str)

    lead_ws = url_str[:len(url_str) - len(url_str.lstrip())]
    trail_ws = url_str[len(url_str.rstrip()):]

    try:
        m_proto = re.match(r'^([A-Za-z][A-Za-z0-9+.\-]*://)(\S+)(.*)$', s)
        if m_proto:
            scheme_prefix = m_proto.group(1)
            rest = m_proto.group(2)
            trailing = m_proto.group(3)

            # Find auth_end: first /, ?, or #
            m_delim = re.search(r"[/\\?#]", rest)
            auth_end = m_delim.start() if m_delim else len(rest)
            auth_part = rest[:auth_end]
            path_and_rest = rest[auth_end:]

            if "@" in auth_part:
                userinfo, host = auth_part.rsplit("@", 1)
                if ":" in userinfo:
                    u, pw = userinfo.split(":", 1)
                    masked_uinfo = _mask_userinfo(u, pw)
                else:
                    masked_uinfo = MASK_SECRET
                s = f"{scheme_prefix}{masked_uinfo}@{host}{path_and_rest}{trailing}"
            elif not rest.startswith("[") and ":" in auth_part:
                u_cand, p_cand = auth_part.split(":", 1)
                if not p_cand.isdigit():
                    m_at = re.search(r"@(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(?=[:/?#]|$)", rest)
                    if m_at:
                        uinfo = rest[:m_at.start()]
                        if ":" in uinfo:
                            u, pw = uinfo.split(":", 1)
                            masked_uinfo = _mask_userinfo(u, pw)
                        else:
                            masked_uinfo = MASK_SECRET
                        s = f"{scheme_prefix}{masked_uinfo}@{rest[m_at.start()+1:]}{trailing}"

        parts = urllib.parse.urlsplit(s)
        if not parts.scheme:
            return redact_secretish(url_str)
        new_netloc = parts.netloc
        if "@" in parts.netloc:
            userinfo, host = parts.netloc.rsplit("@", 1)
            if ":" in userinfo:
                user, pw = userinfo.split(":", 1)
                new_userinfo = _mask_userinfo(user, pw)
            else:
                new_userinfo = MASK_SECRET
            new_netloc = f"{new_userinfo}@{host}"
        elif ":" in parts.netloc and not parts.netloc.startswith("["):
            user_or_host, pw_or_port = parts.netloc.rsplit(":", 1)
            if not pw_or_port.isdigit():
                new_netloc = _mask_userinfo(user_or_host, pw_or_port)

        new_path_segments = []
        for seg in parts.path.split("/"):
            unquoted = urllib.parse.unquote(seg)
            if seg and (_is_token_shaped(seg) or _is_token_shaped(unquoted) or bool(_TOKEN_SEARCH_RE.search(unquoted))):
                new_path_segments.append(MASK_SECRET)
            else:
                new_path_segments.append(seg)
        new_path = "/".join(new_path_segments)

        new_query = parts.query
        if parts.query:
            raw_pairs = parts.query.split("&")
            new_pairs = []
            for item in raw_pairs:
                if "=" in item:
                    k_raw, v_raw = item.partition("=")[0], item.partition("=")[2]
                    k_dec = urllib.parse.unquote(k_raw)
                    v_dec = urllib.parse.unquote(v_raw)
                    is_sens = (
                        _is_sensitive_param_name(k_dec)
                        or _is_token_shaped(v_raw)
                        or _is_token_shaped(v_dec)
                        or bool(_TOKEN_SEARCH_RE.search(v_dec))
                    )
                    if not is_sens and "://" in v_dec:
                        is_sens = (_redact_single_url(v_dec) != v_dec)
                    if is_sens:
                        new_pairs.append(f"{k_raw}={MASK_SECRET}")
                    else:
                        new_pairs.append(item)
                else:
                    k_dec = urllib.parse.unquote(item)
                    if _is_sensitive_param_name(k_dec) or _is_token_shaped(item) or _is_token_shaped(k_dec) or bool(_TOKEN_SEARCH_RE.search(k_dec)):
                        new_pairs.append(MASK_SECRET)
                    else:
                        new_pairs.append(item)
            new_query = "&".join(new_pairs)

        new_fragment = parts.fragment
        if parts.fragment:
            if "=" in parts.fragment:
                raw_pairs = parts.fragment.split("&")
                new_pairs = []
                for item in raw_pairs:
                    if "=" in item:
                        k_raw, v_raw = item.partition("=")[0], item.partition("=")[2]
                        k_dec = urllib.parse.unquote(k_raw)
                        v_dec = urllib.parse.unquote(v_raw)
                        is_sens = (
                            _is_sensitive_param_name(k_dec)
                            or _is_token_shaped(v_raw)
                            or _is_token_shaped(v_dec)
                            or bool(_TOKEN_SEARCH_RE.search(v_dec))
                        )
                        if not is_sens and "://" in v_dec:
                            is_sens = (_redact_single_url(v_dec) != v_dec)
                        if is_sens:
                            new_pairs.append(f"{k_raw}={MASK_SECRET}")
                        else:
                            new_pairs.append(item)
                    else:
                        k_dec = urllib.parse.unquote(item)
                        if _is_sensitive_param_name(k_dec) or _is_token_shaped(item) or _is_token_shaped(k_dec) or bool(_TOKEN_SEARCH_RE.search(k_dec)):
                            new_pairs.append(MASK_SECRET)
                        else:
                            new_pairs.append(item)
                new_fragment = "&".join(new_pairs)
            else:
                frag_unquoted = urllib.parse.unquote(parts.fragment)
                if (
                    _is_sensitive_param_name(parts.fragment)
                    or _is_token_shaped(parts.fragment)
                    or _is_token_shaped(frag_unquoted)
                    or bool(_TOKEN_SEARCH_RE.search(frag_unquoted))
                ):
                    new_fragment = MASK_SECRET

        new_parts = parts._replace(netloc=new_netloc, path=new_path, query=new_query, fragment=new_fragment)
        res_url = urllib.parse.urlunsplit(new_parts)
        res_url = _URL_QUERY_SENSITIVE_RE.sub(rf'\1\2{MASK_SECRET}', res_url)
        return f"{lead_ws}{res_url}{trail_ws}"
    except Exception:
        m_proto = re.match(r"^([A-Za-z][A-Za-z0-9+.\-]*://)", s)
        prefix = m_proto.group(1) if m_proto else ""
        return f"{lead_ws}{prefix}{MASK_SECRET}{trail_ws}"


def redact_url_or_cmd(text: str) -> str:
    """Redacts credentials and sensitive query parameters in URLs and commands."""
    if not isinstance(text, str):
        return text
    url_pattern = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s\"'<>]+")
    return url_pattern.sub(lambda m: _redact_single_url(m.group(0)), text)


def _mask_malformed_json(s: str) -> str:
    """Masks sensitive fields in malformed JSON blobs failing json.loads."""
    masked = re.sub(
        r'(?i)"([^"]*(?:token|secret|password|passwd|pass|key|api[-_]?key|auth|bearer|credential|pat|private[-_]?key)[^"]*)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"',
        rf'"\1":"{MASK_SECRET}"',
        s
    )
    masked = re.sub(
        r'(?i)"([^"]*(?:token|secret|password|passwd|pass|key|api[-_]?key|auth|bearer|credential|pat|private[-_]?key)[^"]*)"\s*:\s*([^,}\]\s]+)',
        rf'"\1":{MASK_SECRET}',
        masked
    )
    masked = re.sub(
        r'(?i)"([^"]*(?:token|secret|password|passwd|pass|key|api[-_]?key|auth|bearer|credential|pat|private[-_]?key)[^"]*)"\s*:\s*("[^"]*)$',
        rf'"\1":"{MASK_SECRET}"',
        masked
    )
    tokens = re.findall(r'[A-Za-z0-9_-]+', s.lower())
    if any(t in _SENSITIVE_WORDS_EXACT or _is_sensitive_param_name(t) for t in tokens):
        return MASK_SECRET
    return masked


def redact_args(args: List[Any]) -> List[str]:
    """Redacts command arguments while preserving argument list structure."""
    if not isinstance(args, list):
        return []
    redacted = []
    prev_is_flag = False
    prev_user_flag: Optional[str] = None
    prev_is_header = False
    prev_is_env = False
    json_accum: List[str] = []
    json_flag_prefix: str = ""
    for a in args:
        a_str = str(a)
        if json_accum:
            json_accum.append(a_str)
            if a_str.strip().endswith("}") or a_str.strip().endswith("]"):
                blob = " ".join(json_accum)
                pref = json_flag_prefix
                json_accum = []
                json_flag_prefix = ""
                try:
                    p = json.loads(blob)
                    redacted.append(f"{pref}{json.dumps(redact_server_dict(p), separators=(',', ':'), ensure_ascii=False)}")
                except Exception:
                    redacted.append(f"{pref}{_mask_malformed_json(blob)}")
            continue
        if prev_user_flag is not None:
            flag = prev_user_flag
            if a_str in _STRUCTURAL_FLAGS or a_str.startswith("--"):
                prev_user_flag = None
                # Do NOT consume a_str as a value! Fall through!
            elif a_str.startswith("-"):
                prev_user_flag = None
                # pass - do not consume flag as user value
            elif ":" in a_str:
                prev_user_flag = None
                u, pw = a_str.split(":", 1)
                redacted.append(_mask_userinfo(u, pw))
                continue
            elif flag in ("--user", "--proxy-user") or _is_token_shaped(a_str) or _TOKEN_SEARCH_RE.search(a_str):
                prev_user_flag = None
                redacted.append(MASK_SECRET)
                continue
            else:
                prev_user_flag = None
                redacted.append(a_str)
                continue

        if prev_is_flag:
            if a_str in _STRUCTURAL_FLAGS or a_str.startswith("--"):
                prev_is_flag = False
                # Do NOT consume a_str as a value! Fall through!
            else:
                prev_is_flag = False
                redacted.append(MASK_SECRET)
                continue

        if prev_is_header:
            if a_str in _STRUCTURAL_FLAGS or a_str.startswith("--"):
                prev_is_header = False
                # Do NOT consume a_str as a value! Fall through!
            else:
                s_strip = a_str.strip()
                if s_strip.startswith("{") or s_strip.startswith("["):
                    prev_is_header = False
                    try:
                        p_json = json.loads(s_strip)
                        r_json = redact_server_dict(p_json)
                        redacted.append(json.dumps(r_json, separators=(',', ':'), ensure_ascii=False))
                        continue
                    except Exception:
                        redacted.append(_mask_malformed_json(s_strip))
                        continue
                if s_strip.endswith(":") and not (s_strip.startswith("{") or s_strip.startswith("[")):
                    redacted.append(a_str)
                    prev_is_header = True
                    continue
                prev_is_header = False
                if ":" in a_str or "=" in a_str:
                    redacted.append(_redact_header_str(a_str))
                else:
                    redacted.append(MASK_SECRET)
                continue

        if prev_is_env:
            if a_str in _STRUCTURAL_FLAGS or a_str.startswith("--"):
                prev_is_env = False
                # Do NOT consume a_str as a value! Fall through!
            elif "=" in a_str:
                prev_is_env = False
                k, _ = a_str.split("=", 1)
                redacted.append(f"{k}={MASK_SECRET}")
                continue
            else:
                prev_is_env = False
                redacted.append(redact_url_or_cmd(a_str))
                continue

        # Check attached -H or --header= (must execute BEFORE inline = check)
        if (a_str.startswith("-H") and len(a_str) > 2 and not a_str.startswith("-H=")) or a_str.startswith(("-H=", "--header=", "-header=")):
            if a_str.startswith("-H="):
                prefix = "-H="
                val = a_str[3:]
            elif a_str.startswith("--header="):
                prefix = "--header="
                val = a_str[len("--header="):]
            elif a_str.startswith("-header="):
                prefix = "-header="
                val = a_str[len("-header="):]
            elif a_str.startswith(("-H ", "-H\t")):
                prefix = a_str[:3]
                val = a_str[3:].strip()
            else:
                prefix = "-H"
                val = a_str[2:]

            if val.startswith(('"', "'")):
                q = val[0]
                if len(val) >= 2 and val.endswith(q):
                    val_body = val[1:-1]
                    trail_q = q
                else:
                    val_body = val[1:]
                    trail_q = ""
            else:
                q = ""
                trail_q = ""
                val_body = val

            redacted.append(f"{prefix}{q}{_redact_header_str(val_body)}{trail_q}")
            prev_is_flag = False
            continue

        # Check attached -u (must execute BEFORE inline = check)
        if a_str.startswith("-u") and not a_str.startswith("-u=") and len(a_str) > 2:
            if a_str.startswith(("-u ", "-u\t")):
                prefix = a_str[:3]
                val = a_str[3:].strip()
            else:
                prefix = "-u"
                val = a_str[2:]

            starts_with_q = val.startswith(('"', "'"))
            has_colon = ":" in val
            is_token = _is_token_shaped(val) or bool(_TOKEN_SEARCH_RE.search(val)) or (len(val) >= 20 and not val.startswith("-"))

            if starts_with_q or has_colon or is_token:
                if starts_with_q:
                    q = val[0]
                    if len(val) >= 2 and val.endswith(q):
                        val_body = val[1:-1]
                        trail_q = q
                    else:
                        val_body = val[1:]
                        trail_q = ""
                else:
                    q = ""
                    trail_q = ""
                    val_body = val

                if ":" in val_body:
                    u_part, pw_part = val_body.split(":", 1)
                    redacted.append(f"{prefix}{q}{_mask_userinfo(u_part, pw_part)}{trail_q}")
                elif _is_token_shaped(val_body) or _TOKEN_SEARCH_RE.search(val_body) or len(val_body) >= 20:
                    redacted.append(f"{prefix}{q}{MASK_SECRET}{trail_q}")
                else:
                    redacted.append(f"{prefix}{q}{MASK_SECRET}{trail_q}")
                prev_is_flag = False
                continue

        # Check flag with = (e.g. -u=user:pass, --token=..., --password=..., --header=...)
        # Inline "=" check: only split on "=" if _STANDALONE_FLAG_RE.fullmatch(flag)
        if (a_str.startswith("--") or a_str.startswith("-")) and "=" in a_str:
            flag, val = a_str.split("=", 1)
            if _STANDALONE_FLAG_RE.fullmatch(flag) and "://" not in flag:
                if flag in ("-u", "--user", "-U", "--proxy-user"):
                    if ":" in val:
                        u, pw = val.split(":", 1)
                        redacted.append(f"{flag}={_mask_userinfo(u, pw)}")
                    else:
                        redacted.append(f"{flag}={MASK_SECRET}")
                    prev_is_flag = False
                    continue
                if _is_sensitive_flag(flag):
                    redacted.append(f"{flag}={MASK_SECRET}")
                    prev_is_flag = False  # DO NOT treat next argument as value!
                    continue
                if flag in ("--header", "-H", "-header") or (flag.startswith("-") and (flag.endswith("header") or flag.endswith("headers"))):
                    redacted.append(f"{flag}={_redact_header_str(val)}")
                    prev_is_flag = False
                    continue
                if flag in ("-e", "--env", "-env"):
                    if "=" in val:
                        sub_k, _ = val.split("=", 1)
                        redacted.append(f"{flag}={sub_k}={MASK_SECRET}")
                    else:
                        redacted.append(f"{flag}={val}")
                    prev_is_flag = False
                    continue
                val_stripped = val.strip()
                if (val_stripped.startswith("{") and not val_stripped.endswith("}")) or (val_stripped.startswith("[") and not val_stripped.endswith("]")):
                    json_accum = [val]
                    json_flag_prefix = f"{flag}="
                    prev_is_flag = False
                    continue
                if val_stripped.startswith("{") or val_stripped.startswith("["):
                    try:
                        parsed_json = json.loads(val_stripped)
                        redacted_json = redact_server_dict(parsed_json)
                        redacted.append(f"{flag}={json.dumps(redacted_json, separators=(',', ':'), ensure_ascii=False)}")
                        prev_is_flag = False
                        continue
                    except Exception:
                        redacted.append(f"{flag}={_mask_malformed_json(val_stripped)}")
                        prev_is_flag = False
                        continue

                if "://" in val:
                    redacted.append(f"{flag}={_redact_single_url(val)}")
                    prev_is_flag = False
                    continue
                if "=" in val:
                    sub_k, sub_val = val.split("=", 1)
                    if _is_sensitive_param_name(sub_k):
                        redacted.append(f"{flag}={sub_k}={MASK_SECRET}")
                        prev_is_flag = False
                        continue
                    else:
                        redacted.append(f"{flag}={sub_k}={_redact_single_url(sub_val)}")
                        prev_is_flag = False
                        continue
                if _is_token_shaped(val) or _TOKEN_SEARCH_RE.search(val):
                    redacted.append(f"{flag}={MASK_SECRET}")
                    prev_is_flag = False
                    continue
                redacted.append(f"{flag}={redact_secretish(val)}")
                prev_is_flag = False
                continue

        if a_str in ("-u", "--user", "-U", "--proxy-user"):
            redacted.append(a_str)
            prev_user_flag = a_str
            continue

        if a_str == "-H" or (a_str.startswith("-") and (a_str.endswith("header") or a_str.endswith("headers"))):
            redacted.append(a_str)
            prev_is_header = True
            continue

        if a_str in ("-e", "--env", "-env"):
            redacted.append(a_str)
            prev_is_env = True
            continue

        # Only treat a token as standalone sensitive flag if it matches _STANDALONE_FLAG_RE
        # Anything containing ':', whitespace, '/', '@', or quotes must NEVER set prev_is_flag = True!
        if _STANDALONE_FLAG_RE.match(a_str) and _is_sensitive_flag(a_str):
            redacted.append(a_str)
            prev_is_flag = True
            continue

        # In generic fall-through, if re.match(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+:\s", a_str): route through _redact_header_str(a_str)
        if re.match(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+:\s", a_str):
            redacted.append(_redact_header_str(a_str))
            continue

        # Header argument without preceding -H
        if a_str.lower().startswith("authorization:"):
            redacted.append(_redact_header_str(a_str))
            continue

        # JSON blob argument (e.g. {"token": "SECRET"} or [{"key": "..."}])
        stripped_a = a_str.strip()
        if (stripped_a.startswith("{") and not stripped_a.endswith("}")) or (stripped_a.startswith("[") and not stripped_a.endswith("]")):
            json_accum = [a_str]
            json_flag_prefix = ""
            continue
        if stripped_a.startswith("{") or stripped_a.startswith("["):
            try:
                parsed_json = json.loads(stripped_a)
                redacted_json = redact_server_dict(parsed_json)
                redacted.append(json.dumps(redacted_json, separators=(',', ':'), ensure_ascii=False))
                continue
            except Exception:
                redacted.append(_mask_malformed_json(a_str))
                continue

        # Check key=value assignment without leading -
        if "=" in a_str and not a_str.startswith("-"):
            k, val = a_str.split("=", 1)
            if "://" not in k:
                if _is_sensitive_param_name(k):
                    redacted.append(f"{k}={MASK_SECRET}")
                    continue
                else:
                    redacted.append(f"{k}={_redact_single_url(val)}")
                    continue

        # Check URL or general command string
        if "://" in a_str:
            redacted.append(redact_url_or_cmd(a_str))
            continue

        redacted.append(redact_url_or_cmd(a_str))

    if json_accum:
        blob = " ".join(json_accum)
        pref = json_flag_prefix
        try:
            p = json.loads(blob)
            redacted.append(f"{pref}{json.dumps(redact_server_dict(p), separators=(',', ':'), ensure_ascii=False)}")
        except Exception:
            redacted.append(f"{pref}{_mask_malformed_json(blob)}")

    return [redact_secretish(x) for x in redacted]


def redact_secretish(text: str, _recurse_url: bool = True) -> str:
    """Masks token-shaped and secret-shaped CLI flags and key=value pairs in command strings."""
    if not isinstance(text, str):
        return text
    if _is_token_shaped(text.strip()):
        return MASK_SECRET

    # Mask -u "user:pass" or --user "user:pass" or -u 'user:pass' (quote-aware)
    _QUOTED_USER_RE = re.compile(
        r'''(?i)(^|\s)(-u|--user|-U|--proxy-user)(\s+|=)?(?:"((?:[^"\\]|\\.)*)"|'([^']*)')'''
    )

    def _sub_quoted_user(m):
        pre, flag, sep = m.group(1), m.group(2), m.group(3) or ""
        dq = m.group(4) is not None
        body, q = (m.group(4), '"') if dq else (m.group(5), "'")
        if ":" in body:
            u, pw = body.split(":", 1)
            masked = _mask_userinfo(u, pw)
        elif flag.lower() in ("--user", "--proxy-user") or _is_token_shaped(body) or _TOKEN_SEARCH_RE.search(body):
            masked = MASK_SECRET
        else:
            return m.group(0)
        return f"{pre}{flag}{sep}{q}{masked}{q}"

    text = _QUOTED_USER_RE.sub(_sub_quoted_user, text)

    unclosed = _find_unclosed_quote(text)
    if unclosed is not None:
        idx, q = unclosed
        head = text[:idx]
        tail = text[idx + 1:]
        m_u = re.search(r'(?i)(^|\s)(-u|--user|-U|--proxy-user)(\s+|=)?$', head)
        if m_u:
            pre, flag, sep = m_u.group(1), m_u.group(2), m_u.group(3) or ""
            if ":" in tail:
                u, pw = tail.split(":", 1)
                masked = _mask_userinfo(u, pw)
            else:
                masked = MASK_SECRET
            text = f"{head[:m_u.start()]}{pre}{flag}{sep}{q}{masked}"

    # Mask unquoted -u user:pass or --user user:pass (allowing empty password with *)
    def _sub_unquoted_user(m):
        prefix, flag, sep, u, pw = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        return f"{prefix}{flag}{sep}{_mask_userinfo(u, pw)}"

    text = re.sub(
        r'(?i)(^|\s)(-u|--user|-U|--proxy-user)(\s+|=)([^\s:"\']+):([^\s"\'<>]*)',
        _sub_unquoted_user,
        text
    )

    # Mask attached unquoted -uuser:pass (allowing empty password with *)
    def _sub_attached_unquoted_user(m):
        prefix, u, pw = m.group(1), m.group(2), m.group(3)
        return f"{prefix}-u{_mask_userinfo(u, pw)}"

    text = re.sub(
        r'(?i)(^|\s)-u([^\s:=][^\s:"\']*):([^\s"\'<>]*)',
        _sub_attached_unquoted_user,
        text
    )

    # Mask --user TOKEN (without colon)
    text = re.sub(
        r'(?i)(^|\s)(--user|--proxy-user)(\s+|=)(?!-)([^\s"\'<>:]+)(?=\s|$)',
        rf"\g<1>\g<2>\g<3>{MASK_SECRET}",
        text
    )
    # Mask -u=TOKEN
    text = re.sub(
        r'(?i)(^|\s)-u="?([^\s"\'<>:]+)"?(?=\s|$)',
        rf'\g<1>-u={MASK_SECRET}',
        text
    )
    # Mask -u TOKEN when token-shaped
    token_pattern = (
        r'(?:sk-[a-zA-Z0-9_-]{8,}'
        r'|(?:ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{15,}'
        r'|xox[baprs]-[0-9a-zA-Z-]{20,}'
        r'|bot[0-9]+:[a-zA-Z0-9_-]+'
        r'|eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+'
        r'|AKIA[0-9A-Z]{16}'
        r'|[0-9a-zA-Z_-]{24,})'
    )
    text = re.sub(
        rf'(?i)(^|\s)-u(\s+)("?{token_pattern}"?)(?=\s|$)',
        rf'\g<1>-u\g<2>{MASK_SECRET}',
        text
    )
    text = re.sub(
        rf'(?i)(^|\s)-u({token_pattern})(?=\s|$)',
        rf'\g<1>-u{MASK_SECRET}',
        text
    )
    def _sub_flag_arg(m):
        pre, flag = m.group(1), m.group(2)
        if m.group(3) is not None:
            sep, val = m.group(3), m.group(4)
        else:
            sep, val = m.group(5), m.group(6)
        if _is_sensitive_flag(flag):
            q = val[0] if val.startswith(('"', "'")) and val.endswith(('"', "'")) and len(val) >= 2 else ""
            return f"{pre}{flag}{sep}{q}{MASK_SECRET}{q}" if q else f"{pre}{flag}{sep}{MASK_SECRET}"
        if val.startswith(('"', "'")) and val.endswith(('"', "'")) and len(val) >= 2:
            q = val[0]
            inner = val[1:-1]
            return f"{pre}{flag}{sep}{q}{redact_secretish(inner)}{q}"
        return m.group(0)

    text = FLAG_ARG_RE.sub(_sub_flag_arg, text)

    def _sub_header_flag(m):
        pre = m.group(1)
        flag = m.group(2)
        if m.group(3) is not None:
            sep = m.group(3)
            q = m.group(4) or ""
            val = m.group(5) if m.group(4) else (m.group(6) or m.group(7))
            closing = q if (q and m.group(0).endswith(q)) else ""
            return f"{pre}{flag}{sep}{q}{_redact_header_str(val)}{closing}"
        elif m.group(8) is not None:
            q = m.group(8)
            val = m.group(9)
            closing = q if m.group(0).endswith(q) else ""
            return f"{pre}{flag}{q}{_redact_header_str(val)}{closing}"
        else:
            val = m.group(10)
            return f"{pre}{flag}{_redact_header_str(val)}"

    HEADER_FLAG_RE = re.compile(
        r'(?i)(^|[\s"\'\(])(-H|--header|-header|--(?:[A-Za-z0-9_-]+-)?headers?)'
        r'(?:'
        r'(=|\s+)(?:(["\'])((?:[^\\]|\\.)*?)(?:\4|$)|([A-Za-z0-9!#$%&*+\-.^_`|~]+:\s*(?:[^"\'\n]*?(?=\s+--?[A-Za-z0-9]|["\'\)]|$)|[^\s"\'<>]*))|(\S+))'
        r'|(["\'])((?:[^\\]|\\.)*?)(?:\8|$)'
        r'|([A-Za-z0-9!#$%&*+\-.^_`|~]+:\s*(?:[^"\'\n]*?(?=\s+--?[A-Za-z0-9]|["\'\)]|$)|[^\s"\'<>]*))'
        r')'
    )
    text = HEADER_FLAG_RE.sub(_sub_header_flag, text)
    text = re.sub(r'(?i)\b(Bearer|Basic)\s+[^\s"\',;]+', rf'\1 {MASK_SECRET}', text)
    text = re.sub(
        r"(?i)\b(Authorization:\s*(?:Bearer|Basic|Token)\s+)[^\s\"']+",
        rf"\g<1>{MASK_SECRET}",
        text
    )
    text = re.sub(
        r"(?i)\b([A-Za-z0-9_-]{0,128}(?:TOKEN|SECRET|PASSWORD|PASSWD|KEY|API[-_]?KEY|AUTH|BEARER|CREDENTIAL|PRIVATE[-_]?KEY|SIG|SIGNATURE|[-_]PASS|[-_]PAT)[A-Za-z0-9_-]{0,128})=([^\s\"';&]+|'[^']*'|\"[^\"]*\")",
        rf"\g<1>={MASK_SECRET}",
        text
    )

    def _sub_inline_json_blob(m):
        blob = m.group(0)
        try:
            parsed = json.loads(blob)
            return json.dumps(redact_server_dict(parsed), separators=(',', ':'), ensure_ascii=False)
        except Exception:
            return _mask_malformed_json(blob)

    text = re.sub(r'\{[^{}\n]{2,}\}', _sub_inline_json_blob, text)
    if _recurse_url:
        return _TOKEN_SEARCH_RE.sub(MASK_SECRET, redact_url_or_cmd(text))
    return _TOKEN_SEARCH_RE.sub(MASK_SECRET, text)


def _format_masked_cmd_or_url(server: Union["McpServerModel", str]) -> str:
    """Formats and masks command or URL for dashboard and CLI table display."""
    if isinstance(server, str):
        cmd = server.strip()
        if not cmd:
            return "<empty>"
        try:
            tokens = shlex.split(cmd)
            if tokens:
                redacted = redact_args(tokens)
                return sanitize_display(redact_secretish(shlex.join(redacted)))
        except (ValueError, Exception):
            return sanitize_display(_mask_edit_buffer_args(cmd))
        return sanitize_display(redact_secretish(cmd))

    if server.transport == "stdio":
        cmd_tokens = [server.command]
        try:
            split_cmd = shlex.split(server.command)
            if len(split_cmd) > 1:
                cmd_tokens = redact_args(split_cmd)
        except Exception:
            pass
        parts = [" ".join(cmd_tokens)] + redact_args(server.args)
        cmd_val = " ".join(p for p in parts if p).strip()
    else:
        cmd_val = _redact_single_url(server.server_url)
    return sanitize_display(redact_secretish(cmd_val))


def redact_server_dict(data: Any, parent_is_sensitive: bool = False) -> Any:
    """Creates a deep copy of dictionary or structure with secrets masked for display/export."""
    if isinstance(data, dict):
        d = {}
        for k, v in data.items():
            k_str = str(k)
            is_sens = parent_is_sensitive or (_is_sensitive_param_name(k_str) and k_str not in _ALLOWLISTED_KEYS)
            if is_sens:
                if isinstance(v, dict):
                    d[k] = redact_server_dict(v, parent_is_sensitive=True)
                elif isinstance(v, list):
                    d[k] = redact_server_dict(v, parent_is_sensitive=True)
                else:
                    d[k] = MASK_SECRET
            elif k_str == "env" and isinstance(v, dict):
                d[k] = {ek: MASK_SECRET for ek in v}
            elif "header" in k_str.lower() and isinstance(v, dict):
                d[k] = {
                    hk: (MASK_SECRET if (hk.lower() not in _PUBLIC_HEADERS_ALLOWLIST or _is_sensitive_param_name(hk)) else hv)
                    for hk, hv in v.items()
                }
            elif "header" in k_str.lower() and isinstance(v, list):
                d[k] = [_redact_header_str(x) if isinstance(x, str) else redact_server_dict(x, parent_is_sensitive=False) for x in v]
            elif k_str in ("serverUrl", "url") and isinstance(v, str):
                d[k] = _redact_single_url(v)
            elif k_str == "args" and isinstance(v, list):
                d[k] = redact_args(v)
            elif k_str == "command" and isinstance(v, str):
                d[k] = redact_secretish(v)
            else:
                d[k] = redact_server_dict(v, parent_is_sensitive=False)
        return d
    elif isinstance(data, list):
        if parent_is_sensitive:
            return [
                redact_server_dict(x, parent_is_sensitive=True) if isinstance(x, (dict, list))
                else MASK_SECRET
                for x in data
            ]
        return [redact_server_dict(x, parent_is_sensitive=False) for x in data]
    elif isinstance(data, str):
        if parent_is_sensitive:
            return MASK_SECRET
        if _is_token_shaped(data.strip()):
            return MASK_SECRET
        if ":" in data and "://" not in data:
            colon_idx = data.find(":")
            hdr_name = data[:colon_idx].strip()
            if _RFC9110_HEADER_NAME_RE.fullmatch(hdr_name) and (hdr_name.lower() in ("authorization", "proxy-authorization", "x-api-key") or _is_sensitive_param_name(hdr_name)):
                return _redact_header_str(data)
        return redact_secretish(data)
    else:
        if parent_is_sensitive:
            return MASK_SECRET
        return data



# ==============================================================================
# 1. Data Models
# ==============================================================================

_EDITABLE_FIELDS = (
    "transport", "command", "args", "env", "server_url", "headers",
    "auth_provider", "oauth_client_id", "oauth_client_secret",
    "disabled", "timeout_seconds", "eager", "background",
    "disabled_tools"
)
_ALL_CLIENT_ID_KEYS = ("clientId", "client_id", "oauthClientId", "oauth_client_id")
_ALL_CLIENT_SECRET_KEYS = ("clientSecret", "client_secret", "oauthClientSecret", "oauth_client_secret")
_CLIENT_ID_KEYS = _ALL_CLIENT_ID_KEYS
_CLIENT_SECRET_KEYS = _ALL_CLIENT_SECRET_KEYS


def _strip_client_creds(container: Dict[str, Any]) -> None:
    """Removes client ID and client secret keys from a dictionary."""
    if not isinstance(container, dict):
        return
    for k in _ALL_CLIENT_ID_KEYS + _ALL_CLIENT_SECRET_KEYS:
        container.pop(k, None)


def _purge_credentials(res: Dict[str, Any]) -> None:
    """
    Purges client credentials from oauth, authConfig, and root,
    preserving non-credential keys (like scopes, redirectUri).
    Drops oauth/authConfig only if they become completely empty.
    """
    if not isinstance(res, dict):
        return
    _strip_client_creds(res)
    if isinstance(res.get("oauth"), dict):
        _strip_client_creds(res["oauth"])
        if not res["oauth"]:
            res.pop("oauth", None)
    if isinstance(res.get("authConfig"), dict):
        _strip_client_creds(res["authConfig"])
        if not res["authConfig"]:
            res.pop("authConfig", None)


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
    disabled: bool = False
    disabled_tools: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)
    explicit_timeout: bool = False
    explicit_transport: bool = False
    raw_dict: Optional[Dict[str, Any]] = field(default=None, repr=False, compare=False)
    _snapshot: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    _orig_transport: Optional[str] = field(default=None, repr=False, compare=False)
    _orig_command: Optional[str] = field(default=None, repr=False, compare=False)
    _orig_args: Optional[List[str]] = field(default=None, repr=False, compare=False)
    _orig_env: Optional[Dict[str, str]] = field(default=None, repr=False, compare=False)
    _orig_server_url: Optional[str] = field(default=None, repr=False, compare=False)
    _orig_headers: Optional[Dict[str, str]] = field(default=None, repr=False, compare=False)
    _orig_auth_provider: Optional[str] = field(default=None, repr=False, compare=False)
    _orig_oauth_client_id: Optional[str] = field(default=None, repr=False, compare=False)
    _orig_oauth_client_secret: Optional[str] = field(default=None, repr=False, compare=False)
    _orig_disabled: Optional[bool] = field(default=None, repr=False, compare=False)
    _orig_timeout_seconds: Optional[int] = field(default=None, repr=False, compare=False)
    _orig_eager: Optional[bool] = field(default=None, repr=False, compare=False)
    _orig_background: Optional[str] = field(default=None, repr=False, compare=False)
    _orig_disabled_tools: Optional[List[str]] = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self.timeout_seconds != 60:
            self.explicit_timeout = True
        if not self._snapshot:
            self.take_snapshot()

    @property
    def timeout(self) -> int:
        return self.timeout_seconds

    @timeout.setter
    def timeout(self, val: Any):
        try:
            self.timeout_seconds = int(val)
        except (ValueError, TypeError):
            self.timeout_seconds = 60

    @property
    def url(self) -> str:
        return self.server_url

    @url.setter
    def url(self, val: Any):
        self.server_url = str(val or "")

    def take_snapshot(self):
        self._snapshot = {
            f: copy.deepcopy(getattr(self, f))
            for f in _EDITABLE_FIELDS
            if hasattr(self, f)
        }
        for f in _EDITABLE_FIELDS:
            if hasattr(self, f):
                setattr(self, f"_orig_{f}", copy.deepcopy(getattr(self, f)))

    def has_changed(self, f: str) -> bool:
        if not hasattr(self, f):
            return False
        if f not in self._snapshot:
            return True
        return getattr(self, f) != self._snapshot[f]

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

        # OAuth credentials: check oauth dict first, then authConfig dict, then root level
        oauth_client_id = ""
        if isinstance(data.get("oauth"), dict):
            oauth_obj = data["oauth"]
            for k in ("clientId", "client_id", "oauthClientId", "oauth_client_id"):
                if oauth_obj.get(k):
                    oauth_client_id = oauth_obj[k]
                    break
        if not oauth_client_id and isinstance(data.get("authConfig"), dict):
            auth_cfg = data["authConfig"]
            for k in ("clientId", "oauthClientId", "client_id", "oauth_client_id"):
                if auth_cfg.get(k):
                    oauth_client_id = auth_cfg[k]
                    break
        if not oauth_client_id:
            for k in ("oauthClientId", "clientId", "client_id", "oauth_client_id"):
                if data.get(k):
                    oauth_client_id = data[k]
                    break

        oauth_client_secret = ""
        if isinstance(data.get("oauth"), dict):
            oauth_obj = data["oauth"]
            for k in ("clientSecret", "client_secret", "oauthClientSecret", "oauth_client_secret"):
                if oauth_obj.get(k):
                    oauth_client_secret = oauth_obj[k]
                    break
        if not oauth_client_secret and isinstance(data.get("authConfig"), dict):
            auth_cfg = data["authConfig"]
            for k in ("clientSecret", "oauthClientSecret", "client_secret", "oauth_client_secret"):
                if auth_cfg.get(k):
                    oauth_client_secret = auth_cfg[k]
                    break
        if not oauth_client_secret:
            for k in ("oauthClientSecret", "clientSecret", "client_secret", "oauth_client_secret"):
                if data.get(k):
                    oauth_client_secret = data[k]
                    break

        # Authentication provider: do NOT inject "oauth" simply because oauth or authConfig exists
        raw_auth = None
        for k in ("authProviderType", "auth_provider"):
            if k in data and data[k] is not None and str(data[k]).strip():
                raw_auth = data[k]
                break
        if raw_auth is not None and str(raw_auth).strip():
            auth_provider = str(raw_auth).strip().lower()
        else:
            auth_provider = "none"

        # Tool configuration (lazy loading & background execution)
        tool_config = data.get("toolConfig") if isinstance(data.get("toolConfig"), dict) else {}
        eager = tool_config.get("eager", data.get("eager", False))
        background = tool_config.get("background", data.get("background", "OFF"))
        if background not in ("OFF", "ALWAYS"):
            background = "OFF"

        # Timeout handling without magic sentinel loss (M9) - unified alias precedence
        explicit_timeout = any(k in data for k in ("timeoutSeconds", "timeout_seconds", "timeout"))
        raw_timeout = None
        for k in ("timeoutSeconds", "timeout_seconds", "timeout"):
            if k in data and data[k] is not None:
                raw_timeout = data[k]
                break
        if raw_timeout is None:
            raw_timeout = 60
        try:
            timeout_seconds = int(raw_timeout)
        except (ValueError, TypeError):
            timeout_seconds = 60

        disabled = bool(data.get("disabled", False))
        disabled_tools = cls._str_list(data.get("disabledTools", data.get("disabled_tools", [])))

        command = str(data.get("command", "") or "")
        args = cls._str_list(data.get("args"))
        env = cls._str_map(data.get("env"))
        headers = cls._header_map(data.get("headers"))

        model = cls(
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
            disabled=disabled,
            disabled_tools=disabled_tools,
            extra=extra,
            explicit_timeout=explicit_timeout,
            explicit_transport=explicit_transport,
            raw_dict=copy.deepcopy(data),
        )
        return model

    def to_dict_full(self) -> dict:
        result: Dict[str, Any] = {}
        if self.disabled:
            result["disabled"] = True

        if self.explicit_transport or self.transport not in ("stdio", "http"):
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

        if self.explicit_timeout or self.timeout_seconds != 60 or self.has_changed("timeout_seconds"):
            result["timeoutSeconds"] = self.timeout_seconds

        extra_copy = copy.deepcopy(self.extra)
        if self.auth_provider == "google_credentials":
            result["authProviderType"] = "google_credentials"
            _purge_credentials(extra_copy)
        elif self.auth_provider == "oauth":
            result["authProviderType"] = "oauth"
            _strip_client_creds(extra_copy)
            oauth_dict: Dict[str, Any] = {}
            if isinstance(extra_copy.get("oauth"), dict):
                oauth_dict = copy.deepcopy(extra_copy["oauth"])
                _strip_client_creds(oauth_dict)
            if self.oauth_client_id:
                oauth_dict["clientId"] = self.oauth_client_id
            if self.oauth_client_secret:
                oauth_dict["clientSecret"] = self.oauth_client_secret
            if oauth_dict:
                result["oauth"] = oauth_dict
            extra_copy.pop("oauth", None)
        elif self.auth_provider not in ("none", ""):
            result["authProviderType"] = self.auth_provider
            _purge_credentials(extra_copy)
        else:
            _purge_credentials(extra_copy)

        # AGY Tooling & Context settings: only emit when eager or background != 'OFF' (M9)
        tool_cfg_new: Dict[str, Any] = {}
        if self.eager:
            tool_cfg_new["eager"] = True
        if self.background != "OFF":
            tool_cfg_new["background"] = self.background

        if tool_cfg_new:
            result["toolConfig"] = tool_cfg_new

        if self.disabled_tools:
            result["disabledTools"] = list(dict.fromkeys(self.disabled_tools))

        # Merge unmodeled keys first, allowing modeled keys to take precedence (C1)
        return {**extra_copy, **result}

    def to_dict(self, base_dict: Optional[Dict[str, Any]] = None) -> dict:
        if self.raw_dict is None:
            return self.to_dict_full()
        source_dict = base_dict if base_dict is not None else self.raw_dict
        if source_dict is None or not isinstance(source_dict, dict) or not source_dict:
            return self.to_dict_full()

        # Check if completely unedited using snapshot
        is_unedited = not any(self.has_changed(f) for f in _EDITABLE_FIELDS)
        if is_unedited:
            return copy.deepcopy(source_dict)

        res = copy.deepcopy(source_dict)
        if self.raw_dict is not None or base_dict is not None:
            if self.has_changed("disabled"):
                if self.disabled:
                    res["disabled"] = True
                else:
                    res.pop("disabled", None)

            # Transport
            if self.has_changed("transport"):
                # User explicitly changed the transport
                res["transport"] = self.transport
                if self.transport == "stdio":
                    # Remove HTTP keys only
                    for k in ("serverUrl", "url", "headers"):
                        res.pop(k, None)
                    res["command"] = self.command
                    res["args"] = list(self.args)
                    if self.env:
                        res["env"] = dict(self.env)
                    else:
                        res.pop("env", None)
                elif self.transport == "http":
                    # Remove stdio keys only
                    for k in ("command", "args", "env"):
                        res.pop(k, None)
                    res["serverUrl"] = self.server_url
                    res.pop("url", None)
                    if self.headers:
                        res["headers"] = dict(self.headers)
                    else:
                        res.pop("headers", None)
            else:
                # Transport unchanged: only update fields that actually changed
                if self.transport == "stdio":
                    if self.has_changed("command"):
                        res["command"] = self.command
                    if self.has_changed("args"):
                        res["args"] = list(self.args)
                    if self.has_changed("env"):
                        if self.env:
                            res["env"] = dict(self.env)
                        else:
                            res.pop("env", None)
                else:
                    if self.has_changed("server_url"):
                        if "url" in res and "serverUrl" not in res:
                            res["url"] = self.server_url
                        else:
                            res["serverUrl"] = self.server_url
                            res.pop("url", None)
                    if self.has_changed("headers"):
                        if self.headers:
                            res["headers"] = dict(self.headers)
                        else:
                            res.pop("headers", None)

            # Timeout handling: write canonical timeoutSeconds and remove legacy aliases
            if self.has_changed("timeout_seconds"):
                res["timeoutSeconds"] = int(self.timeout_seconds)
                res.pop("timeout", None)
                res.pop("timeout_seconds", None)

            # Auth handling:
            # Invariant: An alias is removed only in the same step its value is written to the canonical key,
            # or when the field is being deliberately cleared.
            auth_provider_changed = self.has_changed("auth_provider")
            cred_changed = (
                self.has_changed("oauth_client_id")
                or self.has_changed("oauth_client_secret")
            )
            switching_away = auth_provider_changed and self.auth_provider in ("none", "google_credentials")

            if auth_provider_changed:
                if self.auth_provider == "oauth":
                    res["authProviderType"] = "oauth"
                    res.pop("auth_provider", None)
                elif self.auth_provider in ("none", "google_credentials"):
                    if self.auth_provider == "google_credentials":
                        res["authProviderType"] = "google_credentials"
                    else:
                        res.pop("authProviderType", None)
                    res.pop("auth_provider", None)
                else:
                    res["authProviderType"] = self.auth_provider
                    res.pop("auth_provider", None)
            elif cred_changed and self.auth_provider == "oauth":
                res["authProviderType"] = "oauth"
                res.pop("auth_provider", None)

            if not switching_away and (cred_changed or (auth_provider_changed and self.auth_provider == "oauth")):
                if self.has_changed("oauth_client_id"):
                    if self.oauth_client_id:
                        if not isinstance(res.get("oauth"), dict):
                            res["oauth"] = {}
                        res["oauth"]["clientId"] = self.oauth_client_id
                        for ak in _ALL_CLIENT_ID_KEYS:
                            if ak != "clientId":
                                res["oauth"].pop(ak, None)
                        if isinstance(res.get("authConfig"), dict):
                            res["authConfig"]["clientId"] = self.oauth_client_id
                            for ak in _ALL_CLIENT_ID_KEYS:
                                if ak != "clientId":
                                    res["authConfig"].pop(ak, None)
                        for ak in _ALL_CLIENT_ID_KEYS:
                            res.pop(ak, None)
                    else:
                        if isinstance(res.get("oauth"), dict):
                            for ak in _ALL_CLIENT_ID_KEYS:
                                res["oauth"].pop(ak, None)
                        if isinstance(res.get("authConfig"), dict):
                            for ak in _ALL_CLIENT_ID_KEYS:
                                res["authConfig"].pop(ak, None)
                        for ak in _ALL_CLIENT_ID_KEYS:
                            res.pop(ak, None)

                if self.has_changed("oauth_client_secret"):
                    if self.oauth_client_secret:
                        if not isinstance(res.get("oauth"), dict):
                            res["oauth"] = {}
                        res["oauth"]["clientSecret"] = self.oauth_client_secret
                        for ak in _ALL_CLIENT_SECRET_KEYS:
                            if ak != "clientSecret":
                                res["oauth"].pop(ak, None)
                        if isinstance(res.get("authConfig"), dict):
                            res["authConfig"]["clientSecret"] = self.oauth_client_secret
                            for ak in _ALL_CLIENT_SECRET_KEYS:
                                if ak != "clientSecret":
                                    res["authConfig"].pop(ak, None)
                        for ak in _ALL_CLIENT_SECRET_KEYS:
                            res.pop(ak, None)
                    else:
                        if isinstance(res.get("oauth"), dict):
                            for ak in _ALL_CLIENT_SECRET_KEYS:
                                res["oauth"].pop(ak, None)
                        if isinstance(res.get("authConfig"), dict):
                            for ak in _ALL_CLIENT_SECRET_KEYS:
                                res["authConfig"].pop(ak, None)
                        for ak in _ALL_CLIENT_SECRET_KEYS:
                            res.pop(ak, None)

                if isinstance(res.get("oauth"), dict) and not res["oauth"]:
                    res.pop("oauth", None)
                if isinstance(res.get("authConfig"), dict) and not res["authConfig"]:
                    res.pop("authConfig", None)

            if switching_away:
                _purge_credentials(res)


            # ToolConfig: independent merge of eager and background
            if self.has_changed("eager") or self.has_changed("background"):
                tool_cfg = res.get("toolConfig")
                if not isinstance(tool_cfg, dict):
                    need_cfg = (self.has_changed("eager") and self.eager) or (self.has_changed("background") and self.background != "OFF")
                    tool_cfg = {} if need_cfg else None
                else:
                    tool_cfg = copy.deepcopy(tool_cfg)

                if tool_cfg is not None:
                    if self.has_changed("eager"):
                        if self.eager:
                            tool_cfg["eager"] = True
                        elif "eager" in tool_cfg:
                            tool_cfg["eager"] = False
                    if self.has_changed("background"):
                        if self.background != "OFF":
                            tool_cfg["background"] = self.background
                        elif "background" in tool_cfg:
                            tool_cfg["background"] = "OFF"

                    if tool_cfg:
                        res["toolConfig"] = tool_cfg
                    else:
                        res.pop("toolConfig", None)

                if self.has_changed("eager"):
                    res.pop("eager", None)
                if self.has_changed("background"):
                    res.pop("background", None)

            if self.has_changed("disabled_tools"):
                if self.disabled_tools:
                    res["disabledTools"] = list(dict.fromkeys(self.disabled_tools))
                    res.pop("disabled_tools", None)
                else:
                    res.pop("disabledTools", None)
                    res.pop("disabled_tools", None)

            return res

        return self.to_dict_full()


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
        self.config_path = os.path.abspath(expanded)
        self.backup_path = self.config_path + ".bak"
        self.lock_path = self._compute_lock_path()
        self._lock_depth = 0
        self._lock_fd = None

    def _compute_lock_path(self) -> str:
        real_cfg = os.path.realpath(self.config_path)
        real_dir = os.path.dirname(real_cfg)
        self.lock_path = os.path.join(real_dir, f".{os.path.basename(real_cfg)}.lock")
        return self.lock_path

    @contextmanager
    def _config_lock(self, real_cfg: Optional[str] = None):
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        if self._lock_depth == 0:
            if real_cfg is None:
                real_cfg = os.path.realpath(self.config_path)
            real_dir = os.path.dirname(real_cfg)
            self.lock_path = os.path.join(real_dir, f".{os.path.basename(real_cfg)}.lock")
            if real_dir and not os.path.exists(real_dir):
                os.makedirs(real_dir, mode=0o700, exist_ok=True)
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

    def load_raw_json(self, path: Optional[str] = None) -> Dict[str, Any]:
        """Loads raw JSON config dict, refusing to swallow syntax errors (C2)."""
        cfg_p = path if path is not None else self.config_path
        if not os.path.exists(cfg_p):
            return {"mcpServers": {}}

        try:
            with open(cfg_p, "r", encoding="utf-8-sig") as f:
                content = f.read()
        except OSError as e:
            raise ConfigParseError(f"Cannot read config file: {e}", path=cfg_p)
        except (UnicodeDecodeError, ValueError) as e:
            raise ConfigParseError(f"Encoding error reading config file: {e}", path=cfg_p)

        if not content.strip():
            return {"mcpServers": {}}

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise ConfigParseError(f"Malformed JSON: {e.msg}", path=cfg_p, lineno=e.lineno, colno=e.colno)

        if not isinstance(data, dict):
            raise ConfigParseError(f"Root of config file must be a JSON object, got {type(data).__name__}", path=cfg_p)

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

    @staticmethod
    def _get_backup_dir() -> str:
        env_dir = os.environ.get("AGY_MCP_BACKUP_DIR")
        if env_dir:
            backup_dir = os.path.abspath(os.path.expanduser(env_dir))
        elif os.environ.get("XDG_STATE_HOME"):
            backup_dir = os.path.abspath(os.path.join(os.environ["XDG_STATE_HOME"], "agy", "mcp-backups"))
        else:
            backup_dir = os.path.abspath(os.path.expanduser("~/.local/state/agy/mcp-backups"))

        if not os.path.lexists(backup_dir):
            os.makedirs(backup_dir, mode=0o700, exist_ok=True)
            try:
                os.chmod(backup_dir, 0o700)
            except OSError:
                pass

        st = os.lstat(backup_dir)
        if stat.S_ISLNK(st.st_mode):
            raise OSError(f"Insecure backup directory: '{backup_dir}' cannot be a symlink")
        if not stat.S_ISDIR(st.st_mode):
            raise OSError(f"Insecure backup directory: '{backup_dir}' is not a directory")
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            raise OSError(f"Insecure backup directory: '{backup_dir}' is not owned by current user (UID {os.getuid()})")
        if (st.st_mode & 0o077) != 0:
            try:
                os.chmod(backup_dir, 0o700)
            except OSError as e:
                raise OSError(f"Insecure backup directory: '{backup_dir}' has insecure permissions {oct(st.st_mode)} and chmod 0700 failed: {e}")
            st = os.lstat(backup_dir)
            if (st.st_mode & 0o077) != 0:
                raise OSError(f"Insecure backup directory: '{backup_dir}' has insecure permissions {oct(st.st_mode)}")

        return backup_dir

    def _write_raw_atomic(self, raw: Dict[str, Any], target_path: Optional[str] = None) -> None:
        """Atomically persists raw dict with backups in isolated directory without symlink following into dotfiles."""
        if target_path is None:
            target_path = os.path.realpath(self.config_path)
        real_cfg = target_path
        target_parent = os.path.dirname(real_cfg)
        if target_parent and not os.path.exists(target_parent):
            os.makedirs(target_parent, mode=0o700, exist_ok=True)

        logical_parent = os.path.dirname(self.config_path)
        beside_config_opt_in = os.environ.get("AGY_MCP_BACKUP_BESIDE_CONFIG", "").strip() in ("1", "true", "yes")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        cfg_hash = hashlib.sha256(real_cfg.encode("utf-8")).hexdigest()[:16]
        base_name = os.path.basename(self.config_path)

        if os.path.exists(target_path):
            if beside_config_opt_in:
                backup_dir = logical_parent if logical_parent else "."
                if backup_dir and not os.path.exists(backup_dir):
                    os.makedirs(backup_dir, mode=0o700, exist_ok=True)
                backup_path = self.config_path + ".bak"
                ts_backup_path = f"{self.config_path}.bak.{ts}"
                prune_prefix = base_name + ".bak."
            else:
                backup_dir = self._get_backup_dir()
                backup_path = os.path.join(backup_dir, f"{base_name}.{cfg_hash}.bak")
                ts_backup_path = f"{backup_path}.{ts}"
                prune_prefix = f"{base_name}.{cfg_hash}.bak."
            self.backup_path = backup_path
        else:
            backup_dir = None
            backup_path = None
            ts_backup_path = None
            prune_prefix = None
            self.backup_path = None

        # Step 1: Create, write and fsync main temp file first
        fd, tmp_path = tempfile.mkstemp(dir=target_parent, prefix=".mcp_config.", suffix=".tmp")
        try:
            target_mode = 0o600
            if os.path.exists(target_path):
                try:
                    cur_mode = stat.S_IMODE(os.stat(target_path).st_mode)
                    target_mode = cur_mode & 0o600
                except OSError:
                    pass
            try:
                os.fchmod(fd, target_mode)
            except OSError:
                pass

            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(raw, f, indent=2, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())

            # Step 2: Write backups if target exists
            if os.path.exists(target_path):
                try:
                    with open(target_path, "rb") as f_in:
                        orig_bytes = f_in.read()

                    bak_matches = False
                    if os.path.exists(backup_path):
                        try:
                            fd_read = os.open(backup_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                            try:
                                with os.fdopen(fd_read, "rb") as fb_exist:
                                    bak_matches = (fb_exist.read() == orig_bytes)
                            except Exception:
                                pass
                        except OSError:
                            pass

                    if not bak_matches or not os.path.exists(backup_path):
                        # Safe atomic write to backup_path
                        fd_b, tmp_bak = tempfile.mkstemp(dir=backup_dir, prefix=".mcp_bak.", suffix=".tmp")
                        try:
                            os.fchmod(fd_b, 0o600)
                            with os.fdopen(fd_b, "wb") as fb:
                                fb.write(orig_bytes)
                                fb.flush()
                                os.fsync(fb.fileno())
                            os.replace(tmp_bak, backup_path)
                        finally:
                            if os.path.exists(tmp_bak):
                                try:
                                    os.remove(tmp_bak)
                                except OSError:
                                    pass

                        # Safe atomic write to timestamped backup
                        fd_ts, tmp_ts = tempfile.mkstemp(dir=backup_dir, prefix=".mcp_bak_ts.", suffix=".tmp")
                        try:
                            os.fchmod(fd_ts, 0o600)
                            with os.fdopen(fd_ts, "wb") as ft:
                                ft.write(orig_bytes)
                                ft.flush()
                                os.fsync(ft.fileno())
                            os.replace(tmp_ts, ts_backup_path)
                        finally:
                            if os.path.exists(tmp_ts):
                                try:
                                    os.remove(tmp_ts)
                                except OSError:
                                    pass

                    if backup_dir != target_parent:
                        try:
                            dirfd_bak = os.open(backup_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                            try:
                                os.fsync(dirfd_bak)
                            finally:
                                os.close(dirfd_bak)
                        except (OSError, AttributeError):
                            pass
                except Exception as e:
                    raise OSError(f"Backup failed: {e}. Aborting save to protect configuration.")

                # Prune older timestamped backups (keep latest 5)
                try:
                    bak_files = [
                        os.path.join(backup_dir, f)
                        for f in os.listdir(backup_dir)
                        if f.startswith(prune_prefix)
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

            # Step 3: Atomic replace of target config
            os.replace(tmp_path, target_path)

            # Step 4: fsync target directory
            try:
                dirfd = os.open(target_parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
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

    def transact(self, mutator: Callable[[Dict[str, Any]], Any]) -> Any:
        """Executes mutator under exclusive lock, loading raw JSON and saving atomically (C1)."""
        real_cfg = os.path.realpath(self.config_path)
        with self._config_lock(real_cfg):
            raw = self.load_raw_json(real_cfg)
            if "mcpServers" in raw:
                if raw["mcpServers"] is None:
                    raw["mcpServers"] = {}
                elif not isinstance(raw["mcpServers"], dict):
                    raise ConfigParseError(
                        f"'mcpServers' must be a JSON object, not {type(raw['mcpServers']).__name__}",
                        path=self.config_path
                    )
            else:
                raw["mcpServers"] = {}
            raw_before_snap = json.dumps(raw, sort_keys=True, ensure_ascii=False)
            res = mutator(raw)
            raw_after_snap = json.dumps(raw, sort_keys=True, ensure_ascii=False)
            if res is not False and raw_before_snap != raw_after_snap:
                self._write_raw_atomic(raw, target_path=real_cfg)
            return res

    def save_config(self, servers: Dict[str, McpServerModel]) -> None:
        """Atomically saves server configurations (C8, M1)."""
        def mutator(raw: dict):
            current_servers = raw.setdefault("mcpServers", {})
            for name in list(current_servers.keys()):
                if name not in servers:
                    del current_servers[name]
            for name, model in servers.items():
                fresh_entry = current_servers.get(name)
                base_dict = fresh_entry if isinstance(fresh_entry, dict) else None
                persisted = model.to_dict(base_dict=base_dict)
                current_servers[name] = persisted
                model.raw_dict = copy.deepcopy(persisted)
                model._snapshot = copy.deepcopy(model.raw_dict)
                model.take_snapshot()
        self.transact(mutator)

    def get_server(self, name: str) -> Optional[McpServerModel]:
        return self.load_config().get(name)

    def set_server(self, server: McpServerModel, overwrite: bool = False, is_new: Optional[bool] = None) -> None:
        def mutator(raw: dict):
            servers = raw.setdefault("mcpServers", {})
            actual_is_new = (server.raw_dict is None) if is_new is None else is_new
            if actual_is_new:
                if server.name in servers and not overwrite:
                    raise ValueError(f"Server '{server.name}' already exists.")
                base_dict = {}
            else:
                if server.name not in servers:
                    base_dict = {}
                else:
                    fresh_entry = servers[server.name]
                    base_dict = fresh_entry if isinstance(fresh_entry, dict) else {}
            persisted = server.to_dict(base_dict=base_dict)
            servers[server.name] = persisted
            server.raw_dict = copy.deepcopy(persisted)
            server._snapshot = copy.deepcopy(server.raw_dict)
            server.take_snapshot()
        self.transact(mutator)

    def rename_server(self, old_name: str, server: McpServerModel, overwrite: bool = False) -> None:
        """Atomically renames a server in a single transaction (H2)."""
        def mutator(raw: dict):
            servers = raw.setdefault("mcpServers", {})
            if old_name not in servers:
                raise KeyError(f"Server '{old_name}' not found.")
            if server.name != old_name and server.name in servers and not overwrite:
                raise ValueError(f"Server '{server.name}' already exists.")
            old_entry = servers.pop(old_name)
            fresh_entry = old_entry if isinstance(old_entry, dict) else None
            persisted = server.to_dict(base_dict=fresh_entry)
            servers[server.name] = persisted
            server.raw_dict = copy.deepcopy(persisted)
            server._snapshot = copy.deepcopy(server.raw_dict)
            server.take_snapshot()
        self.transact(mutator)

    def remove_server(self, name: str) -> bool:
        def mutator(raw: dict) -> bool:
            servers = raw.setdefault("mcpServers", {})
            if name in servers:
                del servers[name]
                return True
            return False
        return bool(self.transact(mutator))

    def enable_server(self, name: str) -> bool:
        def mutator(raw: dict) -> bool:
            servers = raw.setdefault("mcpServers", {})
            if name in servers:
                entry = servers[name]
                if isinstance(entry, dict):
                    entry.pop("disabled", None)
                    return True
            return False
        return bool(self.transact(mutator))

    def disable_server(self, name: str) -> bool:
        def mutator(raw: dict) -> bool:
            servers = raw.setdefault("mcpServers", {})
            if name in servers:
                entry = servers[name]
                if isinstance(entry, dict):
                    entry["disabled"] = True
                    return True
            return False
        return bool(self.transact(mutator))



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

        return sorted(tools, key=lambda t: str(t.name))

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
            PreflightChecker._check_command(server, diags)
        elif server.transport == "http":
            if server.command or server.args or server.env:
                diags.append(Diagnostic("WARNING", "HTTP transport is active, but stdio fields (command, args, or env) are populated and will be ignored.", field="transport"))
            PreflightChecker._check_http_url(server, diags)

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
    def _check_command(server: McpServerModel, diags: List[Diagnostic]):
        if not server.command or not server.command.strip():
            diags.append(Diagnostic("ERROR", "Executable command is required for stdio transport.", field="command"))
        else:
            cmd = server.command.strip()
            try:
                masked_cmd = redact_secretish(" ".join(redact_args(shlex.split(cmd))))
            except Exception:
                masked_cmd = MASK_SECRET
            # Whitespace in command is a common pitfall (M18)
            if any(c.isspace() for c in cmd):
                diags.append(Diagnostic("ERROR", f"Command '{masked_cmd}' contains whitespace. Put arguments into the Command Arguments field instead.", field="command"))
            else:
                resolved = shutil.which(cmd)
                if resolved:
                    diags.append(Diagnostic("PASS", f"Executable found in PATH: {resolved}", field="command"))
                else:
                    diags.append(Diagnostic("WARNING", f"Command '{masked_cmd}' not found in current $PATH. Ensure it is installed before running AGY.", field="command"))

        if server.args:
            redacted_args = redact_args(server.args)
            safe_args = " ".join(shlex.quote(a) for a in redacted_args)
            diags.append(Diagnostic("INFO", f"Arguments ({len(server.args)} items): {safe_args}", field="args"))
            for raw_arg, red_arg in zip(server.args, redacted_args):
                raw_str = str(raw_arg)
                if "<your-" in raw_str or "TOKEN_HERE" in raw_str.upper() or "<workspace-root>" in raw_str:
                    diags.append(Diagnostic("WARNING", f"Argument '{red_arg}' contains an unconfigured placeholder value.", field="args"))

        for k, v in server.env.items():
            if not k.strip():
                diags.append(Diagnostic("ERROR", "Environment variable name cannot be empty.", field="env"))
            elif not re.fullmatch(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
                diags.append(Diagnostic("ERROR", f"Environment variable name '{k}' is invalid. Must match '^[A-Za-z_][A-Za-z0-9_]*$'.", field="env"))
            elif not v:
                diags.append(Diagnostic("WARNING", f"Environment variable '{k}' has an empty value.", field="env"))
            elif "<your-" in v or "TOKEN_HERE" in v.upper():
                diags.append(Diagnostic("WARNING", f"Environment variable '{k}' contains an unconfigured placeholder value.", field="env"))

    @staticmethod
    def _check_http_url(server: McpServerModel, diags: List[Diagnostic]):
        if not server.server_url or not server.server_url.strip():
            diags.append(Diagnostic("ERROR", "Server URL is required for HTTP transport.", field="server_url"))
            return
        raw_url = server.server_url.strip()
        try:
            parsed = urllib.parse.urlparse(raw_url)
        except Exception:
            diags.append(Diagnostic("ERROR", "Invalid URL syntax or invalid characters in URL authority.", field="server_url"))
            return

        if parsed.scheme not in ("http", "https"):
            diags.append(Diagnostic("ERROR", "Server URL scheme must be http:// or https://.", field="server_url"))
            return
        if not parsed.hostname:
            diags.append(Diagnostic("ERROR", "Server URL host / domain is missing or invalid.", field="server_url"))
            return

        port = None
        port_error = False
        try:
            port = parsed.port
            if port is not None and not (0 <= port <= 65535):
                port_error = True
                diags.append(Diagnostic("ERROR", "Invalid port in URL.", field="server_url"))
        except ValueError:
            port_error = True
            diags.append(Diagnostic("ERROR", "Invalid port in URL.", field="server_url"))

        if not port_error:
            # Cleartext warning on credentials (M18)
            if parsed.scheme == "http" and (server.auth_provider == "oauth" or any("AUTH" in h.upper() for h in server.headers)):
                diags.append(Diagnostic("WARNING", "Cleartext HTTP transport transmits credentials unencrypted. Use HTTPS.", field="server_url"))
            else:
                host = f"[{parsed.hostname}]" if ":" in (parsed.hostname or "") else parsed.hostname
                host_port = f"{host}:{port}" if port else host
                diags.append(Diagnostic("PASS", f"Valid {parsed.scheme.upper()} endpoint: {host_port}", field="server_url"))

    @staticmethod
    def has_errors(diagnostics: List[Diagnostic]) -> bool:
        return any(d.level == "ERROR" for d in diagnostics)

    @staticmethod
    def probe_server(server: McpServerModel, timeout_seconds: float = 3.0, cache_dir: Optional[str] = None) -> dict:
        """
        Actively confirms the validity of the MCP server configuration by initiating
        a live, non-destructive JSON-RPC handshake (initialize + tools/list).
        If cache_dir is provided and tools are discovered, persists schemas to disk.
        Returns a dictionary with status, latency, server info, tools, and raw_tools.
        """
        if server.transport == "stdio":
            if not server.command or not server.command.strip():
                return {"ok": False, "error": "Cannot test: Executable command is empty."}
            cmd = [server.command.strip(), *server.args]
            env = {**os.environ, **server.env}
            t0 = time.time()
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    text=True
                )
            except Exception as e:
                return {"ok": False, "error": f"Failed to spawn process: {e}"}

            try:
                # 1. Send initialize
                init_req = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "agy-tui-probe", "version": "1.0"}
                    }
                }
                proc.stdin.write(json.dumps(init_req) + "\n")
                proc.stdin.flush()

                deadline = time.time() + timeout_seconds
                init_line = ""
                import select
                while time.time() < deadline:
                    if proc.poll() is not None:
                        break
                    r, _, _ = select.select([proc.stdout], [], [], 0.05)
                    if r:
                        init_line = proc.stdout.readline()
                        break

                if not init_line:
                    proc.kill()
                    return {"ok": False, "error": f"Timed out or process exited (exit code {proc.poll()}) waiting for initialize response."}

                init_res = json.loads(init_line)
                srv_info = init_res.get("result", {}).get("serverInfo", {})

                # 2. Send initialized notification & tools/list
                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
                proc.stdin.flush()

                tools_line = ""
                while time.time() < deadline:
                    r, _, _ = select.select([proc.stdout], [], [], 0.05)
                    if r:
                        tools_line = proc.stdout.readline()
                        break

                tools = []
                raw_tools = []
                if tools_line:
                    try:
                        tools_res = json.loads(tools_line)
                        raw_tools = tools_res.get("result", {}).get("tools", [])
                        tools = [t.get("name") for t in raw_tools if isinstance(t, dict) and t.get("name")]
                        if cache_dir and raw_tools and server.name:
                            target_dir = os.path.join(cache_dir, server.name)
                            os.makedirs(target_dir, exist_ok=True)
                            for t in raw_tools:
                                t_name = t.get("name")
                                if t_name:
                                    t_path = os.path.join(target_dir, f"{t_name}.json")
                                    with open(t_path, "w", encoding="utf-8") as tf:
                                        json.dump(t, tf, indent=2)
                    except Exception:
                        pass

                try:
                    proc.stdin.close()
                except Exception:
                    pass
                try:
                    proc.stdout.close()
                except Exception:
                    pass
                try:
                    proc.stderr.close()
                except Exception:
                    pass
                proc.terminate()
                try:
                    proc.wait(timeout=0.5)
                except Exception:
                    proc.kill()

                latency = round((time.time() - t0) * 1000, 1)
                return {
                    "ok": True,
                    "latency_ms": latency,
                    "server_name": srv_info.get("name", "unnamed"),
                    "server_version": srv_info.get("version", "unknown"),
                    "tools": tools,
                    "raw_tools": raw_tools
                }
            except Exception as e:
                try:
                    if proc.stdin: proc.stdin.close()
                    if proc.stdout: proc.stdout.close()
                    if proc.stderr: proc.stderr.close()
                except Exception:
                    pass
                try:
                    proc.kill()
                except Exception:
                    pass
                return {"ok": False, "error": f"Communication error: {e}"}

        elif server.transport == "http":
            if not server.server_url or not server.server_url.strip():
                return {"ok": False, "error": "Cannot test: Server URL is empty."}
            t0 = time.time()
            import urllib.request
            import urllib.error
            headers = {"Content-Type": "application/json", **server.headers}
            init_req_data = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "agy-tui-probe", "version": "1.0"}
                }
            }).encode("utf-8")
            req = urllib.request.Request(server.server_url, data=init_req_data, headers=headers, method="POST")
            try:
                status = 200
                srv_info = {}
                with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                    status = resp.status
                    data = resp.read().decode("utf-8", errors="replace")
                    try:
                        res_json = json.loads(data)
                        srv_info = res_json.get("result", {}).get("serverInfo", {})
                    except Exception:
                        pass

                # Probe tools on HTTP endpoint
                tools = []
                raw_tools = []
                tools_req_data = json.dumps({
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                }).encode("utf-8")
                tools_req = urllib.request.Request(server.server_url, data=tools_req_data, headers=headers, method="POST")
                try:
                    with urllib.request.urlopen(tools_req, timeout=timeout_seconds) as resp2:
                        data2 = resp2.read().decode("utf-8", errors="replace")
                        tools_json = json.loads(data2)
                        raw_tools = tools_json.get("result", {}).get("tools", [])
                        tools = [t.get("name") for t in raw_tools if isinstance(t, dict) and t.get("name")]
                        if cache_dir and raw_tools and server.name:
                            target_dir = os.path.join(cache_dir, server.name)
                            os.makedirs(target_dir, exist_ok=True)
                            for t in raw_tools:
                                t_name = t.get("name")
                                if t_name:
                                    t_path = os.path.join(target_dir, f"{t_name}.json")
                                    with open(t_path, "w", encoding="utf-8") as tf:
                                        json.dump(t, tf, indent=2)
                except Exception:
                    pass

                latency = round((time.time() - t0) * 1000, 1)
                return {
                    "ok": True,
                    "latency_ms": latency,
                    "server_name": srv_info.get("name", "remote-http"),
                    "server_version": srv_info.get("version", f"HTTP {status}"),
                    "tools": tools,
                    "raw_tools": raw_tools
                }
            except urllib.error.HTTPError as e:
                latency = round((time.time() - t0) * 1000, 1)
                if e.code in (401, 403):
                    return {
                        "ok": True,
                        "latency_ms": latency,
                        "server_name": "remote-auth-required",
                        "server_version": f"HTTP {e.code}",
                        "tools": [],
                        "raw_tools": []
                    }
                return {"ok": False, "error": f"HTTP error {e.code}: {e.reason}"}
            except Exception as e:
                return {"ok": False, "error": f"Connection failed: {e}"}

        return {"ok": False, "error": f"Unsupported transport: {server.transport}"}


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




def _mask_edit_buffer_args(edit_buffer: str) -> str:
    """
    Safely redacts edit buffer in FormView args edit mode via synthetic close and reuse.
    """
    if not isinstance(edit_buffer, str) or not edit_buffer:
        return edit_buffer

    num_trailing_bs = len(edit_buffer) - len(edit_buffer.rstrip("\\"))
    dangling_escape = (num_trailing_bs % 2 == 1)
    buf_for_quotes = edit_buffer[:-1] if dangling_escape else edit_buffer
    unclosed = _find_unclosed_quote(buf_for_quotes)
    q = unclosed[1] if unclosed else None
    candidate = buf_for_quotes + (q or "")

    try:
        tokens = shlex.split(candidate)
        redacted = redact_args(tokens)
        return shlex.join(redacted)
    except Exception:
        return MASK_SECRET


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
        self.form_tab = 1  # 1 to 4
        self.form_field_idx = 0
        self.editing_field = False
        self.edit_buffer = ""
        self.mask_secrets = True
        self.form_dirty = False
        self.last_probe_result: Optional[dict] = None

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
        self.tools_selected_idx = 0

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
                try:
                    if srv.disabled:
                        self.config_mgr.enable_server(name)
                    else:
                        self.config_mgr.disable_server(name)
                    self.load_data()
                    new_srv = self.servers.get(name)
                    state_str = "Disabled" if (new_srv and new_srv.disabled) else "Enabled"
                    self.set_status(f"Server '{name}' {state_str.lower()}.")
                except (ConfigParseError, OSError, ValueError) as e:
                    self.set_status(f"Error updating server '{name}': {e}", is_error=True)
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
                self.mask_secrets = True
                self.table_row_idx = 0
                self.table_col_idx = 0
                self.last_probe_result = None
                self.view = "form"
        elif key in ("a", "A"):
            self.form_server = McpServerModel(name="new-mcp-server", transport="stdio", command="npx")
            self.form_original_name = None
            self.form_is_new = True
            self.form_tab = 1
            self.form_field_idx = 0
            self.editing_field = False
            self.form_dirty = False
            self.mask_secrets = True
            self.table_row_idx = 0
            self.table_col_idx = 0
            self.last_probe_result = None
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
                # Auto-sync on open if tools not cached yet and server is enabled
                if not self.tools_list:
                    srv = self.servers.get(name)
                    cache_dir = getattr(self.tool_reader, "base_dir", None)
                    if srv and not srv.disabled and cache_dir:
                        try:
                            PreflightChecker.probe_server(srv, timeout_seconds=1.5, cache_dir=cache_dir)
                            self.tools_list = self.tool_reader.get_tools(name)
                            self.load_data()
                        except Exception:
                            pass
                self.tools_selected_idx = 0
                self.tools_scroll_offset = 0
                self.view = "tools"
        elif key in ("r", "R"):
            cache_dir = getattr(self.tool_reader, "base_dir", None)
            msg = "Refreshed MCP configuration and tool schemas."
            if total > 0 and self.dash_selected_idx < total:
                sel_name = self.server_keys[self.dash_selected_idx]
                sel_srv = self.servers.get(sel_name)
                if sel_srv and not sel_srv.disabled and cache_dir:
                    try:
                        probe = PreflightChecker.probe_server(sel_srv, timeout_seconds=1.5, cache_dir=cache_dir)
                        if probe.get("ok"):
                            tool_cnt = len(probe.get("tools", []))
                            msg = f"Refreshed: discovered {tool_cnt} tool(s) for '{sel_name}'."
                    except Exception:
                        pass
            self.load_data()
            self.set_status(msg)
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
        col_tools = 13
        col_auth = 13
        fixed_sum = col_st + col_tr + col_tools + col_auth + 6  # 49 + margins
        remaining = max(24, width - fixed_sum)
        col_name = min(22, remaining // 2)
        col_cmd = max(12, remaining - col_name)

        th = (
            f"  {c.BOLD}{'STATUS':<{col_st}}"
            f"{'SERVER NAME':<{col_name}}"
            f"{'TRANS':<{col_tr}}"
            f"{'COMMAND / URL':<{col_cmd}}"
            f"{pad_visible('TOOLS', col_tools, align='center')}"
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

                cmd_val = _format_masked_cmd_or_url(srv)

                t_count = self.cached_tool_counts.get(name, 0)
                disabled_cnt = len(srv.disabled_tools) if srv.disabled_tools else 0
                active_cnt = max(0, t_count - disabled_cnt)
                if t_count == 0:
                    t_str = f"{c.DIM}0 tools{c.RESET}"
                elif disabled_cnt > 0:
                    t_str = f"{c.YELLOW}{active_cnt}/{t_count} tools{c.RESET}"
                else:
                    t_str = f"{t_count} tools"

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
                    f"{pad_visible(t_str, col_tools, align='center')}"
                    f"{pad_visible(auth_str, col_auth)}"
                )

                lines.append(truncate_visible(row_str, width))


            for _ in range(table_height - len(visible_slice)):
                lines.append("")

        lines.append(f"{c.GRAY}{'─' * width}{c.RESET}")
        if width >= 90:
            keymap = (
                f" {c.BOLD}[Space]{c.RESET} Toggle  "
                f"{c.BOLD}[e/Enter]{c.RESET} Edit  "
                f"{c.BOLD}[a]{c.RESET} Add  "
                f"{c.BOLD}[t]{c.RESET} Recipes  "
                f"{c.BOLD}[v]{c.RESET} Tools  "
                f"{c.BOLD}[r]{c.RESET} Refresh  "
                f"{c.BOLD}[d]{c.RESET} Delete  "
                f"{c.BOLD}[q]{c.RESET} Quit"
            )
        else:
            keymap = (
                f" {c.BOLD}[Space]{c.RESET} Toggle  "
                f"{c.BOLD}[e]{c.RESET} Edit  "
                f"{c.BOLD}[a]{c.RESET} Add  "
                f"{c.BOLD}[v]{c.RESET} Tools  "
                f"{c.BOLD}[r]{c.RESET} Refresh  "
                f"{c.BOLD}[d]{c.RESET} Del  "
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
        if key in ("1", "2", "3", "4"):
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
            self.form_tab = (self.form_tab % 4) + 1
            self.form_field_idx = 0
            return
        if key == "BACKTAB":
            self.form_tab = 4 if self.form_tab == 1 else self.form_tab - 1
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

    def handle_tab1_key(self, key: str):
        srv = self.form_server
        is_stdio = (srv.transport == "stdio")
        total_fields = 6 if is_stdio else 5

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
                    srv.disabled = not srv.disabled
                    self.form_dirty = True
            else:
                if idx == 2:
                    self.start_edit_buffer(srv.server_url)
                elif idx == 3:
                    self.start_edit_buffer(str(srv.timeout_seconds))
                elif idx == 4:
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
                # Add item with unique key (M5)
                prefix = "VAR_" if is_stdio else "Header-"
                idx = 1
                while f"{prefix}{idx}" in target_dict or (not is_stdio and any(k.lower() == f"{prefix}{idx}".lower() for k in target_dict)):
                    idx += 1
                new_key = f"{prefix}{idx}"
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
        if key in ("t", "T"):
            self.set_status("Testing live MCP server connection...")
            cache_dir = getattr(self.tool_reader, "base_dir", None) if hasattr(self, "tool_reader") else None
            self.last_probe_result = PreflightChecker.probe_server(self.form_server, cache_dir=cache_dir)
            if self.last_probe_result.get("ok"):
                tool_cnt = len(self.last_probe_result.get("tools", []))
                s_name = self.last_probe_result.get("server_name", "server")
                lat = self.last_probe_result.get("latency_ms", 0)
                if hasattr(self, "cached_tool_counts") and self.form_server.name:
                    self.cached_tool_counts[self.form_server.name] = tool_cnt
                self.set_status(f"Live probe PASS: '{s_name}' connected in {lat}ms ({tool_cnt} tool(s) found).")
            else:
                self.set_status("Live probe FAILED. See details in Preflight view.", is_error=True)
        elif key in ("ENTER", "s", "S"):
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
                        other_keys = [k for k in target_dict if k != old_key]
                        if val in other_keys or (not is_stdio and any(k.lower() == val.lower() for k in other_keys)):
                            self.set_status(f"Key '{val}' already exists.", is_error=True)
                        else:
                            new_dict = {}
                            for k, v in target_dict.items():
                                if k == old_key:
                                    new_dict[val] = v
                                else:
                                    new_dict[k] = v
                            target_dict.clear()
                            target_dict.update(new_dict)
                            self.form_dirty = True
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
            self.form_tab = 4
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

    def _execute_save(self, overwrite: bool = False):
        srv = self.form_server
        try:
            # If renamed, use single atomic rename transaction (H2)
            if self.form_original_name and self.form_original_name != srv.name:
                self.config_mgr.rename_server(self.form_original_name, srv, overwrite=overwrite)
            else:
                is_new = (self.form_original_name is None)
                self.config_mgr.set_server(srv, overwrite=overwrite, is_new=is_new)

            # If enabled and we don't have cached tools yet, attempt a quick probe to cache schemas
            cache_dir = getattr(self.tool_reader, "base_dir", None) if hasattr(self, "tool_reader") else None
            if not srv.disabled and cache_dir and self.tool_reader.get_tool_count(srv.name) == 0:
                try:
                    PreflightChecker.probe_server(srv, timeout_seconds=2.0, cache_dir=cache_dir)
                except Exception:
                    pass

            self.load_data()
            self.form_dirty = False
            self.view = "dashboard"
            self.set_status(f"Server '{srv.name}' saved successfully.")
        except (ConfigParseError, OSError, ValueError, KeyError, Exception) as e:
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
            (4, "Preflight & Preview"),
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
            content_lines = self._render_tab4(srv, width, height)

        max_content = max(5, height - 7)
        for cl in content_lines[:max_content]:
            lines.append(truncate_visible(cl, width))
        for _ in range(max_content - len(content_lines[:max_content])):
            lines.append("")

        lines.append(f"{c.GRAY}{'─' * width}{c.RESET}")
        if self.editing_field:
            footer = f" {c.GREEN}{c.BOLD}EDITING:{c.RESET} [Enter] Commit  [Esc] Cancel  (Type to edit)"
        elif self.form_tab == 4:
            footer = (
                f" {c.BOLD}[1-4]{c.RESET} Tabs  "
                f"{c.BOLD}[t]{c.RESET} Test Server  "
                f"{c.BOLD}[m]{c.RESET} Mask  "
                f"{c.BOLD}[s]{c.RESET} Save  "
                f"{c.BOLD}[Esc]{c.RESET} Cancel"
            )
        else:
            footer = (
                f" {c.BOLD}[1-4]{c.RESET} Tabs  "
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
                if self.mask_secrets and (idx in (2, 3) if is_stdio else idx == 2):
                    if is_stdio:
                        disp_buf = _mask_edit_buffer_args(self.edit_buffer)
                    else:
                        disp_buf = _redact_single_url(self.edit_buffer)
                    val_rendered = f"{c.BG_WHITE}{c.BLACK}{disp_buf}█{c.RESET}"
                else:
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
            if self.mask_secrets and srv.command:
                cmd_disp = _format_masked_cmd_or_url(srv.command)
            else:
                cmd_disp = sanitize_display(srv.command) or "<empty>"
            out.append(item(2, "Executable Command", cmd_disp, path_badge))

            args_str = sanitize_display(" ".join(shlex.quote(a) for a in redact_args(srv.args))) if srv.args else "<none>"
            out.append(item(3, "Command Arguments", args_str, f"Parsed: {len(srv.args)} args"))
            out.append(item(4, "Timeout (Seconds)", str(srv.timeout_seconds), "Default: 60s"))
            out.append(item(5, "Server State", "[x] DISABLED" if srv.disabled else "[ ] ENABLED", "Toggle active state"))
        else:
            safe_url = sanitize_display(_redact_single_url(srv.server_url)) if srv.server_url else "<empty>"
            out.append(item(2, "Server URL", safe_url, "HTTP / HTTPS endpoint"))
            out.append(item(3, "Timeout (Seconds)", str(srv.timeout_seconds), "Default: 60s"))
            out.append(item(4, "Server State", "[x] DISABLED" if srv.disabled else "[ ] ENABLED", "Toggle active state"))

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
                    if self.mask_secrets and (is_stdio or k.lower() not in _PUBLIC_HEADERS_ALLOWLIST or _is_sensitive_param_name(k)):
                        disp_buf = "●" * len(self.edit_buffer) if self.edit_buffer else ""
                        v_str = f"{c.BG_WHITE}{c.BLACK}{disp_buf}█{c.RESET}"
                    else:
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
                if self.mask_secrets:
                    disp_buf = "●" * len(self.edit_buffer) if self.edit_buffer else ""
                    sec_disp = f"{c.BG_WHITE}{c.BLACK}{disp_buf}█{c.RESET}"
                else:
                    sec_disp = f"{c.BG_WHITE}{c.BLACK}{self.edit_buffer}█{c.RESET}"
            elif is_sel_2:
                sec_disp = f"{c.INVERT} {masked_sec} {c.RESET}"
            else:
                sec_disp = f"{c.BOLD}{masked_sec}{c.RESET}"
            out.append(f"  OAuth Client Secret : {sec_disp}  {c.DIM}(Press [m] to toggle masking){c.RESET}")
        else:
            out.append(f"  {c.DIM}No authentication configured. Standard transport headers only.{c.RESET}")

        return out


    def _render_tab4(self, srv: McpServerModel, width: int, height: int) -> List[str]:
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
        out.append(f" {c.BOLD}Live Server Handshake & Tool Probe:{c.RESET}")
        if self.last_probe_result is None:
            out.append(f"  {c.DIM}[ℹ INFO] Press [t] to test live handshake and query tools before saving.{c.RESET}")
        elif self.last_probe_result.get("ok"):
            res = self.last_probe_result
            s_name = res.get("server_name", "unnamed")
            s_ver = res.get("server_version", "")
            lat = res.get("latency_ms", 0)
            tools = res.get("tools", [])
            tools_str = ", ".join(tools) if tools else "<no tools declared>"
            out.append(f"  {c.GREEN}[✓ PASS] Handshake Successful ({lat}ms){c.RESET} Server: {s_name} {s_ver}")
            out.append(f"    {c.CYAN}Discovered {len(tools)} tool(s):{c.RESET} {tools_str}")
        else:
            err = self.last_probe_result.get("error", "Unknown error")
            out.append(f"  {c.RED}{c.BOLD}[✗ FAIL] Handshake Failed:{c.RESET} {err}")

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
            self.mask_secrets = True
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
    # View 4: Discovered Tools Viewer (Interactive Toggling & Mass Controls)
    # --------------------------------------------------------------------------
    def handle_tools_key(self, key: str):
        total_tools = len(self.tools_list)
        srv = self.servers.get(self.tools_server_name)

        if key in ("q", "Q", "ESCAPE"):
            self.view = "dashboard"
            return
        elif key in ("UP", "k", "K"):
            if total_tools > 0:
                self.tools_selected_idx = max(0, self.tools_selected_idx - 1)
        elif key in ("DOWN", "j", "J"):
            if total_tools > 0:
                self.tools_selected_idx = min(total_tools - 1, self.tools_selected_idx + 1)
        elif key == "PAGEUP":
            if total_tools > 0:
                self.tools_selected_idx = max(0, self.tools_selected_idx - 5)
        elif key == "PAGEDOWN":
            if total_tools > 0:
                self.tools_selected_idx = min(total_tools - 1, self.tools_selected_idx + 5)
        elif key in ("SPACE", "ENTER"):
            if srv and total_tools > 0 and self.tools_selected_idx < total_tools:
                t_name = self.tools_list[self.tools_selected_idx].name
                cur_disabled = list(srv.disabled_tools)
                if t_name in cur_disabled:
                    cur_disabled = [x for x in cur_disabled if x != t_name]
                    action_str = "enabled"
                else:
                    cur_disabled.append(t_name)
                    action_str = "disabled"
                srv.disabled_tools = list(dict.fromkeys(cur_disabled))
                try:
                    self.config_mgr.set_server(srv, is_new=False)
                    self.load_data()
                    self.set_status(f"Tool '{t_name}' {action_str} on '{srv.name}'.")
                except Exception as e:
                    self.set_status(f"Error updating tool: {e}", is_error=True)
        elif key in ("a", "A", "e", "E"):
            # Enable all tools
            if srv and total_tools > 0:
                srv.disabled_tools = []
                try:
                    self.config_mgr.set_server(srv, is_new=False)
                    self.load_data()
                    self.set_status(f"All {total_tools} tools enabled on '{srv.name}'.")
                except Exception as e:
                    self.set_status(f"Error enabling tools: {e}", is_error=True)
        elif key in ("x", "X", "d", "D"):
            # Disable all tools
            if srv and total_tools > 0:
                srv.disabled_tools = [t.name for t in self.tools_list]
                try:
                    self.config_mgr.set_server(srv, is_new=False)
                    self.load_data()
                    self.set_status(f"All {total_tools} tools disabled on '{srv.name}'.")
                except Exception as e:
                    self.set_status(f"Error disabling tools: {e}", is_error=True)
        elif key in ("r", "R"):
            if self.tools_server_name:
                srv_live = self.servers.get(self.tools_server_name)
                cache_dir = getattr(self.tool_reader, "base_dir", None)
                if srv_live and not srv_live.disabled and cache_dir:
                    try:
                        PreflightChecker.probe_server(srv_live, timeout_seconds=1.5, cache_dir=cache_dir)
                    except Exception:
                        pass
                self.tools_list = self.tool_reader.get_tools(self.tools_server_name)
                self.load_data()
                self.set_status(f"Refreshed tools for '{self.tools_server_name}' ({len(self.tools_list)} found).")

    def render_tools(self, width: int, height: int) -> List[str]:
        lines = []
        c = Colors

        srv = self.servers.get(self.tools_server_name)
        disabled_set = set(srv.disabled_tools) if srv else set()
        total_cnt = len(self.tools_list)
        disabled_cnt = len(disabled_set.intersection({t.name for t in self.tools_list}))
        active_cnt = max(0, total_cnt - disabled_cnt)

        srv_name = sanitize_display(self.tools_server_name)
        title = f" MCP TOOLS: {srv_name} ({active_cnt}/{total_cnt} active) "
        lines.append(f"{c.BG_CYAN}{c.BLACK}{c.BOLD}{title.center(width)}{c.RESET}")
        lines.append(f" {c.DIM}Schemas cached under ~/.gemini/antigravity-cli/mcp/{srv_name}/  |  Config: {self.config_mgr.config_path}{c.RESET}")
        lines.append(f"{c.GRAY}{'─' * width}{c.RESET}")

        body_lines: List[str] = []
        tool_starts: List[int] = []

        if not self.tools_list:
            body_lines.append("")
            body_lines.append(f"  {c.YELLOW}No cached tool schemas found for '{srv_name}'.{c.RESET}")
            body_lines.append(f"  {c.DIM}Schemas are discovered and cached when AGY connects to the MCP server.{c.RESET}")
            body_lines.append(f"  {c.DIM}Press [r] to probe and sync tools now.{c.RESET}")
        else:
            for idx, t in enumerate(self.tools_list):
                tool_starts.append(len(body_lines))
                is_sel = (idx == self.tools_selected_idx)
                is_disabled = (t.name in disabled_set)

                cursor = f"{c.CYAN}{c.BOLD}❯{c.RESET} " if is_sel else "  "
                if is_disabled:
                    st_pill = f"{c.GRAY}[○] OFF{c.RESET}"
                else:
                    st_pill = f"{c.GREEN}{c.BOLD}[●] ON {c.RESET}"

                t_name = sanitize_display(t.name)
                t_params = sanitize_display(t.parameters_summary)

                if is_sel:
                    disp_name = f"{c.BOLD}{c.CYAN}{c.UNDERLINE}{t_name}{c.RESET}"
                else:
                    disp_name = f"{c.BOLD}{t_name}{c.RESET}"

                body_lines.append(f"{cursor}{st_pill} {disp_name}  {c.CYAN}({t_params}){c.RESET}")

                if t.description:
                    first_line = sanitize_display(t.description.splitlines()[0])
                    body_lines.append(f"      {c.DIM}{first_line[:max(10, width - 8)]}{c.RESET}")
                body_lines.append("")

        max_items = max(5, height - 7)
        if tool_starts and self.tools_selected_idx < len(tool_starts):
            sel_start = tool_starts[self.tools_selected_idx]
            item_len = 3 if (self.tools_selected_idx < len(self.tools_list) and self.tools_list[self.tools_selected_idx].description) else 2
            if sel_start < self.tools_scroll_offset:
                self.tools_scroll_offset = sel_start
            elif sel_start + item_len > self.tools_scroll_offset + max_items:
                self.tools_scroll_offset = max(0, sel_start + item_len - max_items)

        max_scroll = max(0, len(body_lines) - max_items)
        self.tools_scroll_offset = max(0, min(self.tools_scroll_offset, max_scroll))

        visible_lines = body_lines[self.tools_scroll_offset: self.tools_scroll_offset + max_items]
        for vl in visible_lines:
            lines.append(truncate_visible(vl, width))
        for _ in range(max_items - len(visible_lines)):
            lines.append("")

        lines.append(f"{c.GRAY}{'─' * width}{c.RESET}")
        keymap = (
            f" {c.BOLD}[Space]{c.RESET} Toggle  "
            f"{c.BOLD}[a]{c.RESET} All ON  "
            f"{c.BOLD}[x]{c.RESET} All OFF  "
            f"{c.BOLD}[r]{c.RESET} Refresh  "
            f"{c.BOLD}[↑/↓]{c.RESET} Navigate  "
            f"{c.BOLD}[Esc/q]{c.RESET} Back"
        )
        lines.append(truncate_visible(keymap, width))

        if self.status_msg:
            status_color = f"{c.RED}{c.BOLD}" if self.status_is_error else f"{c.GREEN}{c.BOLD}"
            st_line = f" {status_color}▶ {self.status_msg}{c.RESET}"
        else:
            st_line = f" {c.DIM}Active tools: {active_cnt} / {total_cnt}{c.RESET}"
        lines.append(truncate_visible(st_line, width))

        return lines

    # --------------------------------------------------------------------------
    # View 5: Modals (Delete, Discard, Overwrite) (C4, M8)
    # --------------------------------------------------------------------------
    def handle_delete_key(self, key: str):
        if key in ("y", "Y"):
            try:
                self.config_mgr.remove_server(self.delete_target)
                self.load_data()
                self.view = "dashboard"
                self.set_status(f"Server '{self.delete_target}' removed.")
            except (ConfigParseError, OSError, ValueError) as e:
                self.view = "dashboard"
                self.set_status(f"Error removing server '{self.delete_target}': {e}", is_error=True)
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
            self._execute_save(overwrite=True)
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
    col_tools = 13
    col_auth = 16

    header = (
        f"{c.BOLD}{'NAME':<{col_name}} "
        f"{'STATUS':<{col_st}} "
        f"{'TRANSPORT':<{col_tr}} "
        f"{pad_visible('TOOLS', col_tools, align='center')} "
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
        disabled_cnt = len(srv.disabled_tools) if srv.disabled_tools else 0
        active_cnt = max(0, t_count - disabled_cnt)
        if t_count == 0:
            tool_disp = f"{c.DIM}0 tools{c.RESET}"
        elif disabled_cnt > 0:
            tool_disp = f"{c.YELLOW}{active_cnt}/{t_count} tools{c.RESET}"
        else:
            tool_disp = f"{t_count} tools"

        if srv.auth_provider == "google_credentials":
            auth_disp = f"{c.GREEN}Google ADC{c.RESET}"
        elif srv.auth_provider == "oauth":
            auth_disp = f"{c.YELLOW}OAuth 2.0{c.RESET}"
        else:
            auth_disp = f"{c.DIM}none{c.RESET}"

        if not show_secrets:
            target = _format_masked_cmd_or_url(srv)
        else:
            raw_target = f"{srv.command} {' '.join(srv.args)}".strip() if srv.transport == "stdio" else srv.server_url
            target = sanitize_display(raw_target)
        safe_name = sanitize_display(name)

        row = (
            f"{pad_visible(f'{c.BOLD}{safe_name}{c.RESET}', col_name)} "
            f"{pad_visible(status_disp, col_st)} "
            f"{pad_visible(tr_disp, col_tr)} "
            f"{pad_visible(tool_disp, col_tools, align='center')} "
            f"{pad_visible(auth_disp, col_auth)} "
            f"{target}"
        )
        print(row)
    sys.exit(0)


def _servers_or_raise(raw: Dict[str, Any], path: str = "") -> Dict[str, Any]:
    """Ensures mcpServers exists and is a dictionary, raising ConfigParseError otherwise."""
    if not isinstance(raw, dict):
        raise ConfigParseError(f"Root of configuration must be a JSON object, got {type(raw).__name__}", path=path)
    servers = raw.get("mcpServers")
    if servers is None:
        return {}
    if not isinstance(servers, dict):
        raise ConfigParseError(f"'mcpServers' must be a JSON dictionary, got {type(servers).__name__}", path=path)
    return servers


def run_cli_enable(config_mgr: ConfigManager, name: str, dry_run: bool = False, show_secrets: bool = False) -> None:
    raw = config_mgr.load_raw_json()
    servers = _servers_or_raise(raw, path=config_mgr.config_path)
    if name not in servers:
        sys.stderr.write(f"Error: Server '{name}' not found in configuration.\n")
        sys.exit(1)
    if dry_run:
        entry = copy.deepcopy(servers[name])
        if isinstance(entry, dict):
            entry.pop("disabled", None)
        payload = entry if show_secrets else redact_server_dict(entry)
        print(f"[Dry Run] Would enable server '{name}':")
        print(json.dumps({name: payload}, indent=2, ensure_ascii=False))
        sys.exit(0)
    success = config_mgr.enable_server(name)
    if not success:
        sys.stderr.write(f"Error: Failed to enable server '{name}'. Server not found or invalid.\n")
        sys.exit(1)
    print(f"✓ Enabled MCP server '{name}'.")
    sys.exit(0)


def run_cli_disable(config_mgr: ConfigManager, name: str, dry_run: bool = False, show_secrets: bool = False) -> None:
    raw = config_mgr.load_raw_json()
    servers = _servers_or_raise(raw, path=config_mgr.config_path)
    if name not in servers:
        sys.stderr.write(f"Error: Server '{name}' not found in configuration.\n")
        sys.exit(1)
    if dry_run:
        entry = copy.deepcopy(servers[name])
        if isinstance(entry, dict):
            entry["disabled"] = True
        payload = entry if show_secrets else redact_server_dict(entry)
        print(f"[Dry Run] Would disable server '{name}':")
        print(json.dumps({name: payload}, indent=2, ensure_ascii=False))
        sys.exit(0)
    success = config_mgr.disable_server(name)
    if not success:
        sys.stderr.write(f"Error: Failed to disable server '{name}'. Server not found or invalid.\n")
        sys.exit(1)
    print(f"✓ Disabled MCP server '{name}'.")
    sys.exit(0)


def run_cli_remove(config_mgr: ConfigManager, name: str, dry_run: bool = False) -> None:
    raw = config_mgr.load_raw_json()
    servers = _servers_or_raise(raw, path=config_mgr.config_path)
    if name not in servers:
        sys.stderr.write(f"Error: Server '{name}' not found in configuration.\n")
        sys.exit(1)
    if dry_run:
        print(f"[Dry Run] Would remove server '{name}' from configuration.")
        sys.exit(0)
    success = config_mgr.remove_server(name)
    if not success:
        sys.stderr.write(f"Error: Failed to remove server '{name}'. Server not found.\n")
        sys.exit(1)
    print(f"✓ Removed MCP server '{name}'. Backup saved to {config_mgr.backup_path}.")
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

    # CLI flag validation (H5, L1)
    mutation_flags = [f for f, v in [("--enable", args.enable), ("--disable", args.disable), ("--remove", args.remove)] if v]
    if args.dry_run and not mutation_flags:
        parser.error("--dry-run requires a mutation action flag (-e/--enable, -d/--disable, or -r/--remove).")

    op_flags = [f for f, v in [("--list", args.list), ("--enable", args.enable), ("--disable", args.disable), ("--remove", args.remove)] if v]
    if len(op_flags) > 1:
        parser.error(f"Cannot specify multiple operations simultaneously: {', '.join(op_flags)}")

    if (args.json or args.show_secrets) and not mutation_flags:
        args.list = True

    if args.json and not args.list:
        parser.error("--json can only be used with --list.")

    if args.show_secrets and not (args.list or (args.dry_run and mutation_flags)):
        parser.error("--show-secrets requires --list or mutation --dry-run.")

    config_mgr = ConfigManager(args.config)

    # Interactive TUI mode (when no operation flags specified and both stdin/stdout are TTY)
    is_interactive = not (args.list or args.enable or args.disable or args.remove)
    try:
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

        if args.enable:
            run_cli_enable(config_mgr, args.enable, dry_run=args.dry_run, show_secrets=args.show_secrets)

        if args.disable:
            run_cli_disable(config_mgr, args.disable, dry_run=args.dry_run, show_secrets=args.show_secrets)

        if args.remove:
            run_cli_remove(config_mgr, args.remove, dry_run=args.dry_run)

    except ConfigParseError as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(2)
    except OSError as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
