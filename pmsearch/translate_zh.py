from __future__ import annotations

import time
from typing import Callable

from deep_translator import GoogleTranslator


def _chunk_text(text: str, max_len: int = 4500) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_len:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        parts.append(text[start : start + max_len])
        start += max_len
    return parts


_LANG_TO_GOOGLE: dict[str, str] = {
    "zh": "zh-CN",
    "jap": "ja",
}


def translate_abstract(
    text: str,
    lang: str,
    *,
    source: str = "en",
    sleep_s: float = 0.35,
    on_progress: Callable[[int, int], None] | None = None,
) -> str:
    """
    Translate English abstract to ``zh`` (Chinese) or ``jap`` (Japanese).
    ``no`` returns empty string.
    """
    if lang == "no":
        return ""
    target = _LANG_TO_GOOGLE.get(lang, "zh-CN")
    chunks = _chunk_text(text)
    if not chunks:
        return ""
    translator = GoogleTranslator(source=source, target=target)
    out: list[str] = []
    total = len(chunks)
    for i, chunk in enumerate(chunks):
        try:
            out.append(translator.translate(chunk))
        except Exception:
            out.append("")
        if on_progress:
            on_progress(i + 1, total)
        if i + 1 < total:
            time.sleep(sleep_s)
    return "\n".join(out).strip()


def translate_to_chinese(
    text: str,
    *,
    source: str = "en",
    sleep_s: float = 0.35,
    on_progress: Callable[[int, int], None] | None = None,
) -> str:
    """将英文摘要译为简体中文；失败时返回空字符串。"""
    return translate_abstract(
        text,
        "zh",
        source=source,
        sleep_s=sleep_s,
        on_progress=on_progress,
    )
