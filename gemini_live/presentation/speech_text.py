"""Deterministic text forms used by TTS and CTC alignment.

The planner owns the reader-facing ``narration``.  This module derives two
machine-facing forms from it so a model never has to guess how ``96%`` or
``30.4°C`` should be pronounced or matched against speech audio.
"""

from __future__ import annotations

import re
import unicodedata


_DIGITS = (
    "không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín",
)


def number_to_vietnamese(value: int) -> str:
    """Spell a non-negative integer in a stable Vietnamese form.

    This deliberately favours predictable pronunciation for TTS/alignment
    over stylistic alternatives such as ``mốt`` or ``lăm``.
    """
    if value < 0:
        return f"âm {number_to_vietnamese(-value)}"
    if value < 10:
        return _DIGITS[value]
    if value < 100:
        tens, ones = divmod(value, 10)
        text = "mười" if tens == 1 else f"{_DIGITS[tens]} mươi"
        if ones == 0:
            return text
        if ones == 1 and tens > 1:
            return f"{text} mốt"
        if ones == 5:
            return f"{text} lăm"
        return f"{text} {_DIGITS[ones]}"
    if value < 1_000:
        hundreds, rest = divmod(value, 100)
        text = f"{_DIGITS[hundreds]} trăm"
        if rest == 0:
            return text
        if rest < 10:
            return f"{text} lẻ {_DIGITS[rest]}"
        return f"{text} {number_to_vietnamese(rest)}"
    if value < 1_000_000:
        thousands, rest = divmod(value, 1_000)
        text = f"{number_to_vietnamese(thousands)} nghìn"
        if rest == 0:
            return text
        if rest < 100:
            return f"{text} không trăm {number_to_vietnamese(rest)}"
        return f"{text} {number_to_vietnamese(rest)}"
    millions, rest = divmod(value, 1_000_000)
    text = f"{number_to_vietnamese(millions)} triệu"
    return text if rest == 0 else f"{text} {number_to_vietnamese(rest)}"


def numeric_to_vietnamese(raw: str) -> str:
    """Spell a decimal token whose dot/comma is a decimal separator."""
    normalized = raw.strip().replace(",", ".")
    sign = ""
    if normalized.startswith("-"):
        sign, normalized = "âm ", normalized[1:]
    integer, dot, fraction = normalized.partition(".")
    result = number_to_vietnamese(int(integer or "0"))
    if dot and fraction:
        result = f"{result} phẩy {' '.join(_DIGITS[int(digit)] for digit in fraction)}"
    return f"{sign}{result}"


def spoken_text(narration: str) -> str:
    """Create the Vietnamese text sent verbatim to a speech provider."""
    text = unicodedata.normalize("NFKC", narration)

    # Handle dates before generic number replacement so they remain meaningful.
    text = re.sub(
        r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b",
        lambda match: (
            f"ngày {number_to_vietnamese(int(match.group(3)))} "
            f"tháng {number_to_vietnamese(int(match.group(2)))} "
            f"năm {number_to_vietnamese(int(match.group(1)))}"
        ),
        text,
    )
    # Planner narration sometimes uses the compact Vietnamese day/month form
    # (for example 05/08).  Expand it before generic number replacement so a
    # speech model never has to infer whether a leading zero is meaningful.
    text = re.sub(
        r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b",
        lambda match: (
            f"ngày {number_to_vietnamese(int(match.group(1)))} "
            f"tháng {number_to_vietnamese(int(match.group(2)))}"
            + (f" năm {number_to_vietnamese(int(match.group(3)))}" if match.group(3) else "")
        ),
        text,
    )
    text = re.sub(
        r"\bngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})(?:\s+năm\s+(\d{4}))?",
        lambda match: (
            f"ngày {number_to_vietnamese(int(match.group(1)))} "
            f"tháng {number_to_vietnamese(int(match.group(2)))}"
            + (f" năm {number_to_vietnamese(int(match.group(3)))}" if match.group(3) else "")
        ),
        text,
        flags=re.IGNORECASE,
    )

    # Expand clock times before generic number replacement.  The weather
    # presentation uses these in hourly forecasts, and this form matches how
    # the chosen Gemini voice naturally reads Vietnamese time expressions.
    def _replace_clock(match: re.Match[str]) -> str:
        hour = number_to_vietnamese(int(match.group(1)))
        minute = int(match.group(2))
        if minute == 0:
            return f"{hour} giờ"
        return f"{hour} giờ {number_to_vietnamese(minute)} phút"

    text = re.sub(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", _replace_clock, text)

    decimal = r"-?\d+(?:[\.,]\d+)?"
    replacements = (
        (rf"({decimal})\s*%", "phần trăm"),
        (rf"({decimal})\s*°\s*C?", "độ C"),
        (rf"({decimal})\s+độ\s*C\b", "độ C"),
        (rf"({decimal})\s+mm\b", "mi li mét"),
        (rf"({decimal})\s*m/s\b", "mét trên giây"),
        (rf"({decimal})\s*hPa\b", "héc tô Pascal"),
        (rf"({decimal})\s*km/h\b", "ki lô mét trên giờ"),
    )
    for pattern, unit in replacements:
        text = re.sub(
            pattern,
            lambda match: f"{numeric_to_vietnamese(match.group(1))} {unit}",
            text,
            flags=re.IGNORECASE,
        )

    # Remaining values (for example "7 trên 7 ngày") are still audible words.
    return re.sub(decimal, lambda match: numeric_to_vietnamese(match.group(0)), text)


def alignment_text(spoken: str) -> str:
    """Return a lowercase, punctuation-free transcript for CTC matching."""
    # Gemini pronounces the standalone temperature unit ``C`` as "xê".  CTC
    # aligns sound, not spelling, so its transcript must use that phonetic
    # form.  This only affects a standalone unit, never the letter inside a
    # Vietnamese word.
    phonetic = re.sub(r"(?<!\w)C(?!\w)", "xê", spoken)
    normalized = unicodedata.normalize("NFD", phonetic.lower().replace("đ", "d"))
    without_marks = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", without_marks)).strip()


def derive_speech_text(narration: str) -> tuple[str, str]:
    """Produce the provider text and its deterministic alignment transcript."""
    spoken = spoken_text(narration)
    return spoken, alignment_text(spoken)
