"""P value text and significance summaries ("ns", "*", ...).

Stars are decided on the unrounded P, with ≤ at every cutoff.

- ``"classic"`` (the default; the four-star P value thresholds): ns when P > 0.05,
  * ≤ 0.05, ** ≤ 0.01, *** ≤ 0.001, **** ≤ 0.0001. P has 4 decimals, "<0.0001" below that.
- ``"APA"``: the same cutoffs, stopping at *** (≤ 0.001). P has 3 decimals and no leading zero (".123", "<.001").
- ``"NEJM"``: as APA, but P keeps the leading zero ("0.123", "<0.001").

"""

from __future__ import annotations

import math

STYLES = ("classic", "APA", "NEJM")

# (bound, stars): P at or below the bound earns the stars. Tightest first.
_CUTOFFS = {
    "classic": [(0.0001, "****"), (0.001, "***"), (0.01, "**"), (0.05, "*")],
    "APA": [(0.001, "***"), (0.01, "**"), (0.05, "*")],
    "NEJM": [(0.001, "***"), (0.01, "**"), (0.05, "*")],
}
_DIGITS = {"classic": 4, "APA": 3, "NEJM": 3}


def _style(style: str) -> str:
    if style.lower() == "gp":  # the old key for "classic", still read from saved projects
        return "classic"
    for name in STYLES:
        if style.lower() == name.lower():
            return name
    raise ValueError(f"Unknown P value style {style!r}; choose one of {', '.join(STYLES)}.")


def p_summary(p: float, style: str = "classic") -> str:
    """Stars for a P value ("ns" when not significant, "" when P is missing)."""

    if p is None or not math.isfinite(p):
        return ""
    for bound, stars in _CUTOFFS[_style(style)]:
        if p <= bound:
            return stars
    return "ns"


def format_p(p: float, digits: int | None = None, style: str = "classic") -> str:
    """P as text: ``digits`` decimals (the style's default when None), and "<0.0001"-style text below
    the smallest value that many decimals can show. P that rounds to 1 shows as ">0.9999"."""

    style = _style(style)
    if p is None or not math.isfinite(p):
        return ""
    digits = _DIGITS[style] if digits is None else int(digits)
    if digits < 1:
        raise ValueError("digits must be at least 1.")
    smallest = 10.0 ** -digits
    if p < smallest:
        text = f"<{smallest:.{digits}f}"
    elif round(p, digits) >= 1.0:
        text = f">{1 - smallest:.{digits}f}"
    else:
        text = f"{p:.{digits}f}"
    if style == "APA":
        text = text.replace("0.", ".", 1)
    return text


def p_text(p: float, style: str = "classic", digits: int | None = None) -> str:
    """P as it reads in a sentence: "P = 0.0123" or "P < 0.0001" (APA: "p = .012")."""

    text = format_p(p, digits, style)
    if not text:
        return ""
    letter = "p" if _style(style) == "APA" else "P"
    if text[0] in "<>":
        return f"{letter} {text[0]} {text[1:]}"
    return f"{letter} = {text}"
