# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Security guard: KIA + RMS + desensitive blacklist checks for file reads.

Centralised implementation so that every file-read path uses the same logic:
  - PermissionEngine.check_permission  (read_file via FileSystemRail)
  - acp_output_tools.read_text_file     (read_text_file via ACP JSON-RPC)

Order: blacklist first (fuzzy filename match, no IO), then KIA (ICPM
path-based check), then RMS (local byte detection).

Degrade strategy:
  - KIA: fail-closed (ICPM unavailable → block).
  - Blacklist: pass-through (service unavailable → empty list → allow).
  - RMS: pure-local, never degrades.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import ssl
import threading
import time
import http.client
import ntpath
import urllib.request
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── RMS detection (pure local, no network) ──

# Office extensions subject to RMS detection
_RMS_CHECKED_EXTENSIONS = frozenset({
    ".docx", ".xlsx", ".xlsm", ".xlsb", ".pptx",
    ".doc", ".xls", ".ppt",
})

# OLE2 magic bytes (8 bytes)
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# "DataSpaces" encoded as UTF-16LE — only present in RMS-protected OLE2 documents
_DATASPACES_UTF16LE = "DataSpaces".encode("utf-16-le")


def _resolve_path(path: str) -> str:
    """规范化路径：解析 ``..``、符号链接、相对路径为绝对规范路径。

    守卫必须用与实际读取（OS/IDE 解析后）一致的路径去查 ICPM / 比对 / 读字节，
    否则 ``..`` / 符号链接 / 相对路径会让守卫查的目录与实际读取的文件不一致，
    形成 KIA 绕过（例如守卫查 ``C:\\projects\\..\\secret`` 无 KIA，实际读取的却是
    ``C:\\secret\\kia.md``）。统一在此 realpath 后再交给 ICPM 与 RMS 检测。
    """
    try:
        return os.path.realpath(os.path.abspath(path))
    except (OSError, ValueError):
        # 路径含非法字符等异常 — 退回原值，由后续 ICPM/读取各自处理
        return path


def detect_rms_file(path: str) -> str | None:
    """Detect RMS encryption in a file. Returns reason string if RMS, None if clean."""
    resolved = _resolve_path(path)
    ext = pathlib.Path(resolved).suffix.lower()
    if ext not in _RMS_CHECKED_EXTENSIONS:
        return None
    try:
        with open(resolved, "rb") as f:
            header = f.read(8)
        if not header.startswith(_OLE2_MAGIC):
            return None
        # OLE2 file — read more to check for DataSpaces storage (RMS indicator)
        with open(resolved, "rb") as f:
            chunk = f.read(8192)
        if _DATASPACES_UTF16LE in chunk:
            return f"RMS-encrypted Office file: {ext}"
    except OSError:
        pass  # Cannot read — degrade to allow
    return None


# ── KIA detection (ICPM HTTP service) ──

_ICPM_DEFAULT_BASE_URL = "http://127.0.0.1:32200"
_ICPM_TIMEOUT_SECONDS = 3


def _icpm_endpoint() -> tuple[str, int]:
    """Return ICPM host/port from ``ICPM_BASE_URL`` env (default 127.0.0.1:32200).

    Mirrors the TS-side ``ICPM_BASE_URL`` so the Python guard can target a
    stub during tests or an alternate ICPM instance.
    """
    base = os.environ.get("ICPM_BASE_URL", "").strip() or _ICPM_DEFAULT_BASE_URL
    parsed = urlparse(base)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 32200
    return host, port


def _query_kia_paths_for_dir(dir_path: str) -> list[str]:
    """Query ICPM for KIA file paths under ``dir_path`` (blocking, sync).

    Uses stdlib :mod:`http.client` — its lenient HTTP parser tolerates ICPM's
    non-conformant responses that simultaneously send ``Content-Length`` and
    ``Transfer-Encoding`` (which ``aiohttp``/``httpx`` reject). Because it is
    blocking, it **must** run off the event loop — :func:`check_kia_file`
    dispatches it via :func:`asyncio.to_thread` so a slow/unreachable ICPM
    never stalls concurrent tasks for up to the 3s timeout.

    Returns the list of KIA paths under the directory (possibly empty).
    Raises on connection failure / HTTP error so the caller can degrade.
    """
    host, port = _icpm_endpoint()
    conn = http.client.HTTPConnection(host, port, timeout=_ICPM_TIMEOUT_SECONDS)
    try:
        body = json.dumps({"filePath": dir_path, "pageNo": 1, "pageSize": 500})
        conn.request(
            "POST",
            "/api/queryDirKiaPaths",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        if resp.status != 200:
            return []
        data = json.loads(resp.read().decode("utf-8"))
        if data.get("status") != "success" or not data.get("isExistKia"):
            return []
        return list(data.get("kiaPaths", []))
    finally:
        conn.close()


async def check_kia_file(path: str) -> bool:
    """Check if a file is KIA-classified via ICPM. Returns True if KIA.

    Mirrors the TypeScript icpm-client.ts logic:
      1. Get parent directory of the file
      2. Call POST /api/queryDirKiaPaths with that directory
      3. Check if the file path appears in the returned kiaPaths list

    The blocking HTTP call is dispatched to a worker thread via
    :func:`asyncio.to_thread`, so the event loop stays responsive while ICPM
    responds (up to the 3s timeout) — previously the sync ``http.client``
    call ran inline and stalled the loop for the whole timeout window.

    Controlled by ``KIA_GUARD_ENABLED`` env var. Degrades to allow (returns
    False) if ICPM is unavailable or errors.
    """
    if os.environ.get("KIA_GUARD_ENABLED", "").lower() not in ("1", "true", "yes"):
        return False
    try:
        # 先规范化路径（解析 .. / 符号链接 / 相对路径），确保守卫查的目录与
        # 实际读取的文件一致，否则 .. / symlink / 相对路径可绕过 KIA 守卫。
        # ICPM speaks Windows paths — use ntpath so the guard is correct
        # regardless of host OS (CI runs on Linux where os.path is posixpath
        # and would mangle backslash / drive-letter paths).
        resolved = _resolve_kia_path(path)
        # ICPM service only accepts Windows backslash format paths
        normalized_path = resolved.replace("/", "\\")
        dir_path = _get_parent_directory(normalized_path)
        # Off-load the blocking HTTP call to a worker thread — never stall the
        # event loop while ICPM responds (up to the 3s timeout per file).
        kia_paths = await asyncio.to_thread(_query_kia_paths_for_dir, dir_path)
        return _is_file_in_kia_list(normalized_path, kia_paths)
    except Exception:
        # ICPM unreachable / errored — degrade to allow.
        return False


def _get_parent_directory(file_path: str) -> str:
    """Get parent directory with trailing separator (matches ICPM directory matching).

    Uses ``ntpath`` (Windows semantics) because ICPM directory matching operates
    on Windows paths — backslash is the separator, drive letters are significant.
    On a Linux host ``os.path.dirname`` would treat backslash as a regular char
    and return ``""`` for a Windows path, breaking the parent-directory query.
    """
    parent = ntpath.dirname(file_path)
    if parent and not parent.endswith(("\\", "/")):
        sep = "\\" if "\\" in file_path else "/"
        return parent + sep
    return parent


def _resolve_kia_path(path: str) -> str:
    """Resolve ``..`` / symlinks / relative path for ICPM using Windows semantics.

    ICPM speaks Windows paths. Using ``ntpath`` keeps the guard correct
    regardless of host OS: production runs on Windows, but CI runs on Linux
    where ``os.path`` is posixpath and would mangle Windows-style paths
    (treat backslash as a regular char, prepend cwd to drive-letter paths).

    ``ntpath.realpath`` resolves ``..`` and (on Windows) symlinks, preserving
    the path-canonicalisation anti-bypass behaviour. On non-Windows hosts it
    falls back to syntactic normalisation, which is sufficient because the KIA
    guard is only enabled on W3 Windows machines (non-W3 degrades before
    reaching here).
    """
    try:
        return ntpath.realpath(ntpath.abspath(path))
    except (OSError, ValueError):
        # 路径含非法字符等异常 — 退回原值，由后续 ICPM/读取各自处理
        return path


def _normalize_for_kia_compare(path: str) -> str:
    """Normalise a path for KIA list comparison: lowercase + backslash separators."""
    return path.lower().replace("/", "\\")


def _is_file_in_kia_list(file_path: str, kia_paths: list) -> bool:
    """Check if file_path appears in kia_paths (normalised for comparison)."""
    if not kia_paths:
        return False
    target = _normalize_for_kia_compare(file_path)
    return any(_normalize_for_kia_compare(kp) == target for kp in kia_paths)


# Tool names that read file content and must go through KIA+RMS guards.
_FILE_READ_TOOLS = frozenset({"read_file", "read_text_file"})


def extract_file_path_from_tool_args(tool_name: str, tool_args: dict) -> str | None:
    """Extract the file path from a file-read tool's arguments.

    read_file uses ``file_path`` or ``path``; read_text_file uses ``path``.
    """
    if tool_name not in _FILE_READ_TOOLS:
        return None
    if not isinstance(tool_args, dict):
        return None
    # read_file supports both file_path and path; read_text_file uses path
    path = tool_args.get("file_path") or tool_args.get("path")
    if isinstance(path, str) and path.strip():
        return path.strip()
    return None


# ── Desensitive blacklist guard (fuzzy file/URL match) ──

# Fetches from login-activity service (/api/desensitive/black-rule),
# same env vars as the login-event/token-usage reporter.
#
# Config
_BL_CACHE_TTL_SECONDS = 86400  # 24 hours (success)
_BL_FAILURE_CACHE_TTL_SECONDS = 3 * 60 * 60  # 3 hours (failed fetch — balances recovery vs per-call latency)
_BL_HTTP_TIMEOUT_SECONDS = 5
_BL_MAX_RETRIES = 2
_BL_RETRY_DELAY_SECONDS = 1

# Empty fallback — pass-through when API is unreachable or errors.
_BL_EMPTY: dict[str, list[str]] = {"url": [], "filename": []}

# SSL context for HTTPS to login-activity (self-signed certs in intranet)
_bl_ssl_context = ssl.create_default_context()
_bl_ssl_context.check_hostname = False
_bl_ssl_context.verify_mode = ssl.CERT_NONE

# Cache
_bl_cache_lock = threading.Lock()
_bl_cache: dict[str, Any] = {"data": None, "ts": 0.0}


def _bl_is_enabled() -> bool:
    return os.environ.get("BLACKLIST_GUARD_ENABLED", "").lower() in ("1", "true", "yes")


def _bl_get_url() -> str:
    return os.environ.get("OFFICE_CLAW_LOGIN_STATS_URL", "").strip().rstrip("/")


def _bl_get_token() -> str:
    return os.environ.get("OFFICE_CLAW_LOGIN_STATS_TOKEN", "").strip()


def _bl_fetch_from_api() -> dict[str, list[str]] | None:
    """Fetch blacklist from login-activity service (blocking, sync).

    Returns None on any failure; caller uses empty list (pass-through).
    """
    base_url = _bl_get_url()
    token = _bl_get_token()
    if not base_url:
        return None

    url = f"{base_url}/api/desensitive/black-rule"
    try:
        req = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Office-Claw-User": os.environ.get("OFFICE_CLAW_USER_ID", "system"),
            },
        )
        with urllib.request.urlopen(req, timeout=_BL_HTTP_TIMEOUT_SECONDS, context=_bl_ssl_context) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("[blacklist] fetch failed: %s", exc)
        return None

    if not isinstance(result, dict) or result.get("status") != 200 or not result.get("success"):
        logger.warning("[blacklist] API non-success: %s", result)
        return None

    data = result.get("data")
    if not isinstance(data, list):
        return None

    url_list: list[str] = []
    filename_list: list[str] = []
    for scene in data:
        if not isinstance(scene, dict):
            continue
        scene_name = scene.get("sceneName") or ""
        blacklist = scene.get("blacklist") or []
        if not isinstance(blacklist, list):
            continue
        for item in blacklist:
            if not isinstance(item, dict):
                continue
            word = item.get("zhWord") or item.get("enWord")
            if isinstance(word, str) and word.strip():
                word = word.strip()
                if "url" in scene_name.lower():
                    url_list.append(word)
                elif "文件" in scene_name or "filename" in scene_name.lower():
                    filename_list.append(word)

    if not url_list and not filename_list:
        return None
    return {"url": url_list, "filename": filename_list}


def _bl_fetch_with_retry() -> dict[str, list[str]] | None:
    """Fetch with retry, returns None on all-fail (pass-through)."""
    for attempt in range(1, _BL_MAX_RETRIES + 1):
        result = _bl_fetch_from_api()
        if result is not None:
            if attempt > 1:
                logger.info("[blacklist] fetch succeeded on attempt %d", attempt)
            return result
        if attempt < _BL_MAX_RETRIES:
            time.sleep(_BL_RETRY_DELAY_SECONDS)
    logger.warning(
        "[blacklist] all %d attempts failed, pass-through", _BL_MAX_RETRIES
    )
    return None


async def get_blacklist() -> dict[str, list[str]]:
    """Get current blacklist with cache.

    Success → 24h TTL; failure → 3h TTL (avoids per-call retry latency
    while the service is down, but recovers within 3h).

    Cache-miss dispatches blocking HTTP via ``asyncio.to_thread``
    (same pattern as ``check_kia_file``).
    """
    now = time.time()
    with _bl_cache_lock:
        cached = _bl_cache["data"]
        if cached is not None:
            # Success (non-empty) → 24h; failure (empty) → 3h
            is_success = bool(cached.get("url") or cached.get("filename"))
            ttl = _BL_CACHE_TTL_SECONDS if is_success else _BL_FAILURE_CACHE_TTL_SECONDS
            if now - _bl_cache["ts"] < ttl:
                return cached

    try:
        fetched = await asyncio.to_thread(_bl_fetch_with_retry)
    except Exception as exc:
        logger.warning("[blacklist] get_blacklist unexpected error: %s, pass-through", exc)
        fetched = None

    if fetched is not None:
        with _bl_cache_lock:
            _bl_cache["data"] = fetched
            _bl_cache["ts"] = time.time()
        return fetched

    with _bl_cache_lock:
        _bl_cache["data"] = _BL_EMPTY
        _bl_cache["ts"] = time.time()
    return _BL_EMPTY


def clear_blacklist_cache() -> None:
    """Clear in-memory cache (for testing)."""
    with _bl_cache_lock:
        _bl_cache["data"] = None
        _bl_cache["ts"] = 0.0


async def check_blacklist_file(path: str) -> str | None:
    """Check if a file path matches the filename blacklist (fuzzy/contains).

    Returns the matched word if blocked, None if clean/disabled/empty.
    """
    if not _bl_is_enabled():
        return None
    if not isinstance(path, str) or not path.strip():
        return None

    basename = ntpath.basename(path.strip())
    if not basename:
        return None

    basename_lower = basename.lower()
    blacklist = await get_blacklist()
    for word in blacklist.get("filename", []):
        if word.lower() in basename_lower:
            return word
    return None


async def check_blacklist_url(url: str) -> str | None:
    """Check if a URL matches the URL blacklist (fuzzy/contains).

    Returns the matched word if blocked, None if clean/disabled/empty.
    """
    if not _bl_is_enabled():
        return None
    if not isinstance(url, str) or not url.strip():
        return None

    url_lower = url.strip().lower()
    blacklist = await get_blacklist()
    for word in blacklist.get("url", []):
        if word.lower() in url_lower:
            return word
    return None


__all__ = [
    "detect_rms_file",
    "check_kia_file",
    "extract_file_path_from_tool_args",
    "check_blacklist_file",
    "check_blacklist_url",
]
