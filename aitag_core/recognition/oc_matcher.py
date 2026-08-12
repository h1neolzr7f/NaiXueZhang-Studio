from __future__ import annotations

from aitag_core.prompt import tokenize_prompt

from .contract import OcMatch


def caption_token_set(caption: str) -> set[str]:
    return {t.raw.strip().lower() for t in tokenize_prompt(caption) if t.raw.strip()}


def caption_similarity(a: str, b: str) -> float:
    aa = caption_token_set(a)
    bb = caption_token_set(b)
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / max(1, min(len(aa), len(bb)))


def match_oc_preset(slot_caption: str, preset: dict) -> OcMatch:
    cap = str(slot_caption or "").strip()
    oc_cap = str((preset or {}).get("char_caption") or "").strip()
    label = str((preset or {}).get("label") or "")
    if not cap or not oc_cap:
        return OcMatch()

    cap_low = cap.lower()
    oc_low = oc_cap.lower()
    cap_tokens = caption_token_set(cap_low)
    identity_tokens = {
        str(t).strip().lower()
        for t in ((preset or {}).get("identity") or [])
        if str(t).strip().lower().endswith("_(oc)")
    }
    if identity_tokens and any(t in cap_tokens for t in identity_tokens):
        return OcMatch(True, label, _preview(oc_cap), "explicit-oc-token")
    if cap_low == oc_low:
        return OcMatch(True, label, _preview(oc_cap), "exact-caption")
    score = caption_similarity(cap, oc_cap)
    if score >= 0.78:
        return OcMatch(True, label, _preview(oc_cap), f"token-similarity:{score:.2f}")
    return OcMatch(False, "", "", f"token-similarity:{score:.2f}")


def _preview(text: str, max_len: int = 120) -> str:
    raw = str(text or "").strip()
    return raw[:max_len] + ("..." if len(raw) > max_len else "")

