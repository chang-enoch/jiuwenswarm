# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared SSL verification configuration for HTTP tools."""

from __future__ import annotations

import os
import ssl


def _env_bool(key: str, default: bool = True) -> bool:
    raw = os.environ.get(key, "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return default


def get_ssl_verify() -> bool:
    """Return whether SSL certificate verification is enabled."""
    return _env_bool("JIUWENSWARM_SSL_VERIFY", default=True)


def get_requests_verify() -> bool:
    """Return the verify kwarg value for requests calls."""
    return get_ssl_verify()


def get_insecure_ssl_context() -> ssl.SSLContext:
    """Return an SSL context that skips certificate verification."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ---------------------------------------------------------------------------
# TLS 套件白名单（2026-09-18，与桌面端 src/core/net/tls-policy.ts 同口径手工同步；
# 桌面侧方案见 docs/tls-cert-validation-and-cipher-hardening-2026-09-16.md §A）
#
# 白名单是「允许集」：只声明 OpenSSL 支持的 AEAD 子集（DSS/PSK 在 HTTPS 场景
# 不可达、SM4 OpenSSL 不支持，均不列）。Python ssl 的套件串是 OpenSSL 名。
# ---------------------------------------------------------------------------

# TLS 1.2 套件白名单（OpenSSL 名）
SECURE_TLS12_CIPHERS = ":".join([
    "ECDHE-ECDSA-AES128-GCM-SHA256",
    "ECDHE-ECDSA-AES256-GCM-SHA384",
    "ECDHE-RSA-AES128-GCM-SHA256",
    "ECDHE-RSA-AES256-GCM-SHA384",
    "ECDHE-RSA-CHACHA20-POLY1305",
    "ECDHE-ECDSA-CHACHA20-POLY1305",
    "DHE-RSA-AES128-GCM-SHA256",
    "DHE-RSA-AES256-GCM-SHA384",
    "DHE-RSA-CHACHA20-POLY1305",
    "ECDHE-ECDSA-AES128-CCM",
    "ECDHE-ECDSA-AES256-CCM",
    "DHE-RSA-AES128-CCM",
    "DHE-RSA-AES256-CCM",
])

# TLS 1.3 套件白名单（SM4 两套 OpenSSL 不支持，属可选允许项）
SECURE_TLS13_CIPHERSUITES = ":".join([
    "TLS_AES_128_GCM_SHA256",
    "TLS_AES_256_GCM_SHA384",
    "TLS_CHACHA20_POLY1305_SHA256",
    "TLS_AES_128_CCM_SHA256",
])


def build_secure_ssl_context() -> ssl.SSLContext:
    """构造收敛到套件白名单的默认 TLS 客户端上下文（TLS 1.2–1.3）。

    用途：jiuwen 侧少量直连 TLS 出网（search/audio/image/video 等 requests
    工具）如需严格合规，经 ``SecureTLSAdapter`` 挂到 requests.Session。
    绝大多数出网已经桌面主进程命名管道代理（TLS 终止在主进程），不必经此。
    """
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.maximum_version = ssl.TLSVersion.TLSv1_3
    ctx.set_ciphers(SECURE_TLS12_CIPHERS)
    try:
        # TLS 1.3 套件独立于 set_ciphers 控制（Python 3.13+；旧版本无此 API 时
        # 依赖 OpenSSL 默认 1.3 列表——其三项均已在白名单内）
        ctx.set_ciphersuites(SECURE_TLS13_CIPHERSUITES)  # type: ignore[attr-defined]
    except AttributeError:
        pass
    return ctx


def mount_secure_adapter(session: "object") -> None:
    """把白名单套件上下文挂到 requests.Session（https:// 前缀）。

    用法：``session = requests.Session(); mount_secure_adapter(session)``。
    依赖 requests 的 HTTPAdapter 机制；requests 不可用时静默失败（调用方
    本来就是 requests 场景）。
    """
    try:
        from requests.adapters import HTTPAdapter
    except ImportError:
        return

    class _SecureTLSAdapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            kwargs["ssl_context"] = build_secure_ssl_context()
            return super().init_poolmanager(*args, **kwargs)

        def proxy_manager_for(self, proxy, **proxy_kwargs):
            proxy_kwargs["ssl_context"] = build_secure_ssl_context()
            return super().proxy_manager_for(proxy, **proxy_kwargs)

    session.mount("https://", _SecureTLSAdapter())
