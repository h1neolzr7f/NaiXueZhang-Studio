"""Windows user-bound DPAPI protection for secrets stored by the desktop app."""

from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes


PREFIX = "dpapi:v1:"

UNAVAILABLE_MESSAGE = (
    "无法在非 Windows 系统加密保存密钥。NovelAI Token 与 Pixiv refresh token "
    "仅在 Windows DPAPI 可用时写入 data/；拒绝明文落盘。"
)


class SecretProtectionUnavailable(ValueError):
    """Raised when a secret cannot be persisted without writing plaintext."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(payload: bytes) -> tuple[_DataBlob, object]:
    buffer = ctypes.create_string_buffer(payload)
    return _DataBlob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def protection_unavailable_reason() -> str:
    if os.name == "nt":
        return ""
    return UNAVAILABLE_MESSAGE


def protect_secret(value: str) -> str:
    text = str(value or "")
    if not text or text.startswith(PREFIX):
        return text
    if os.name != "nt":
        raise SecretProtectionUnavailable(UNAVAILABLE_MESSAGE)
    source, source_buffer = _blob(text.encode("utf-8"))
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "AITag Mirror Gallery",
        None,
        None,
        None,
        0,
        ctypes.byref(output),
    ):
        raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
    try:
        encrypted = ctypes.string_at(output.pbData, output.cbData)
        return PREFIX + base64.urlsafe_b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def unprotect_secret(value: str) -> str:
    text = str(value or "")
    if not text.startswith(PREFIX):
        return text
    if os.name != "nt":
        raise RuntimeError("This secret is protected for a Windows user account")
    try:
        encrypted = base64.urlsafe_b64decode(text[len(PREFIX) :].encode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid DPAPI secret encoding") from exc
    source, source_buffer = _blob(encrypted)
    output = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)
    ):
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
    try:
        return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
