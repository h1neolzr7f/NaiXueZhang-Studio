"""Outbound URL policy for local services that carry secrets or act as SSRF clients.

Default product threat model is 127.0.0.1. Still block open credential-forwarding
to arbitrary hosts when settings/token fields are attacker-controlled on the same machine.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# OpenAI-compatible / known LLM bases (host only, lowercased).
_AI_API_HOST_ALLOW = frozenset(
    {
        "api.openai.com",
        "api.deepseek.com",
        "api.x.ai",
        "api.anthropic.com",
        "generativelanguage.googleapis.com",
        "openrouter.ai",
        "api.idlecloud.cc",
        "nai3.idlecloud.cc",
        "api.novelai.net",
        "image.novelai.net",
    }
)

# Image CDN hosts used by providers after generation.
_IMAGE_HOST_ALLOW = frozenset(
    {
        "i.pximg.net",
        "img-original.pximg.net",
        "s.pximg.net",
        "api.idlecloud.cc",
        "nai3.idlecloud.cc",
        "cdn.idlecloud.cc",
        "image.novelai.net",
        "api.novelai.net",
    }
)

_PRIVATE_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


def _host_from_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else f"https://{text}")
    host = (parsed.hostname or "").strip().lower()
    return host


def _is_blocked_ip(host: str) -> bool:
    if not host:
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # Hostname — resolve and check all A/AAAA if possible.
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return False  # unknown host; allowlist host names handle this
        for info in infos:
            raw = info[4][0]
            try:
                addr = ipaddress.ip_address(raw)
            except ValueError:
                continue
            if any(addr in net for net in _PRIVATE_NETWORKS):
                return True
        return False
    return any(addr in net for net in _PRIVATE_NETWORKS)


def validate_ai_api_base(url: str, *, allow_empty: bool = True) -> str:
    """Return normalized HTTPS api base or raise ValueError."""
    text = str(url or "").strip().rstrip("/")
    if not text:
        if allow_empty:
            return ""
        raise ValueError("api_base is required")
    if "://" not in text:
        text = f"https://{text}"
    parsed = urlparse(text)
    if parsed.scheme.lower() != "https":
        raise ValueError("api_base must use HTTPS")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("api_base host is missing")
    if _is_blocked_ip(host):
        raise ValueError("api_base cannot target private/link-local addresses")
    # Allow exact host or subdomain of known providers; also common *.openai.azure.com style.
    allowed = (
        host in _AI_API_HOST_ALLOW
        or any(host.endswith("." + h) for h in _AI_API_HOST_ALLOW)
        or host.endswith(".openai.azure.com")
        or host.endswith(".deepseek.com")
        or host.endswith(".x.ai")
    )
    if not allowed:
        raise ValueError(
            f"api_base host '{host}' is not in the allowlist "
            f"(known LLM providers only)"
        )
    return text


def validate_provider_api_base(url: str, *, provider: str = "") -> str:
    """Validate optional per-token api_base (Xianyun/NAI overrides)."""
    text = str(url or "").strip().rstrip("/")
    if not text:
        return ""
    return validate_ai_api_base(text, allow_empty=False)


def validate_outbound_proxy(proxy: str) -> str:
    """Optional HTTP(S) proxy URL; block empty-scheme and private-only abuse."""
    text = str(proxy or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"http://{text}"
    parsed = urlparse(text)
    if parsed.scheme.lower() not in {"http", "https", "socks5", "socks5h"}:
        raise ValueError("proxy scheme must be http/https/socks5")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("proxy host is missing")
    # Proxies are often local (127.0.0.1:7890); allow loopback for proxy only.
    if host in {"localhost", "127.0.0.1", "::1"}:
        return text
    if _is_blocked_ip(host) and not host.startswith("192.168.") and not host.startswith("10."):
        # Allow common LAN proxies (home/lab) but block metadata IP.
        try:
            addr = ipaddress.ip_address(host)
            if addr in ipaddress.ip_network("169.254.0.0/16") or str(addr) == "0.0.0.0":
                raise ValueError("proxy cannot target link-local/metadata addresses")
        except ValueError as exc:
            if "proxy cannot" in str(exc):
                raise
    return text


def validate_image_download_url(url: str) -> str:
    """Allow only HTTPS image hosts used by generation providers."""
    text = str(url or "").strip()
    if not text:
        raise ValueError("image_url is empty")
    parsed = urlparse(text)
    if parsed.scheme.lower() != "https":
        raise ValueError("image_url must use HTTPS")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("image_url host is missing")
    if _is_blocked_ip(host):
        raise ValueError("image_url cannot target private/link-local addresses")
    allowed = host in _IMAGE_HOST_ALLOW or any(
        host.endswith("." + h) for h in _IMAGE_HOST_ALLOW
    )
    # Also allow common CDN patterns for idlecloud/novelai assets.
    if not allowed:
        if host.endswith(".idlecloud.cc") or host.endswith(".novelai.net"):
            allowed = True
    if not allowed:
        raise ValueError(f"image_url host '{host}' is not allowlisted")
    return text
