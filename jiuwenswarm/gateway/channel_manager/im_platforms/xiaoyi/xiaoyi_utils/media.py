# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""XiaoYi Media Utils - 媒体下载、处理和保存功能。
基于 TypeScript xiaoyi-media.ts 实现。
"""

import asyncio
import base64
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
import ssl
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from jiuwenswarm.common.utils import get_xy_tmp_dir

logger = logging.getLogger(__name__)

_TMP_MEDIA_PATH = get_xy_tmp_dir()


# ==================== Configuration ====================
@dataclass
class MediaDownloadOptions:
    """媒体下载选项."""
    max_bytes: int = 30_000_000  # 30MB default
    timeout_ms: int = 60_000  # 60 seconds default


# ==================== Data Classes ====================
@dataclass
class DownloadedMedia:
    """已下载的媒体文件."""
    path: str
    content_type: str
    placeholder: str
    file_name: str | None = None


@dataclass
class MediaFile:
    """待下载的媒体文件信息."""
    uri: str
    mime_type: str
    name: str


# ==================== MIME Type Detection ====================
def is_image_mime_type(mime_type: str | None) -> bool:
    """检查 MIME 类型是否为图片."""
    if not mime_type:
        return False

    lower = mime_type.lower()

    # 标准格式: image/jpeg, image/png 等
    if lower.startswith("image/"):
        return True

    # 处理非标准格式，如 "jpeg" 而非 "image/jpeg"
    if "/" in lower:
        subtype = lower.split("/")[1]
    else:
        subtype = lower

    image_subtypes = ["jpeg", "jpg", "png", "gif", "webp", "bmp", "svg+xml", "svg"]
    return subtype in image_subtypes


def is_pdf_mime_type(mime_type: str | None) -> bool:
    """检查 MIME 类型是否为 PDF."""
    return (mime_type or "").lower() == "application/pdf"


def is_text_mime_type(mime_type: str | None) -> bool:
    """检查 MIME 类型是否为文本类型."""
    if not mime_type:
        return False

    lower = mime_type.lower()
    text_mimes = [
        "text/",
        "application/json",
        "application/xml",
        "text/xml",
    ]

    return any(lower.startswith(mime.rstrip("/")) for mime in text_mimes)


def _infer_placeholder(mime_type: str) -> str:
    """根据 MIME 类型推断占位符文本。"""
    if mime_type.startswith("image/"):
        return "<media:image>"
    elif mime_type.startswith("video/"):
        return "<media:video>"
    elif mime_type.startswith("audio/"):
        return "<media:audio>"
    elif mime_type == "application/pdf":
        return "<media:document>"
    elif mime_type.startswith("text/"):
        return "<media:text>"
    else:
        return "<media:document>"


# ==================== HTTP Download ====================
def _proxy_for_url(url: str) -> str | None:
    """按目标地址分流：环回/纯 IP 直连；公网域名经 CLAW_HTTP_PROXY 出网。

    规则对齐 XYFileUploadService._smart_proxy_for，但不抽公共 helper。
    """
    try:
        host = urlsplit(url).hostname or ""
    except Exception:
        return None
    if not host:
        return None
    if host in ("localhost", "127.0.0.1", "::1"):
        return None
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host):
        return None
    proxy = (os.environ.get("CLAW_HTTP_PROXY") or "").strip()
    return proxy or None


async def _fetch_from_url(
    url: str,
    max_bytes: int,
    timeout_ms: int
) -> tuple[bytes, str]:
    """
    从 URL 下载内容。

    Returns:
        tuple[bytes, str]: (buffer, mime_type)
    """
    timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)
    proxy = _proxy_for_url(url)
    headers = {"User-Agent": "XiaoYi-Channel/1.0"}

    async def _read_once(*, ssl_opt: bool | None = None) -> tuple[bytes, str]:
        get_kwargs: dict[str, Any] = {
            "timeout": timeout,
            "headers": headers,
        }
        if proxy:
            get_kwargs["proxy"] = proxy
        if ssl_opt is False:
            get_kwargs["ssl"] = False
        async with aiohttp.ClientSession() as session:
            async with session.get(url, **get_kwargs) as response:
                response.raise_for_status()

                content_length = response.headers.get("content-length")
                if content_length:
                    size = int(content_length)
                    if size > max_bytes:
                        raise ValueError(f"File too large: {size} bytes (limit: {max_bytes})")

                buffer = await response.read()

                if len(buffer) > max_bytes:
                    raise ValueError(f"File too large: {len(buffer)} bytes (limit: {max_bytes})")

                content_type = response.headers.get("content-type", "application/octet-stream")
                mime_type = content_type.split(";")[0].strip() if ";" in content_type else content_type

                return buffer, mime_type

    try:
        try:
            return await _read_once()
        except (ssl.SSLError, aiohttp.ClientSSLError, aiohttp.ClientConnectorCertificateError) as exc:
            if not proxy:
                raise
            logger.warning("[XiaoYi Media] 代理 TLS 拦截（%s），容忍拦截重试一次", exc)
            return await _read_once(ssl_opt=False)

    except aiohttp.ClientResponseError as response:
        status = response.status
        reason = response.reason
        raise RuntimeError(f"HTTP {status}: {reason}") from response
    except asyncio.TimeoutError as error:
        raise RuntimeError(f"Download timeout after {timeout_ms}ms") from error 


# ==================== Media Download and Save ====================
async def download_and_save_media(
    url: str,
    mime_type: str,
    file_name: str,
    options: MediaDownloadOptions | None = None,
    save_dir: str | None = None
) -> DownloadedMedia:
    """
    下载并保存媒体文件到本地磁盘。

    Args:
        url: 文件 URL
        mime_type: MIME 类型
        file_name: 文件名
        options: 下载选项
        save_dir: 保存目录 (如果 None，使用临时目录)

    Returns:
        DownloadedMedia: 已下载的媒体信息
    """
    if options is None:
        options = MediaDownloadOptions()

    logger.info(f"[XiaoYi Media] Downloading: {file_name} ({mime_type}) from {url}")

    try:
        buffer, detected_mime_type = await _fetch_from_url(url, options.max_bytes, options.timeout_ms)

        # 使用检测到的 MIME 类型（如果提供的类型是通用的）
        final_mime_type = detected_mime_type if mime_type == "application/octet-stream" else mime_type

        logger.info(f"[XiaoYi Media] Downloaded {len(buffer)} bytes, MIME: {final_mime_type}")

        placeholder = _infer_placeholder(final_mime_type)

        dest_dir = Path(save_dir) if save_dir else _TMP_MEDIA_PATH
        dest_dir.mkdir(parents=True, exist_ok=True)

        file_path = dest_dir / file_name
        with open(file_path, "wb") as f:
            f.write(buffer)

        return DownloadedMedia(
            path=file_path,  # 实际项目中应该保存并返回本地路径
            content_type=final_mime_type,
            placeholder=placeholder,
            file_name=file_name,
        )

    except ValueError as e:
        logger.error(f"[XiaoYi Media] Download failed: {e}")
        raise
    except RuntimeError as e:
        logger.error(f"[XiaoYi Media] Network error: {e}")
        raise
    except Exception as e:
        logger.error(f"[XiaoYi Media] Error: {e}")
        raise


async def download_and_save_media_list(
    files: list[MediaFile],
    options: MediaDownloadOptions | None = None,
    save_dir: str | None = None,
) -> list[DownloadedMedia]:
    """
    下载并保存多个媒体文件。

    Args:
        files: 待下载的文件列表
        options: 下载选项
        save_dir: 保存目录（非空则写入该目录，否则临时目录）

    Returns:
        list[DownloadedMedia]: 已下载的媒体信息列表
    """
    if options is None:
        options = MediaDownloadOptions()

    results: list[DownloadedMedia] = []

    for file in files:
        try:
            downloaded = await download_and_save_media(
                file.uri,
                file.mime_type,
                file.name,
                options,
                save_dir=save_dir,
            )
            results.append(downloaded)
        except Exception as e:
            logger.error(f"[XiaoYi Media] Failed to download {file.name}: {e}")
            # 继续处理其他文件

    return results


# ==================== Media Payload Building ====================
def build_xiaoyi_media_payload(media_list: list[DownloadedMedia]) -> dict[str, Any]:
    """
    构建入站消息的媒体载荷。

    Args:
        media_list: 已下载的媒体列表

    Returns:
        dict: 包含 MediaPath, MediaType, MediaPaths, MediaTypes 等字段的载荷
    """
    if not media_list:
        return {}

    files = [dict(path=str(media.path), type=media.content_type or "") for media in media_list]
    return files


# ==================== Image Extraction ====================
@dataclass
class InputImageContent:
    """用于 AI 处理的图片内容。"""
    type: str = "image"
    data: str = ""  # Base64 编码的图片数据
    mime_type: str = ""


@dataclass
class ImageLimits:
    """图片下载限制。"""
    max_bytes: int = 10_000_000  # 10MB default
    timeout_ms: int = 30_000  # 30 seconds default


async def extract_image_from_url(url: str, limits: ImageLimits | None = None) -> InputImageContent:
    """
    从 URL 提取图片并返回 Base64 编码数据。

    Args:
        url: 图片 URL
        limits: 下载限制

    Returns:
        InputImageContent: 包含 Base64 编码的图片内容
    """
    if limits is None:
        limits = ImageLimits()

    buffer, mime_type = await _fetch_from_url(url, limits.max_bytes, limits.timeout_ms)

    # 验证是否为图片 MIME 类型
    if not is_image_mime_type(mime_type):
        raise ValueError(f"Unsupported image type: {mime_type}")

    return InputImageContent(
        type="image",
        data=base64.b64encode(buffer).decode("utf-8"),
        mime_type=mime_type,
    )


async def extract_text_from_url(
    url: str,
    max_bytes: int = 5_000_000,
    timeout_ms: int = 30_000
) -> str:
    """
    从 URL 提取文本内容。

    Args:
        url: 文件 URL
        max_bytes: 最大字节数 (默认 5MB)
        timeout_ms: 超时时间 (默认 30 秒)

    Returns:
        str: 文本内容
    """
    buffer, mime_type = await _fetch_from_url(url, max_bytes, timeout_ms)

    text_mimes = [
        "text/plain",
        "text/markdown",
        "text/html",
        "text/csv",
        "application/json",
        "application/xml",
        "text/xml",
    ]

    is_text_file = False
    for tm in text_mimes:
        if mime_type.startswith(tm.rstrip("/")) or mime_type == tm.rstrip("/"):
            is_text_file = True
            break

    if not is_text_file:
        raise ValueError(f"Unsupported text type: {mime_type}")

    return buffer.decode("utf-8")
