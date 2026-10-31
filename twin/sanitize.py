"""Handling for untrusted text (worker questions, news, tool output) before it reaches a model.

The model is told that anything between the data fences is information, never instructions.
These helpers make the fence hard to break out of and keep inputs bounded.
"""

from __future__ import annotations

import re
import unicodedata

FENCE_OPEN = "<<<UNTRUSTED_DATA>>>"
FENCE_CLOSE = "<<<END_UNTRUSTED_DATA>>>"

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FENCE_LIKE = re.compile(r"<<<\s*/?\s*(END_)?UNTRUSTED_DATA\s*>>>", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]{0,200}>")


def clean(text: str, limit: int = 2000) -> str:
    """Normalise, strip control characters, markup and fence look-alikes; cap the length."""
    text = unicodedata.normalize("NFKC", text)
    text = _CONTROL.sub(" ", text)
    text = _FENCE_LIKE.sub("[removed]", text)
    text = _TAGS.sub(" ", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def fence(text: str, limit: int = 2000) -> str:
    """Wrap cleaned untrusted text in data fences."""
    return f"{FENCE_OPEN}\n{clean(text, limit)}\n{FENCE_CLOSE}"
