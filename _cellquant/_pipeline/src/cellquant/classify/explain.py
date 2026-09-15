"""Rule-derived explanations for nuclear marker calls."""

from __future__ import annotations

from numbers import Real

import numpy as np
import pandas as pd

from cellquant.classify import _boundaries


_REASON_TEXT = {
    "not_acquired": "Measurement is missing because this marker channel was not acquired.",
    "nonfinite_pixels": "Measurement is missing because one or more pixels in the nucleus are non-finite.",
    "outside_region": "Object is excluded because it is outside the counting region.",
}


def explain_marker_call(marker_spec: dict, row) -> str:
    """Explain one cell call using the same rule representation as scoring.

    ``row`` may be a mapping, Series, or object with call/fraction/reason fields.
    """
    if marker_spec is None or not isinstance(marker_spec, dict):
        raise ValueError("marker_spec must be a mapping")
    call = _field(row, "call")
    reason = _field(row, "reason")
    fraction = _field(row, "fraction")
    if reason:
        known = _REASON_TEXT.get(str(reason))
        if known is not None:
            return known
        return f"Measurement is missing ({reason})."

    if marker_spec.get("channel") is None:
        return _REASON_TEXT["not_acquired"]

    cutoff = float(marker_spec["positive_fraction"])
    margin = float(marker_spec.get("uncertainty_margin") or 0)
    lower, upper = _boundaries(cutoff, margin)
    if fraction is None or (isinstance(fraction, Real) and not np.isfinite(fraction)):
        return "Measured fraction is unavailable; a generic missing reason was recorded."

    pct = 100.0 * float(fraction)
    required_pct = 100.0 * float(cutoff)
    lower_pct = 100.0 * float(lower)
    upper_pct = 100.0 * float(upper)
    call_text = str(call or "unknown")
    base = (
        f"{pct:.0f}% of the measured region exceeds the intensity threshold; "
        f"the required fraction is {required_pct:.0f}%."
    )
    if margin > 0:
        base += (
            f" Calls below {lower_pct:.0f}% are negative, "
            f"{lower_pct:.0f}%–{upper_pct:.0f}% are uncertain, "
            f"and at or above {upper_pct:.0f}% are positive."
        )
    return f"{base} Call: {call_text}."


def _field(row, name):
    if isinstance(row, pd.Series):
        return row.get(name)
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)
