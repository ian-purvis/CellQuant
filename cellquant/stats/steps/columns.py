"""Show working for columns (see cellquant.stats.steps): two-column comparisons, one-way ANOVA and its nonparametric forms,
the multiple comparisons that follow them, nested analyses, outliers, column statistics and normality tests."""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from cellquant.stats.results import fmt
from cellquant.stats.steps import Block, register

NAN = float("nan")
SUBSCRIPTS = str.maketrans("0123456789-", "₀₁₂₃₄₅₆₇₈₉₋")
EXACT_LIMIT = 200_000  # most arrangements that are enumerated to get an exact P here


# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------


def _g(x: Any, digits: int = 5) -> str:
    """A number for a formula: ``digits`` significant digits, 'undefined' for NaN."""

    try:
        x = float(x)
    except (TypeError, ValueError):
        return str(x)
    return "undefined" if math.isnan(x) else fmt(x, digits)


def _sub(x: Any) -> str:
    """A number as a subscript (degrees of freedom)."""

    return _g(x, 4).translate(SUBSCRIPTS)


def _div(a: float, b: float) -> float:
    return a / b if b and math.isfinite(a) and math.isfinite(b) else NAN


def _ev(result: Any, key: str) -> Any:
    """The engine's value under ``key`` (None when it does not report one)."""

    value = (getattr(result, "values", None) or {}).get(key)
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    return None


def _row_value(result: Any, section: str, label: str) -> Any:
    try:
        return result.row(section, label).value
    except (KeyError, AttributeError):
        return None


def _opt(result: Any, options: dict, key: str, default: Any) -> Any:
    """An option as the engine used it (its result keeps those), else the sheet's option, else the default."""

    for source in (getattr(result, "options", None) or {}, options):
        if source.get(key) is not None:
            return source[key]
    return default


def _used(result: Any, key: str, default: Any) -> Any:
    """A setting the engine recorded in its result (settings the sheet's dialog does not pass are not read from the options)."""

    value = (getattr(result, "options", None) or {}).get(key)
    return default if value is None else value


def _alternative(result: Any, options: dict) -> str:
    alt = _opt(result, options, "alternative", "two-sided")
    return {"two-tailed": "two-sided", "two_sided": "two-sided"}.get(alt, alt)


def _by_engine(block: Block, label: str, how: str, engine: Any) -> None:
    """A quantity too involved to redo here: the engine's value, said to be the engine's."""

    block.add(label, f"computed by {how}", engine)


def _t_tail(t: float, df: float, alt: str) -> tuple[float, str]:
    """The P value of a t statistic and the formula that gives it."""

    if not (math.isfinite(t) and df > 0):
        return NAN, "undefined"
    if alt == "greater":
        return float(stats.t.sf(t, df)), f"P(T{_sub(df)} > {_g(t)})"
    if alt == "less":
        return float(stats.t.cdf(t, df)), f"P(T{_sub(df)} < {_g(t)})"
    return float(min(1.0, 2.0 * stats.t.sf(abs(t), df))), f"2 · P(T{_sub(df)} > {_g(abs(t))})"


def _z_tail(z: float, alt: str) -> tuple[float, str]:
    if not math.isfinite(z):
        return NAN, "undefined"
    if alt == "greater":
        return float(stats.norm.sf(z)), f"P(Z > {_g(z)})"
    if alt == "less":
        return float(stats.norm.cdf(z)), f"P(Z < {_g(z)})"
    return float(min(1.0, 2.0 * stats.norm.sf(abs(z)))), f"2 · P(Z > {_g(abs(z))})"


def _finite(values: Any) -> np.ndarray:
    array = np.asarray(values, float).ravel()
    return array[np.isfinite(array)]


def _column(table: Any, key: Any, default: int) -> Any:
    if isinstance(key, str) and key in table.names:
        return table[key]
    if isinstance(key, int) and not isinstance(key, bool) and 0 <= key < len(table):
        return table[key]
    return table[default]


def _hedges_j(df: float) -> float:
    if not df > 1:
        return NAN
    return math.exp(math.lgamma(df / 2) - 0.5 * math.log(df / 2) - math.lgamma((df - 1) / 2))


def _tie_sum(values: np.ndarray) -> float:
    _, counts = np.unique(values, return_counts=True)
    return float(np.sum(counts.astype(float) ** 3 - counts))


def _df_setting(options: dict, key: str, conservative: float) -> float | None:
    """The degrees of freedom set by hand (a number, or "conservative"), as the options hold it."""

    value = options.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        return float(conservative)
    return float(value)


def _frame(**columns: Any) -> pd.DataFrame:
    return pd.DataFrame(columns)


# ---------------------------------------------------------------------------------------------
# Two columns: t tests
# ---------------------------------------------------------------------------------------------


def _group_block(title: str, label: str, x: np.ndarray, ev, source: np.ndarray | None = None, log: bool = False) -> Block:
    """n, mean, sum of squared deviations, variance and SD of one group (of the logs when ``log``)."""

    n = x.size
    mean = float(x.mean()) if n else NAN
    ss = float(np.sum((x - mean) ** 2)) if n else NAN
    var = ss / (n - 1) if n > 1 else NAN
    block = Block(title)
    what = "log10 values" if log else "values"
    block.add(f"Number of {what}, n{label}", "count of non-blank cells", n, ev("n_" + label.lower()))
    block.add(f"Mean, x̄{label}", f"Σx / n = {_g(x.sum())} / {n}", mean, ev("mean_" + label.lower()))
    block.add(f"Sum of squared deviations, SS{label}", "Σ(x − x̄)²", ss)
    block.add(f"Variance, s{label}²", f"SS / (n − 1) = {_g(ss)} / {n - 1}", var)
    block.add(f"Standard deviation, s{label}", f"√s² = √{_g(var)}", math.sqrt(var) if var >= 0 else NAN, ev("sd_" + label.lower()))
    columns = {"x": x if source is None else source}
    if log:
        columns["log10 x"] = x
    columns.update({"x − x̄": x - mean, "(x − x̄)²": (x - mean) ** 2})
    block.table = pd.DataFrame(columns)
    return block


def _variance_f_block(a: np.ndarray, b: np.ndarray, names: tuple[str, str], result: Any, suffix: str = "") -> Block | None:
    if a.size < 2 or b.size < 2:
        return None
    va, vb = float(np.var(a, ddof=1)), float(np.var(b, ddof=1))
    if not (va > 0 and vb > 0):
        return None
    block = Block("F test to compare variances" + suffix, note="F is the larger variance over the smaller one, so F ≥ 1; the two-tailed P doubles the upper tail.")
    if vb >= va:
        f, dfn, dfd, top = vb / va, b.size - 1, a.size - 1, names[1]
    else:
        f, dfn, dfd, top = va / vb, a.size - 1, b.size - 1, names[0]
    block.add("F", f"larger s² / smaller s² = {_g(max(va, vb))} / {_g(min(va, vb))} ({top} is larger)", f, _ev(result, "f"))
    block.add("Numerator df", f"n − 1 of the larger-variance group = {dfn}", dfn, _ev(result, "f_dfn"))
    block.add("Denominator df", f"n − 1 of the other group = {dfd}", dfd, _ev(result, "f_dfd"))
    p = float(min(1.0, 2.0 * stats.f.sf(f, dfn, dfd)))
    block.add("P value", f"2 · P(F{_sub(dfn)},{_sub(dfd)} > {_g(f)})", p, _ev(result, "f_p"))
    return block


def _two_sample_blocks(a: np.ndarray, b: np.ndarray, names: tuple[str, str], welch: bool, alt: str, ci: float, df_set: float | None, null: float,
                       result: Any, pre: str = "", log: bool = False, raw: tuple[np.ndarray, np.ndarray] | None = None) -> list[Block]:
    """Unpaired t test on a and b (logs when ``log``): groups, SE, t, df, P, CI, R²."""

    def ev(key: str):
        return _ev(result, pre + key)

    na, nb = a.size, b.size
    blocks = [_group_block(f"Group A: {names[0]}", "A", a, ev, raw[0] if raw else None, log),
              _group_block(f"Group B: {names[1]}", "B", b, ev, raw[1] if raw else None, log)]
    va = float(np.var(a, ddof=1)) if na > 1 else NAN
    vb = float(np.var(b, ddof=1)) if nb > 1 else NAN
    test = Block("Welch's t test" if welch else "Unpaired t test", note="Every difference is B − A.")
    if welch:
        sa2, sb2 = va / na, vb / nb
        test.add("Variance of the mean of A", f"s_A² / n_A = {_g(va)} / {na}", sa2)
        test.add("Variance of the mean of B", f"s_B² / n_B = {_g(vb)} / {nb}", sb2)
        se = math.sqrt(sa2 + sb2)
        test.add("Standard error of the difference, SE", f"√(s_A²/n_A + s_B²/n_B) = √({_g(sa2)} + {_g(sb2)})", se, ev("se"))
        df_calc = (sa2 + sb2) ** 2 / (sa2**2 / (na - 1) + sb2**2 / (nb - 1)) if se > 0 else NAN
        df_formula = f"(s_A²/n_A + s_B²/n_B)² / ((s_A²/n_A)²/(n_A−1) + (s_B²/n_B)²/(n_B−1)) = {_g(df_calc)}"
    else:
        df_calc = na + nb - 2
        pooled = ((na - 1) * (0 if math.isnan(va) else va) + (nb - 1) * (0 if math.isnan(vb) else vb)) / df_calc
        test.add("Pooled variance, s_p²", f"((n_A−1)s_A² + (n_B−1)s_B²) / (n_A+n_B−2) = ({na - 1}·{_g(va)} + {nb - 1}·{_g(vb)}) / {df_calc}", pooled)
        se = math.sqrt(pooled * (1 / na + 1 / nb))
        test.add("Standard error of the difference, SE", f"s_p · √(1/n_A + 1/n_B) = √({_g(pooled)}) · √(1/{na} + 1/{nb})", se, ev("se"))
        df_formula = f"n_A + n_B − 2 = {na} + {nb} − 2"
    df = df_calc if df_set is None else df_set
    diff = float(b.mean() - a.mean())
    test.add("Difference of means, x̄_B − x̄_A", f"{_g(b.mean())} − {_g(a.mean())}", diff, ev("difference"))
    if null != 0.0:
        test.add("Hypothesised difference, Δ₀", "set by hand under Advanced", null)
    t = _div(diff - null, se) if se > 0 else NAN
    test.add("t", f"(difference − Δ₀) / SE = ({_g(diff)} − {_g(null)}) / {_g(se)}" if null else f"difference / SE = {_g(diff)} / {_g(se)}", t, ev("t"))
    if df_set is None:
        test.add("Degrees of freedom, df", df_formula, df, ev("df"))
    else:
        test.add("Degrees of freedom, df", f"set by hand under Advanced (computed {_g(df_calc)})", df, ev("df"))
    p, formula = _t_tail(t, df, alt)
    test.add("P value", formula, p, _ev(result, "p"))
    crit = float(stats.t.ppf(1.0 - (1.0 - ci) / 2.0, df)) if df > 0 else NAN
    test.add(f"Critical value, t* ({100 * ci:g}% CI)", f"t({_g(1 - (1 - ci) / 2)}, df = {_g(df)})", crit)
    half = crit * se
    test.add("Half-width of the CI", f"t* · SE = {_g(crit)} · {_g(se)}", half)
    test.add("Lower limit of the CI", f"difference − half-width = {_g(diff)} − {_g(half)}", diff - half, ev("ci_low"))
    test.add("Upper limit of the CI", f"difference + half-width = {_g(diff)} + {_g(half)}", diff + half, ev("ci_high"))
    r2 = t * t / (t * t + df) if math.isfinite(t) else NAN
    test.add("R² (eta squared)", f"t² / (t² + df) = {_g(t * t)} / ({_g(t * t)} + {_g(df)})", r2, ev("r_squared"))
    blocks.append(test)
    return blocks


def _effect_block(a: np.ndarray, b: np.ndarray, result: Any) -> Block | None:
    na, nb = a.size, b.size
    if na < 2 or nb < 2:
        return None
    va, vb = float(np.var(a, ddof=1)), float(np.var(b, ddof=1))
    pooled = math.sqrt(((na - 1) * va + (nb - 1) * vb) / (na + nb - 2))
    diff = float(b.mean() - a.mean())
    block = Block("Effect sizes")
    block.add("Pooled SD, s_p", f"√(((n_A−1)s_A² + (n_B−1)s_B²) / (n−2)) = √(({na - 1}·{_g(va)} + {nb - 1}·{_g(vb)}) / {na + nb - 2})", pooled)
    d = _div(diff, pooled) if pooled > 0 else NAN
    block.add("Cohen's d", f"(x̄_B − x̄_A) / s_p = {_g(diff)} / {_g(pooled)}", d, _ev(result, "cohen_d"))
    j = _hedges_j(na + nb - 2)
    block.add("Correction factor, J", f"Γ(ν/2) / (√(ν/2) · Γ((ν−1)/2)), ν = n − 2 = {na + nb - 2}", j)
    block.add("Hedges' g", f"J · d = {_g(j)} · {_g(d)}", j * d, _ev(result, "hedges_g"))
    block.add("Glass's Δ", f"(x̄_B − x̄_A) / s_A = {_g(diff)} / {_g(math.sqrt(va))}", _div(diff, math.sqrt(va)) if va > 0 else NAN, _ev(result, "glass_delta"))
    return block


def _unpaired(table: Any, options: dict, result: Any, test: str) -> list[Block]:
    ca, cb = _column(table, options.get("first", 0), 0), _column(table, options.get("second", 1), 1)
    names = (ca.name, cb.name)
    a, b = ca.finite, cb.finite
    alt, ci = _alternative(result, options), float(_opt(result, options, "ci", 0.95))
    log = test == "lognormal_t"
    welch = test == "welch_t" or (log and bool(_used(result, "welch", False)))
    df_set = _df_setting(options, "df", min(a.size, b.size) - 1)
    null = 0.0 if log else float(options.get("difference") or 0.0)
    blank = ca.values.size - a.size + cb.values.size - b.size
    head = Block("Data used", note="Blank cells are left out of each column.")
    head.add("Blank cells left out", "cells without a number in the two columns", blank)
    if log:
        la, lb = np.log10(a), np.log10(b)
        blocks = [head, *_two_sample_blocks(la, lb, names, welch, alt, ci, df_set, 0.0, result, "log_", True, (a, b))]
        ratio_block = Block("Back-transformed (ratio of geometric means)")
        ma, mb = float(la.mean()), float(lb.mean())
        diff = mb - ma
        ratio_block.add("Geometric mean of A", f"10^(mean of log10) = 10^{_g(ma)}", 10**ma, _ev(result, "geomean_a"))
        ratio_block.add("Geometric mean of B", f"10^{_g(mb)}", 10**mb, _ev(result, "geomean_b"))
        ratio_block.add("Ratio of geometric means, B/A", f"10^(difference of log10 means) = 10^{_g(diff)}", 10**diff, _ev(result, "ratio"))
        ratio_block.add("Lower limit of the CI of the ratio", "10^(lower limit of the CI of the log difference)", 10 ** _lower(blocks[-1]), _ev(result, "ratio_ci_low"))
        ratio_block.add("Upper limit of the CI of the ratio", "10^(upper limit of the CI of the log difference)", 10 ** _upper(blocks[-1]), _ev(result, "ratio_ci_high"))
        blocks.append(ratio_block)
        if not welch and (f_block := _variance_f_block(la, lb, names, result, " (on the logs)")):
            blocks.append(f_block)
        return blocks
    blocks = [head, *_two_sample_blocks(a, b, names, welch, alt, ci, df_set, null, result)]
    if effect := _effect_block(a, b, result):
        blocks.append(effect)
    if not welch and (f_block := _variance_f_block(a, b, names, result)):
        blocks.append(f_block)
    return blocks


def _lower(block: Block) -> float:
    return next(step.value for step in block.steps if step.label.startswith("Lower limit"))


def _upper(block: Block) -> float:
    return next(step.value for step in block.steps if step.label.startswith("Upper limit"))


def _paired_rows(table: Any, options: dict) -> tuple[Any, Any, np.ndarray, np.ndarray, np.ndarray, int]:
    """The two columns padded to the same length, the rows where both have a value (a, b, 1-based row numbers) and the rows dropped."""

    ca, cb = _column(table, options.get("first", 0), 0), _column(table, options.get("second", 1), 1)
    size = max(ca.values.size, cb.values.size)
    pad = lambda v: np.concatenate([v, np.full(size - v.size, np.nan)])  # noqa: E731
    va, vb = pad(ca.values), pad(cb.values)
    keep = np.isfinite(va) & np.isfinite(vb)
    dropped = int((np.isfinite(va) | np.isfinite(vb)).sum() - keep.sum())
    return ca, cb, va[keep], vb[keep], np.flatnonzero(keep) + 1, dropped


def _pairing_block(a: np.ndarray, b: np.ndarray, result: Any, spearman: bool = False) -> Block | None:
    """Was the pairing effective? r (or rs) between the columns and its one-tailed P."""

    n = a.size
    if n < 3 or np.all(a == a[0]) or np.all(b == b[0]):
        return None
    x, y = (stats.rankdata(a), stats.rankdata(b)) if spearman else (a, b)
    sxy = float(np.sum((x - x.mean()) * (y - y.mean())))
    r = sxy / math.sqrt(float(np.sum((x - x.mean()) ** 2)) * float(np.sum((y - y.mean()) ** 2)))
    block = Block("How effective was the pairing?", note="Spearman's rs is Pearson's r of the ranks." if spearman else "")
    block.add("Spearman correlation, rs" if spearman else "Correlation coefficient, r", "Σ(x − x̄)(y − ȳ) / √(Σ(x − x̄)² · Σ(y − ȳ)²)" + (" on the ranks" if spearman else ""),
              r, _ev(result, "pairing_r"))
    df = n - 2
    if r >= 1:
        p = 0.0
    else:
        p = float(stats.t.sf(r * math.sqrt(df) / math.sqrt(1 - r * r), df))
    block.add("P value (one tailed)", f"P(T{_sub(df)} > r·√(n−2) / √(1−r²)) = P(T{_sub(df)} > {_g(r * math.sqrt(df) / math.sqrt(max(1 - r * r, 1e-300)))})", p, _ev(result, "pairing_p"))
    return block


def _paired(table: Any, options: dict, result: Any, test: str) -> list[Block]:
    ca, cb, a, b, rows, dropped = _paired_rows(table, options)
    names = (ca.name, cb.name)
    alt, ci = _alternative(result, options), float(_opt(result, options, "ci", 0.95))
    log = test == "ratio_paired_t"
    pre = "log_" if log else ""
    ev = lambda key: _ev(result, pre + key)  # noqa: E731
    x, y = (np.log10(a), np.log10(b)) if log else (a, b)
    d = y - x
    n = d.size
    null = 0.0 if log else float(options.get("difference") or 0.0)
    df_calc = n - 1
    df_set = _df_setting(options, "df", df_calc)
    df = df_calc if df_set is None else df_set
    head = Block("Data used", note="A row needs a value in both columns; other rows are left out.")
    head.add("Rows with a blank in either column", "rows with only one of the two values", dropped)
    head.add("Number of pairs, n", "rows with both values", n, ev("n"))
    mean = float(d.mean())
    ss = float(np.sum((d - mean) ** 2))
    block = Block("Differences" + (" of the logs" if log else ""), note="d = B − A" + (" on log10 values, d = log10(B) − log10(A)" if log else "") + " for each pair.")
    columns = {"row": rows, "A": a, "B": b}
    if log:
        columns.update({"log10 A": x, "log10 B": y})
    columns.update({"d": d, "d − d̄": d - mean, "(d − d̄)²": (d - mean) ** 2})
    block.table = pd.DataFrame(columns)
    block.add("Mean of A" + (" (log10)" if log else ""), f"Σx / n = {_g(x.sum())} / {n}", float(x.mean()), ev("mean_a"))
    block.add("Mean of B" + (" (log10)" if log else ""), f"Σy / n = {_g(y.sum())} / {n}", float(y.mean()), ev("mean_b"))
    block.add("Mean difference, d̄", f"Σd / n = {_g(d.sum())} / {n}", mean, ev("difference"))
    block.add("Sum of squared deviations, SS_d", "Σ(d − d̄)²", ss)
    sd = math.sqrt(ss / (n - 1)) if n > 1 else NAN
    block.add("SD of the differences, s_d", f"√(SS_d / (n − 1)) = √({_g(ss)} / {n - 1})", sd, ev("sd_difference"))
    se = sd / math.sqrt(n)
    block.add("SEM of the differences, SE", f"s_d / √n = {_g(sd)} / √{n}", se, ev("se"))
    test_block = Block("Paired t test" if not log else "Paired t test on the logs")
    if null != 0.0:
        test_block.add("Hypothesised difference, Δ₀", "set by hand under Advanced", null)
    t = _div(mean - null, se) if se > 0 else NAN
    test_block.add("t", f"(d̄ − Δ₀) / SE = ({_g(mean)} − {_g(null)}) / {_g(se)}" if null else f"d̄ / SE = {_g(mean)} / {_g(se)}", t, ev("t"))
    test_block.add("Degrees of freedom, df", f"n − 1 = {df_calc}" if df_set is None else f"set by hand under Advanced (computed {df_calc})", df, ev("df"))
    p, formula = _t_tail(t, df, alt)
    test_block.add("P value", formula, p, _ev(result, "p"))
    crit = float(stats.t.ppf(1.0 - (1.0 - ci) / 2.0, df)) if df > 0 else NAN
    half = crit * se
    test_block.add(f"Critical value, t* ({100 * ci:g}% CI)", f"t({_g(1 - (1 - ci) / 2)}, df = {_g(df)})", crit)
    test_block.add("Lower limit of the CI", f"d̄ − t*·SE = {_g(mean)} − {_g(crit)}·{_g(se)}", mean - half, ev("ci_low"))
    test_block.add("Upper limit of the CI", f"d̄ + t*·SE = {_g(mean)} + {_g(crit)}·{_g(se)}", mean + half, ev("ci_high"))
    test_block.add("R² (partial eta squared)", f"t² / (t² + df) = {_g(t * t)} / ({_g(t * t)} + {_g(df)})", t * t / (t * t + df) if math.isfinite(t) else NAN, ev("r_squared"))
    blocks = [head, block, test_block]
    if log:
        back = Block("Back-transformed (geometric mean of the ratios)")
        back.add("Geometric mean ratio, B/A", f"10^(mean log difference) = 10^{_g(mean)}", 10**mean, _ev(result, "ratio"))
        back.add("Lower limit of the CI", "10^(lower limit of the CI of the mean log difference)", 10 ** (mean - half), _ev(result, "ratio_ci_low"))
        back.add("Upper limit of the CI", "10^(upper limit)", 10 ** (mean + half), _ev(result, "ratio_ci_high"))
        blocks.append(back)
    else:
        effect = Block("Effect sizes")
        sa = float(np.std(a, ddof=1)) if n > 1 else NAN
        dz = _div(mean, sd) if sd > 0 else NAN
        j = _hedges_j(n - 1)
        effect.add("Cohen's d_z", f"d̄ / s_d = {_g(mean)} / {_g(sd)}", dz, _ev(result, "cohen_dz"))
        effect.add("Correction factor, J", f"Γ(ν/2) / (√(ν/2) · Γ((ν−1)/2)), ν = n − 1 = {n - 1}", j)
        effect.add("Hedges' g_z", f"J · d_z = {_g(j)} · {_g(dz)}", j * dz, _ev(result, "hedges_gz"))
        effect.add("Glass's Δ", f"d̄ / s_A = {_g(mean)} / {_g(sa)}", _div(mean, sa) if sa > 0 else NAN, _ev(result, "glass_delta"))
        blocks.append(effect)
    if pair := _pairing_block(x, y, result):
        blocks.append(pair)
    return blocks


# ---------------------------------------------------------------------------------------------
# Rank-based pieces shared by the two-column rank tests
# ---------------------------------------------------------------------------------------------


def _rank_sum_counts(m: int, n: int) -> list[int]:
    """Number of ways to get each U = 0..mn when n of m + n untied values are chosen: the coefficients of
    ∏ᵢ₌₁..ₙ (1 − q^(m+i)) / (1 − q^i), counted with whole numbers."""

    size = m * n + 1
    poly = np.zeros(size, dtype=object)
    poly[:] = 0
    poly[0] = 1
    for i in range(1, n + 1):
        shift = m + i
        if shift < size:
            poly[shift:] = poly[shift:] - poly[: size - shift]
        for start in range(i, size, i):
            poly[start:start + i] = poly[start:start + i] + poly[start - i:start - i + len(poly[start:start + i])]
    return [int(c) for c in poly]


def _signed_rank_counts(n: int) -> list[int]:
    """Number of ways to get each sum T+ = 0..n(n+1)/2 when each of the ranks 1..n is positive or negative."""

    top = n * (n + 1) // 2
    counts = [0] * (top + 1)
    counts[0] = 1
    for rank in range(1, n + 1):
        for s in range(top, rank - 1, -1):
            counts[s] += counts[s - rank]
    return counts


def _tails(cdf_counts: np.ndarray, total: Any, k: int) -> tuple[float, float]:
    """P(S ≤ k) and P(S ≥ k) from a list of counts of S = 0, 1, 2, …"""

    lower = sum(cdf_counts[: k + 1])
    upper = sum(cdf_counts[k:])
    return float(lower / total), float(upper / total)


def _order_ci(sorted_values: np.ndarray, counts: list[int] | None, total: int, sd: float, ci: float) -> tuple[int, float, float, float]:
    """The k whose k-th smallest and k-th largest values form the CI with confidence nearest to ci, its confidence and the limits."""

    count = sorted_values.size
    ks = np.arange(1, (count + 1) // 2 + 1)
    if counts is not None:
        cumulative = np.cumsum([float(c) for c in counts]) / total
        achieved = 1.0 - 2.0 * cumulative[ks - 1]
    else:
        achieved = 1.0 - 2.0 * stats.norm.cdf((ks - 1 + 0.5 - count / 2.0) / sd)
    achieved = np.clip(achieved, 0.0, 1.0)
    distance = np.abs(achieved - ci)
    best = np.flatnonzero(distance <= distance.min() + 1e-12)
    k = int(ks[best[np.argmax(achieved[best])]])
    return k, float(achieved[k - 1]), float(sorted_values[k - 1]), float(sorted_values[count - k])


# ---------------------------------------------------------------------------------------------
# Mann-Whitney
# ---------------------------------------------------------------------------------------------


def _mann_whitney(table: Any, options: dict, result: Any) -> list[Block]:
    ca, cb = _column(table, options.get("first", 0), 0), _column(table, options.get("second", 1), 1)
    a, b = ca.finite, cb.finite
    names = (ca.name, cb.name)
    alt, ci = _alternative(result, options), float(_opt(result, options, "ci", 0.95))
    continuity = bool(_used(result, "continuity", False))
    m, n = a.size, b.size
    pooled = np.concatenate([a, b])
    ranks = stats.rankdata(pooled)
    rows = np.concatenate([np.flatnonzero(np.isfinite(ca.values)), np.flatnonzero(np.isfinite(cb.values))]) + 1
    ranking = Block("Ranks", note="All values are ranked together from smallest to largest; tied values get the average of their ranks.")
    ranking.table = pd.DataFrame({"column": [names[0]] * m + [names[1]] * n, "row": rows, "value": pooled, "rank": ranks}).sort_values(["value", "column"], kind="stable").reset_index(drop=True)
    rank_a, rank_b = float(ranks[:m].sum()), float(ranks[m:].sum())
    ranking.add(f"Number of values in {names[0]}, n_A", "count", m, _ev(result, "n_a"))
    ranking.add(f"Number of values in {names[1]}, n_B", "count", n, _ev(result, "n_b"))
    ranking.add(f"Sum of ranks of {names[0]}, R_A", "Σ ranks of A", rank_a, _ev(result, "rank_sum_a"))
    ranking.add(f"Sum of ranks of {names[1]}, R_B", "Σ ranks of B", rank_b, _ev(result, "rank_sum_b"))
    ranking.add("Check: R_A + R_B", f"N(N+1)/2 = {m + n}·{m + n + 1}/2", rank_a + rank_b)
    u_b = rank_b - n * (n + 1) / 2.0
    u_a = m * n - u_b
    stat = Block("Mann-Whitney U")
    stat.add("U_B", f"R_B − n_B(n_B+1)/2 = {_g(rank_b)} − {n}·{n + 1}/2", u_b, _ev(result, "u_b"))
    stat.add("U_A", f"n_A·n_B − U_B = {m}·{n} − {_g(u_b)}", u_a, _ev(result, "u_a"))
    stat.add("U", f"smaller of U_A and U_B = {_g(min(u_a, u_b))}", min(u_a, u_b), _ev(result, "u"))
    exact_option = _used(result, "exact", None)
    use_exact = (min(m, n) <= 100 and m * n <= 20_000) if exact_option is None else bool(exact_option)
    stat.add("Exact P value used?", "exact when the smaller group has at most 100 values and n_A·n_B ≤ 20 000, else Gaussian approximation", use_exact, _ev(result, "exact"))
    ties = _tie_sum(pooled)
    p_block = Block("P value")
    if use_exact:
        ties_present = np.unique(pooled).size < pooled.size
        observed = None
        if not ties_present:
            counts = _rank_sum_counts(m, n)
            total = math.comb(m + n, n)
            lower, upper = _tails(counts, total, int(round(u_b)))
            note = f"U has {total:,} equally likely arrangements (no ties)"
        elif math.comb(m + n, n) <= EXACT_LIMIT:
            sums = np.array([ranks[list(c)].sum() for c in itertools.combinations(range(m + n), n)]) if n <= m else None
            if sums is None:  # fewer values in A: choose A's positions and take the rest
                total_rank = ranks.sum()
                sums = total_rank - np.array([ranks[list(c)].sum() for c in itertools.combinations(range(m + n), m)])
            tol = 1e-9
            lower, upper = float(np.mean(sums <= rank_b + tol)), float(np.mean(sums >= rank_b - tol))
            note = f"{len(sums):,} arrangements of the pooled ranks (ties kept)"
        else:
            lower = upper = NAN
            note = ""
        if math.isnan(lower):
            _by_engine(p_block, "P value", "exact permutation of the pooled ranks (too many arrangements to list here)", _ev(result, "p"))
        else:
            if alt == "greater":
                p, formula = upper, f"P(U_B ≥ {_g(u_b)}) = {_g(upper)}"
            elif alt == "less":
                p, formula = lower, f"P(U_B ≤ {_g(u_b)}) = {_g(lower)}"
            else:
                p, formula = min(1.0, 2.0 * min(lower, upper)), f"2 · min(P(U_B ≤ {_g(u_b)}), P(U_B ≥ {_g(u_b)})) = 2 · min({_g(lower)}, {_g(upper)})"
            p_block.note = f"Exact P: {note}."
            p_block.add("Lower tail, P(U_B ≤ observed)", "share of arrangements with U_B this small or smaller", lower)
            p_block.add("Upper tail, P(U_B ≥ observed)", "share of arrangements with U_B this large or larger", upper)
            p_block.add("P value", formula, min(1.0, p), _ev(result, "p"))
    else:
        big = m + n
        sd = math.sqrt(m * n / 12.0 * ((big + 1) - ties / (big * (big - 1))))
        p_block.add("Mean of U_B under the null", f"n_A·n_B / 2 = {m}·{n} / 2", m * n / 2.0)
        p_block.add("Tie sum, Σ(t³ − t)", "over groups of tied values", ties)
        p_block.add("SD of U_B under the null", f"√(n_A·n_B/12 · ((N+1) − Σ(t³−t)/(N(N−1)))) = √({m}·{n}/12 · ({big + 1} − {_g(ties)}/({big}·{big - 1})))", sd)
        centred = u_b - m * n / 2.0
        if continuity:
            centred -= {"greater": 0.5, "less": -0.5}.get(alt, 0.5 * float(np.sign(centred)))
        z = _div(centred, sd) if sd > 0 else NAN
        p_block.add("z", f"(U_B − mean{' ∓ 0.5' if continuity else ''}) / SD = {_g(centred)} / {_g(sd)}", z, _ev(result, "z"))
        p, formula = _z_tail(z, alt)
        p_block.add("P value", formula, p, _ev(result, "p"))
    med = Block("Difference between medians")
    ma, mb = float(np.median(a)), float(np.median(b))
    med.add(f"Median of {names[0]}", "middle value (mean of the two middle values when n is even)", ma, _ev(result, "median_a"))
    med.add(f"Median of {names[1]}", "middle value", mb, _ev(result, "median_b"))
    med.add("Difference of medians", f"{_g(mb)} − {_g(ma)}", mb - ma, _ev(result, "difference"))
    differences = np.sort(np.subtract.outer(b, a).ravel())
    med.add("Hodges-Lehmann difference", f"median of all {m * n} differences b_j − a_i", float(np.median(differences)), _ev(result, "hodges_lehmann"))
    sd_u = math.sqrt(m * n * (m + n + 1) / 12.0)
    if use_exact:
        counts = _rank_sum_counts(m, n)
        k, achieved, low, high = _order_ci(differences, counts, math.comb(m + n, n), sd_u, ci)
    else:
        k, achieved, low, high = _order_ci(differences, None, 0, sd_u, ci)
    med.add("Order of the CI limits, k", f"the k-th smallest and k-th largest difference give the CI nearest to {100 * ci:g}%", k)
    med.add("Confidence level reached", "1 − 2·P(U ≤ k − 1)" if use_exact else "1 − 2·Φ((k − 1 + 0.5 − mn/2) / SD)", achieved, _ev(result, "ci_achieved"))
    med.add("Lower limit of the CI", f"k-th smallest of the {m * n} differences", low, _ev(result, "ci_low"))
    med.add("Upper limit of the CI", f"k-th largest of the {m * n} differences", high, _ev(result, "ci_high"))
    return [ranking, stat, p_block, med]


# ---------------------------------------------------------------------------------------------
# Kolmogorov-Smirnov
# ---------------------------------------------------------------------------------------------


def _ks_statistic(a: np.ndarray, b: np.ndarray, alt: str) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
    grid = np.unique(np.concatenate([a, b]))
    fa = np.searchsorted(np.sort(a), grid, side="right") / a.size
    fb = np.searchsorted(np.sort(b), grid, side="right") / b.size
    if alt == "greater":
        d = float(np.max(fa - fb))
    elif alt == "less":
        d = float(np.max(fb - fa))
    else:
        d = float(np.max(np.abs(fa - fb)))
    return max(d, 0.0), grid, fa, fb


def _kolmogorov_smirnov(table: Any, options: dict, result: Any) -> list[Block]:
    ca, cb = _column(table, options.get("first", 0), 0), _column(table, options.get("second", 1), 1)
    a, b = ca.finite, cb.finite
    names = (ca.name, cb.name)
    alt = _alternative(result, options)
    m, n = a.size, b.size
    d, grid, fa, fb = _ks_statistic(a, b, alt)
    steps = Block("Cumulative distributions", note="At each distinct value x, F is the share of the column's values ≤ x.")
    steps.table = pd.DataFrame({"x": grid, f"F_A": fa, "F_B": fb, "F_A − F_B": fa - fb})
    steps.add(f"Number of values in {names[0]}, n_A", "count", m, _ev(result, "n_a"))
    steps.add(f"Number of values in {names[1]}, n_B", "count", n, _ev(result, "n_b"))
    label = {"greater": "D⁺ = max(F_A − F_B)", "less": "D⁻ = max(F_B − F_A)"}.get(alt, "D = max |F_A − F_B|")
    steps.add("Kolmogorov-Smirnov D", label, d, _ev(result, "d"))
    p_block = Block("P value")
    exact_option = _used(result, "exact", None)
    use_exact = (m * n < 10_000) if exact_option is None else bool(exact_option)
    done = False
    if use_exact and math.comb(m + n, m) <= EXACT_LIMIT:
        # Exact P: the share of the arrangements of m A's among the N sorted positions whose D is at least the observed D
        # (the positions are treated as distinct, which is what the exact P assumes).
        combos = np.array(list(itertools.combinations(range(m + n), m)), dtype=np.intp)
        indicator = np.zeros((len(combos), m + n))
        np.put_along_axis(indicator, combos, 1.0, axis=1)
        gap = np.cumsum(indicator, axis=1) / m - np.cumsum(1 - indicator, axis=1) / n
        spread = gap.max(axis=1) if alt == "greater" else (-gap).max(axis=1) if alt == "less" else np.abs(gap).max(axis=1)
        p = float(np.mean(spread >= d - 1e-9))
        p_block.note = f"Exact P: {len(combos):,} arrangements of the {m + n} values into {m} and {n}; ties are not allowed for."
        p_block.add("P value", f"share of arrangements with D ≥ {_g(d)}", p, _ev(result, "p"))
        done = True
    if not done:
        _by_engine(p_block, "P value", "the exact distribution of D (scipy ks_2samp)" if use_exact else "the asymptotic distribution of D (scipy ks_2samp)", _ev(result, "p"))
    p_block.add("Exact P value used?", "exact unless n_A·n_B ≥ 10 000", use_exact, _ev(result, "exact") if _ev(result, "exact") is not None else None)
    med = Block("Medians")
    med.add(f"Median of {names[0]}", "middle value", float(np.median(a)), _ev(result, "median_a"))
    med.add(f"Median of {names[1]}", "middle value", float(np.median(b)), _ev(result, "median_b"))
    return [steps, p_block, med]


# ---------------------------------------------------------------------------------------------
# Wilcoxon matched pairs
# ---------------------------------------------------------------------------------------------


def _wilcoxon(table: Any, options: dict, result: Any) -> list[Block]:
    ca, cb, a, b, rows, dropped = _paired_rows(table, options)
    alt, ci = _alternative(result, options), float(_opt(result, options, "ci", 0.95))
    zero_method = _used(result, "zero_method", "exclude")
    continuity = bool(_used(result, "continuity", False))
    exact_option = _used(result, "exact", None)
    d = b - a
    n_pairs = d.size
    zero = d == 0
    n_zero = int(zero.sum())
    ranked_mask = ~zero if zero_method == "exclude" else np.ones(n_pairs, bool)
    ranks = np.zeros(n_pairs)
    ranks[ranked_mask] = stats.rankdata(np.abs(d[ranked_mask]))
    positive = float(ranks[d > 0].sum())
    negative = float(ranks[d < 0].sum())
    head = Block("Differences and signed ranks", note=f"d = B − A. Zero differences are {'left out before ranking' if zero_method == 'exclude' else 'ranked, then their ranks are left out of both sums (Pratt)'}; "
                 "|d| is ranked smallest to largest, ties get the average rank.")
    head.table = pd.DataFrame({"row": rows, "A": a, "B": b, "d": d, "|d|": np.abs(d), "rank of |d|": np.where(ranked_mask, ranks, np.nan),
                               "sign": np.sign(d).astype(int)})
    head.add("Number of pairs", "rows with both values", n_pairs, _ev(result, "n"))
    head.add("Rows with a blank in either column", "rows with only one of the two values", dropped)
    head.add("Zero differences", "pairs with d = 0", n_zero, _ev(result, "n_zero"))
    head.add("Sum of positive ranks, W⁺", "Σ ranks of the pairs with d > 0", positive, _ev(result, "sum_positive"))
    head.add("Sum of negative ranks, W⁻", "Σ ranks of the pairs with d < 0", negative, _ev(result, "sum_negative"))
    head.add("Sum of signed ranks, W", f"W⁺ − W⁻ = {_g(positive)} − {_g(negative)}", positive - negative, _ev(result, "w"))
    nonzero_ranks = ranks[d != 0]
    n_used = nonzero_ranks.size
    head.add("Number of nonzero differences used", "pairs ranked and not zero", n_used, _ev(result, "n_used"))
    use_exact = n_used >= 1 and (n_used < 200 if exact_option is None else bool(exact_option))
    p_block = Block("P value")
    p_block.add("Exact P value used?", "exact for fewer than 200 nonzero differences, else Gaussian approximation", use_exact, _ev(result, "exact"))
    if use_exact:
        doubled = [int(round(2 * r)) for r in nonzero_ranks]
        top = sum(doubled)
        counts = [0] * (top + 1)
        counts[0] = 1
        reach = 0
        for r in doubled:  # number of sign patterns giving each doubled positive-rank sum
            for s in range(reach, -1, -1):
                if counts[s]:
                    counts[s + r] += counts[s]
            reach += r
        total = 2**n_used
        lower, upper = _tails(counts, total, int(round(2 * positive)))
        if alt == "greater":
            p, formula = upper, f"P(W⁺ ≥ {_g(positive)}) = {_g(upper)}"
        elif alt == "less":
            p, formula = lower, f"P(W⁺ ≤ {_g(positive)}) = {_g(lower)}"
        else:
            p, formula = min(1.0, 2.0 * min(lower, upper)), f"2 · min(P(W⁺ ≤ {_g(positive)}), P(W⁺ ≥ {_g(positive)})) = 2 · min({_g(lower)}, {_g(upper)})"
        p_block.note = f"Exact P: each of the {n_used} nonzero ranks is positive or negative with probability ½ ({total:,} sign patterns)."
        p_block.add("Lower tail, P(W⁺ ≤ observed)", "share of sign patterns with W⁺ this small or smaller", lower)
        p_block.add("Upper tail, P(W⁺ ≥ observed)", "share of sign patterns with W⁺ this large or larger", upper)
        p_block.add("P value", formula, min(1.0, p), _ev(result, "p"))
    else:
        nn = int(ranked_mask.sum())
        mean = nn * (nn + 1) / 4.0
        variance = nn * (nn + 1) * (2 * nn + 1) / 24.0
        if zero_method == "pratt" and n_zero:
            mean -= n_zero * (n_zero + 1) / 4.0
            variance -= n_zero * (n_zero + 1) * (2 * n_zero + 1) / 24.0
        ties = _tie_sum(nonzero_ranks)
        variance -= ties / 48.0
        p_block.add("Mean of W⁺ under the null", f"n(n+1)/4 = {nn}·{nn + 1}/4" + (" − zeros' share (Pratt)" if zero_method == "pratt" and n_zero else ""), mean)
        p_block.add("Tie sum, Σ(t³ − t)", "over groups of tied ranks", ties)
        p_block.add("Variance of W⁺ under the null", f"n(n+1)(2n+1)/24 − Σ(t³−t)/48 = {_g(variance)}", variance)
        centred = positive - mean
        if continuity:
            centred -= {"greater": 0.5, "less": -0.5}.get(alt, 0.5 * float(np.sign(centred)))
        z = _div(centred, math.sqrt(variance)) if variance > 0 else NAN
        p_block.add("z", f"(W⁺ − mean{' ∓ 0.5' if continuity else ''}) / SD = {_g(centred)} / {_g(math.sqrt(max(variance, 0)))}", z, _ev(result, "z"))
        p, formula = _z_tail(z, alt)
        p_block.add("P value", formula, p, _ev(result, "p"))
    med = Block("Median of differences and Hodges-Lehmann estimate")
    med.add("Median of differences", f"median of d over {n_pairs} pairs", float(np.median(d)), _ev(result, "median_difference"))
    i, j = np.triu_indices(n_pairs)
    walsh = np.sort((d[i] + d[j]) / 2.0)
    med.add("Hodges-Lehmann pseudo-median", f"median of the {walsh.size} averages (d_i + d_j)/2, i ≤ j", float(np.median(walsh)), _ev(result, "hodges_lehmann"))
    sd = math.sqrt(n_pairs * (n_pairs + 1) * (2 * n_pairs + 1) / 24.0)
    counts_untied = _signed_rank_counts(n_pairs) if use_exact else None
    k, achieved, low, high = _order_ci(walsh, counts_untied, 2**n_pairs, sd, ci)
    med.add("Order of the CI limits, k", f"the k-th smallest and k-th largest average give the CI nearest to {100 * ci:g}%", k)
    med.add("Confidence level reached", "1 − 2·P(W⁺ ≤ k − 1)" if use_exact else "1 − 2·Φ((k − 1 + 0.5 − m/2) / SD)", achieved, _ev(result, "ci_achieved"))
    med.add("Lower limit of the CI", "k-th smallest average", low, _ev(result, "ci_low"))
    med.add("Upper limit of the CI", "k-th largest average", high, _ev(result, "ci_high"))
    blocks = [head, p_block, med]
    if pair := _pairing_block(a, b, result, spearman=True):
        blocks.append(pair)
    return blocks


@register("compare")
def _compare(source: Any, options: dict, result: Any) -> list[Block]:
    test = options.get("test", "unpaired_t")
    if test in ("unpaired_t", "welch_t", "lognormal_t"):
        return _unpaired(source, options, result, test)
    if test in ("paired_t", "ratio_paired_t"):
        return _paired(source, options, result, test)
    if test == "mann_whitney":
        return _mann_whitney(source, options, result)
    if test == "kolmogorov_smirnov":
        return _kolmogorov_smirnov(source, options, result)
    if test == "wilcoxon":
        return _wilcoxon(source, options, result)
    return []


# ---------------------------------------------------------------------------------------------
# Multiple comparisons
# ---------------------------------------------------------------------------------------------

Q_METHODS = ("tukey", "games_howell", "newman_keuls")


def _comparison_blocks(result: Any, options: dict, names: list[str], estimates: np.ndarray, se_df, kind: str) -> list[Block]:
    """The multiple comparisons of a sheet: each pair's difference, SE and statistic recomputed, the adjusted P recomputed where it is a
    plain formula and otherwise the engine's. ``se_df(i, j)`` gives the SE and df of a pair; ``kind`` is "pooled", "welch" or "rank"."""

    rows = (getattr(result, "values", None) or {}).get("comparisons")
    if not rows:
        if options.get("comparisons") in (None, "none") and not options.get("trend"):
            return [Block("Multiple comparisons", note="None were asked for in this analysis. Choose them with Change options… "
                                                       "(Multiple comparisons) and each pair's difference, SE, statistic and adjusted P "
                                                       "are worked out here.")]
        return []
    method = result.values.get("comparison_method", "")
    title = next((s.title for s in result.sections if s.title.startswith("Multiple comparisons: ")), "Multiple comparisons")
    sizes = result.values.get("comparisons_per_family") or [len(rows)]
    m_set = options.get("m")
    k = len(names)
    index = {name: i for i, name in enumerate(names)}
    fdr = method in ("bky", "bh", "by")
    block = Block(title, note=f"Method: {title.split(': ', 1)[-1]}. Differences are group 1 − group 2 as the sheet lists them. "
                  + ("The statistic is z = difference / SE." if kind == "rank" else "")
                  + ("A q value, an adjusted P of the other methods, needs all the P values together: the sheet's value is shown." if fdr else ""))
    summary = []
    for row in rows:
        i, j = index[row["group_1"]], index[row["group_2"]]
        label = row["label"]
        diff = float(estimates[i] - estimates[j])
        se, df = se_df(i, j)
        t = _div(diff, se) if se > 0 else NAN
        q_stat = method in Q_METHODS
        statistic = abs(t) * math.sqrt(2) if q_stat else t
        symbol = "q" if q_stat else ("z" if kind == "rank" else "t")
        block.add(f"{label}: difference", f"{_g(estimates[i])} − {_g(estimates[j])}", diff, row["difference"])
        block.add(f"{label}: SE", "standard error of the difference", se, row["se"])
        block.add(f"{label}: {symbol}", f"|difference| / SE · √2 = {_g(abs(diff))} / {_g(se)} · √2" if q_stat else f"difference / SE = {_g(diff)} / {_g(se)}", statistic, row["statistic"])
        infinite = not math.isfinite(df)
        raw = float(min(1.0, 2 * (stats.norm.sf(abs(t)) if infinite else stats.t.sf(abs(t), df)))) if math.isfinite(t) else NAN
        block.add(f"{label}: P value (unadjusted)", f"2 · P(Z > {_g(abs(t))})" if infinite else f"2 · P(T{_sub(df)} > {_g(abs(t))})", raw, row["p"])
        made = sizes[row["family"] - 1] if row["family"] - 1 < len(sizes) else len(rows)
        m = made if m_set is None else int(m_set)
        adjusted, formula = None, ""
        if method in ("tukey", "games_howell"):
            adjusted, formula = float(min(1.0, max(0.0, stats.studentized_range.sf(statistic, k, df if math.isfinite(df) and df < 1e7 else 1e7)))), f"P(Q({k}, df = {_g(df)}) > {_g(statistic)}), studentized range"
        elif method in ("bonferroni", "dunn"):
            adjusted, formula = min(1.0, m * raw), f"min(1, m · P) = min(1, {m} · {_g(raw)})"
        elif method in ("sidak", "tamhane_t2"):
            adjusted, formula = 1 - (1 - raw) ** m, f"1 − (1 − P)^m = 1 − (1 − {_g(raw)})^{m}"
        elif method in ("fisher_lsd", "dunn_uncorrected"):
            adjusted, formula = raw, "= unadjusted P (no adjustment)"
        engine_adjusted = row["q"] if fdr else row["p_adjusted"]
        label_adjusted = f"{label}: q value" if fdr else f"{label}: adjusted P"
        if adjusted is not None and not fdr:
            block.add(label_adjusted, formula, adjusted, engine_adjusted)
        elif math.isfinite(engine_adjusted):
            _by_engine(block, label_adjusted, f"{title.split(': ', 1)[-1]} (needs the other comparisons or a multivariate distribution)", engine_adjusted)
        summary.append({"comparison": label, "difference": diff, "SE": se, "df": df, symbol: statistic, "P": raw,
                        "adjusted P" if not fdr else "q": engine_adjusted if adjusted is None else adjusted})
    block.table = pd.DataFrame(summary)
    return [block]


def _engine_only_comparisons(result: Any) -> list[Block]:
    """Comparisons after the mixed model: the engine's differences, SEs and P values."""

    rows = (getattr(result, "values", None) or {}).get("comparisons")
    if not rows:
        return []
    title = next((s.title for s in result.sections if s.title.startswith("Multiple comparisons: ")), "Multiple comparisons")
    block = Block(title, note="From the fitted model's means and their covariance; shown as the analysis gives them.")
    for row in rows:
        _by_engine(block, f"{row['label']}: difference", "the mixed model", row["difference"])
        _by_engine(block, f"{row['label']}: SE", "the mixed model", row["se"])
        _by_engine(block, f"{row['label']}: adjusted P", "the mixed model", row["q"] if math.isnan(row["p_adjusted"]) else row["p_adjusted"])
    return [block]


# ---------------------------------------------------------------------------------------------
# One-way ANOVA
# ---------------------------------------------------------------------------------------------


def _groups(table: Any) -> tuple[list[str], list[np.ndarray]]:
    return table.names, [column.finite for column in table.columns]


def _list_engine(result: Any, key: str, i: int) -> float | None:
    values = (getattr(result, "values", None) or {}).get(key)
    try:
        return float(values[i])
    except (TypeError, IndexError, ValueError):
        return None


def _anova_f(samples: list[np.ndarray]) -> dict:
    n = np.array([s.size for s in samples], float)
    means = np.array([s.mean() for s in samples])
    k, big_n = len(samples), n.sum()
    grand = float(np.concatenate(samples).mean())
    ss_b = float(np.sum(n * (means - grand) ** 2))
    ss_w = float(sum(np.sum((s - s.mean()) ** 2) for s in samples))
    df_b, df_w = k - 1, int(big_n - k)
    ms_b, ms_w = ss_b / df_b, (ss_w / df_w if df_w > 0 else NAN)
    f = ms_b / ms_w if ms_w > 0 else NAN
    return {"n": n, "means": means, "k": k, "N": big_n, "grand": grand, "ss_b": ss_b, "ss_w": ss_w, "df_b": df_b, "df_w": df_w, "ms_b": ms_b, "ms_w": ms_w, "f": f}


def _group_summary(names: list[str], samples: list[np.ndarray], result: Any) -> Block:
    block = Block("Group summaries")
    rows = []
    for i, (name, x) in enumerate(zip(names, samples)):
        n = x.size
        mean = float(x.mean())
        ss = float(np.sum((x - mean) ** 2))
        sd = math.sqrt(ss / (n - 1)) if n > 1 else NAN
        block.add(f"{name}: n", "count of non-blank cells", n, _list_engine(result, "n", i))
        block.add(f"{name}: mean", f"Σx / n = {_g(x.sum())} / {n}", mean, _list_engine(result, "means", i))
        block.add(f"{name}: SD", f"√(Σ(x − x̄)² / (n − 1)) = √({_g(ss)} / {n - 1})", sd, _list_engine(result, "sds", i))
        rows.append({"group": name, "n": n, "Σx": float(x.sum()), "mean": mean, "SS": ss, "SD": sd})
    block.table = pd.DataFrame(rows)
    return block


def _ordinary(table: Any, options: dict, result: Any) -> list[Block]:
    names, samples = _groups(table)
    core = _anova_f(samples)
    k, big_n = core["k"], core["N"]
    blocks = [_group_summary(names, samples, result)]
    df_set = options.get("df")
    df_w = core["df_w"] if df_set is None else float(df_set)
    ss = Block("Sums of squares")
    ss.add("Number of values, N", "Σ n_i", int(big_n))
    ss.add("Grand mean, ȳ", f"Σ all values / N", core["grand"])
    ss.add("Between groups, SS_between", "Σ n_i (ȳ_i − ȳ)²", core["ss_b"], _ev(result, "ss_between"))
    ss.add("Within groups, SS_within", "Σ Σ (y − ȳ_i)² = sum of the groups' SS", core["ss_w"], _ev(result, "ss_within"))
    ss_total = core["ss_b"] + core["ss_w"]
    ss.add("Total, SS_total", f"SS_between + SS_within = {_g(core['ss_b'])} + {_g(core['ss_w'])}", ss_total, _ev(result, "ss_total"))
    blocks.append(ss)
    tab = Block("ANOVA table")
    tab.add("df between", f"k − 1 = {k} − 1", core["df_b"], _ev(result, "dfn"))
    tab.add("df within", f"N − k = {int(big_n)} − {k}" if df_set is None else f"set by hand under Advanced (N − k = {core['df_w']})", df_w, _ev(result, "dfd"))
    tab.add("MS between", f"SS_between / df = {_g(core['ss_b'])} / {core['df_b']}", core["ms_b"], _ev(result, "ms_between"))
    tab.add("MS within", f"SS_within / (N − k) = {_g(core['ss_w'])} / {core['df_w']}", core["ms_w"], _ev(result, "ms_within"))
    tab.add("F", f"MS_between / MS_within = {_g(core['ms_b'])} / {_g(core['ms_w'])}", core["f"], _ev(result, "f"))
    p = float(stats.f.sf(core["f"], core["df_b"], df_w)) if math.isfinite(core["f"]) else NAN
    tab.add("P value", f"P(F{_sub(core['df_b'])},{_sub(df_w)} > {_g(core['f'])})", p, _ev(result, "p"))
    tab.add("R²", f"SS_between / SS_total = {_g(core['ss_b'])} / {_g(ss_total)}", core["ss_b"] / ss_total if ss_total > 0 else NAN, _ev(result, "r_squared"))
    tab.table = pd.DataFrame({"source": ["Between groups", "Within groups", "Total"], "SS": [core["ss_b"], core["ss_w"], ss_total],
                              "df": [core["df_b"], core["df_w"], int(big_n) - 1], "MS": [core["ms_b"], core["ms_w"], NAN], "F": [core["f"], NAN, NAN], "P": [p, NAN, NAN]})
    blocks.append(tab)
    if all(s.size >= 2 for s in samples):
        bf = Block("Brown-Forsythe test of equal SDs", note="An ordinary one-way ANOVA on z = |x − median of its group|.")
        z = [np.abs(s - np.median(s)) for s in samples]
        zc = _anova_f(z)
        bf.table = pd.DataFrame({"group": np.repeat(names, [s.size for s in samples]), "x": np.concatenate(samples), "median": np.repeat([float(np.median(s)) for s in samples], [s.size for s in samples]),
                                 "z": np.concatenate(z)})
        bf.add("SS between (of z)", "Σ n_i (z̄_i − z̄)²", zc["ss_b"])
        bf.add("SS within (of z)", "Σ Σ (z − z̄_i)²", zc["ss_w"])
        bf.add("F", f"MS_between / MS_within = {_g(zc['ms_b'])} / {_g(zc['ms_w'])}", zc["f"], _ev(result, "bf_f"))
        bf.add("df between", f"k − 1 = {zc['df_b']}", zc["df_b"], _ev(result, "bf_dfn"))
        bf.add("df within", f"N − k = {zc['df_w']}", zc["df_w"], _ev(result, "bf_dfd"))
        bf.add("P value", f"P(F{_sub(zc['df_b'])},{_sub(zc['df_w'])} > {_g(zc['f'])})", float(stats.f.sf(zc["f"], zc["df_b"], zc["df_w"])) if math.isfinite(zc["f"]) else NAN, _ev(result, "bf_p"))
        blocks.append(bf)
        v = np.array([np.var(s, ddof=1) for s in samples])
        if np.all(v > 0):
            n = core["n"]
            sp = float(np.sum((n - 1) * v) / (big_n - k))
            num = (big_n - k) * math.log(sp) - float(np.sum((n - 1) * np.log(v)))
            den = 1 + (float(np.sum(1 / (n - 1))) - 1 / (big_n - k)) / (3 * (k - 1))
            bart = Block("Bartlett's test of equal SDs")
            bart.add("Pooled variance, s_p²", "Σ (n_i − 1) s_i² / (N − k)", sp)
            bart.add("Numerator", f"(N − k) ln s_p² − Σ (n_i − 1) ln s_i² = {_g(num)}", num)
            bart.add("Denominator", f"1 + (Σ 1/(n_i − 1) − 1/(N − k)) / (3(k − 1)) = {_g(den)}", den)
            bart.add("K² (corrected)", f"numerator / denominator = {_g(num)} / {_g(den)}", num / den, _ev(result, "bartlett"))
            bart.add("P value", f"P(χ²{_sub(k - 1)} > {_g(num / den)})", float(stats.chi2.sf(num / den, k - 1)), _ev(result, "bartlett_p"))
            blocks.append(bart)
    sd_set = options.get("sd") if options.get("comparisons") not in (None, "none") or options.get("trend") else None
    pooled_var = core["ms_w"] if sd_set is None else float(sd_set) ** 2
    blocks += _comparison_blocks(result, options, names, core["means"], lambda i, j: (math.sqrt(pooled_var * (1 / core["n"][i] + 1 / core["n"][j])), float(df_w)), "pooled")
    return blocks


def _welch(table: Any, options: dict, result: Any) -> list[Block]:
    names, samples = _groups(table)
    n = np.array([s.size for s in samples], float)
    means = np.array([s.mean() for s in samples])
    v = np.array([np.var(s, ddof=1) for s in samples])
    k, big_n = len(samples), n.sum()
    blocks = [_group_summary(names, samples, result)]
    w = n / v
    wm = float(np.sum(w * means) / w.sum())
    lam = float(np.sum((1 - w / w.sum()) ** 2 / (n - 1)))
    num = float(np.sum(w * (means - wm) ** 2) / (k - 1))
    wb = Block("Welch's ANOVA, W")
    wb.table = pd.DataFrame({"group": names, "n": n, "mean": means, "variance": v, "weight w = n/s²": w, "1 − w/Σw": 1 - w / w.sum()})
    wb.add("Weighted mean, ȳ_w", f"Σ w_i ȳ_i / Σ w_i = {_g(np.sum(w * means))} / {_g(w.sum())}", wm)
    wb.add("Numerator", f"Σ w_i (ȳ_i − ȳ_w)² / (k − 1) = {_g(np.sum(w * (means - wm) ** 2))} / {k - 1}", num)
    wb.add("Λ", "Σ (1 − w_i/Σw)² / (n_i − 1)", lam)
    den = 1 + 2 * (k - 2) / (k * k - 1) * lam
    wb.add("Denominator", f"1 + 2(k − 2)/(k² − 1) · Λ = 1 + 2·{k - 2}/{k * k - 1} · {_g(lam)}", den)
    big_w = num / den
    wb.add("W", f"numerator / denominator = {_g(num)} / {_g(den)}", big_w, _ev(result, "welch_f"))
    wb.add("df numerator", f"k − 1 = {k - 1}", k - 1, _ev(result, "welch_dfn"))
    df2_calc = (k * k - 1) / (3 * lam)
    df_set = options.get("df")
    df2 = df2_calc if df_set is None else float(df_set)
    wb.add("df denominator", f"(k² − 1) / (3Λ) = {k * k - 1} / (3 · {_g(lam)})" if df_set is None else f"set by hand under Advanced (computed {_g(df2_calc)})", df2, _ev(result, "welch_dfd"))
    wb.add("P value", f"P(F{_sub(k - 1)},{_sub(df2)} > {_g(big_w)})", float(stats.f.sf(big_w, k - 1, df2)), _ev(result, "welch_p"))
    blocks.append(wb)
    grand = float(np.concatenate(samples).mean())
    denom = float(np.sum((1 - n / big_n) * v))
    fstar = float(np.sum(n * (means - grand) ** 2) / denom)
    c = (1 - n / big_n) * v / denom
    bf2 = 1 / float(np.sum(c * c / (n - 1)))
    bb = Block("Brown-Forsythe ANOVA, F*")
    bb.table = pd.DataFrame({"group": names, "n": n, "mean": means, "variance": v, "1 − n/N": 1 - n / big_n, "(1 − n/N)·s²": (1 - n / big_n) * v, "c": c})
    bb.add("Grand mean, ȳ", "Σ all values / N", grand)
    bb.add("Numerator", "Σ n_i (ȳ_i − ȳ)²", float(np.sum(n * (means - grand) ** 2)))
    bb.add("Denominator", "Σ (1 − n_i/N) s_i²", denom)
    bb.add("F*", f"numerator / denominator = {_g(np.sum(n * (means - grand) ** 2))} / {_g(denom)}", fstar, _ev(result, "bf_anova_f"))
    bb.add("df numerator", f"k − 1 = {k - 1}", k - 1, _ev(result, "bf_anova_dfn"))
    bb.add("df denominator", "1 / Σ (c_i² / (n_i − 1)), c_i = (1 − n_i/N)s_i² / Σ(1 − n_j/N)s_j²", bf2, _ev(result, "bf_anova_dfd"))
    bb.add("P value", f"P(F{_sub(k - 1)},{_sub(bf2)} > {_g(fstar)})", float(stats.f.sf(fstar, k - 1, bf2)), _ev(result, "bf_anova_p"))
    blocks.append(bb)

    def pair(i: int, j: int) -> tuple[float, float]:
        a, b = v[i] / n[i], v[j] / n[j]
        return math.sqrt(a + b), (a + b) ** 2 / (a * a / (n[i] - 1) + b * b / (n[j] - 1))

    blocks += _comparison_blocks(result, options, names, means, pair, "welch")
    return blocks


def _repeated_measures(table: Any, options: dict, result: Any) -> list[Block]:
    names = table.names
    rows = max(c.values.size for c in table.columns)
    Y = np.full((rows, len(names)), np.nan)
    for j, c in enumerate(table.columns):
        Y[: c.values.size, j] = c.values
    Y = Y[np.isfinite(Y).any(axis=1)]
    if str(getattr(result, "analysis", "")).startswith("Mixed-effects"):
        return _mixed(Y, names, options, result)
    complete = np.isfinite(Y).all(axis=1)
    dropped = int((~complete).sum())
    Y = Y[complete]
    n, k = Y.shape
    grand = float(Y.mean())
    col, row = Y.mean(axis=0), Y.mean(axis=1)
    head = Block("Data used", note="Each row is one subject; a row with a blank is left out.")
    head.table = pd.DataFrame({"subject (row)": np.arange(1, n + 1), **{name: Y[:, j] for j, name in enumerate(names)}, "subject mean": row})
    head.add("Number of subjects, n", "complete rows", n, _ev(result, "n_subjects"))
    head.add("Number of treatments, k", "columns", k)
    head.add("Rows left out", "rows with a blank", dropped)
    for j, name in enumerate(names):
        head.add(f"{name}: mean", f"Σ column / n = {_g(Y[:, j].sum())} / {n}", float(col[j]), _list_engine(result, "means", j))
    head.add("Grand mean, ȳ", "Σ all values / (n·k)", grand)
    ss_total = float(np.sum((Y - grand) ** 2))
    ss_treat = float(n * np.sum((col - grand) ** 2))
    ss_subj = float(k * np.sum((row - grand) ** 2))
    ss_resid = ss_total - ss_treat - ss_subj
    df_t, df_s, df_r = k - 1, n - 1, (n - 1) * (k - 1)
    ss = Block("Sums of squares")
    ss.add("Total, SS_total", "Σ (y − ȳ)² over all values", ss_total, _ev(result, "ss_total"))
    ss.add("Treatments (columns), SS_treatment", "n Σ (ȳ_j − ȳ)²", ss_treat, _ev(result, "ss_treatment"))
    ss.add("Subjects (rows), SS_subjects", "k Σ (ȳ_i − ȳ)²", ss_subj, _ev(result, "ss_subjects"))
    ss.add("Residual, SS_residual", f"SS_total − SS_treatment − SS_subjects = {_g(ss_total)} − {_g(ss_treat)} − {_g(ss_subj)}", ss_resid, _ev(result, "ss_residual"))
    ms_t, ms_s, ms_r = ss_treat / df_t, ss_subj / df_s, ss_resid / df_r
    f = _div(ms_t, ms_r) if ms_r > 0 else NAN
    f_s = _div(ms_s, ms_r) if ms_r > 0 else NAN
    eps_block = Block("Geisser-Greenhouse epsilon")
    S = np.cov(Y, rowvar=False, ddof=1).reshape(k, k) if n > 1 else np.full((k, k), NAN)
    if k > 2:
        C = np.eye(k) - np.ones((k, k)) / k
        Sc = C @ S @ C
        denom = (k - 1) * np.trace(Sc @ Sc)
        eps = float(np.trace(Sc) ** 2 / denom) if denom > 0 else NAN
        eps_block.note = "S is the covariance matrix of the columns; S* = C S C with C = I − J/k is the double-centred matrix."
        eps_block.table = pd.DataFrame(Sc, index=names, columns=names).rename_axis("S*").reset_index()
        eps_block.add("tr(S*)", "sum of the diagonal of S*", float(np.trace(Sc)))
        eps_block.add("tr(S*²)", "sum of the diagonal of S*·S*", float(np.trace(Sc @ Sc)))
        eps_block.add("ε", f"(tr S*)² / ((k − 1) tr(S*²)) = {_g(np.trace(Sc) ** 2)} / ({k - 1} · {_g(np.trace(Sc @ Sc))})", eps, _ev(result, "epsilon"))
    else:
        eps = 1.0
        eps_block.add("ε", "k = 2: ε = 1", eps, _ev(result, "epsilon"))
    gg = bool(_opt(result, options, "geisser_greenhouse", True))
    scale = eps if gg and math.isfinite(eps) else 1.0
    epsilon_set = options.get("epsilon")
    if epsilon_set is not None:
        scale = min(1.0, max(1.0 / (k - 1), float(epsilon_set)))
        eps_block.add("ε used (set by hand)", "between 1/(k − 1) and 1", scale, _ev(result, "epsilon_used"))
    df_err = df_r if options.get("df") is None else float(options["df"])
    dfn, dfd = df_t * scale, df_err * scale
    tab = Block("ANOVA table")
    tab.add("MS treatments", f"SS_treatment / (k − 1) = {_g(ss_treat)} / {df_t}", ms_t, _ev(result, "ms_treatment"))
    tab.add("MS subjects", f"SS_subjects / (n − 1) = {_g(ss_subj)} / {df_s}", ms_s, _ev(result, "ms_subjects"))
    tab.add("MS residual", f"SS_residual / ((n − 1)(k − 1)) = {_g(ss_resid)} / {df_r}", ms_r, _ev(result, "ms_residual"))
    tab.add("F (treatments)", f"MS_treatment / MS_residual = {_g(ms_t)} / {_g(ms_r)}", f, _ev(result, "f"))
    tab.add("df numerator", f"(k − 1){' · ε' if scale != 1 else ''} = {df_t}{' · ' + _g(scale) if scale != 1 else ''}", dfn, _ev(result, "dfn"))
    tab.add("df denominator", f"{'(n − 1)(k − 1)' if options.get('df') is None else 'set by hand'}{' · ε' if scale != 1 else ''} = {_g(df_err)}{' · ' + _g(scale) if scale != 1 else ''}", dfd, _ev(result, "dfd"))
    p = float(stats.f.sf(f, dfn, dfd)) if math.isfinite(f) else NAN
    tab.add("P value", f"P(F{_sub(dfn)},{_sub(dfd)} > {_g(f)})", p, _ev(result, "p"))
    tab.add("R² (treatments)", f"SS_treatment / (SS_treatment + SS_residual) = {_g(ss_treat)} / {_g(ss_treat + ss_resid)}", ss_treat / (ss_treat + ss_resid) if ss_treat + ss_resid > 0 else NAN, _ev(result, "r_squared"))
    tab.add("F (matching, subjects)", f"MS_subjects / MS_residual = {_g(ms_s)} / {_g(ms_r)}", f_s, _ev(result, "f_subjects"))
    tab.add("P value (matching)", f"P(F{_sub(df_s)},{_sub(df_r)} > {_g(f_s)})", float(stats.f.sf(f_s, df_s, df_r)) if math.isfinite(f_s) else NAN, _ev(result, "p_subjects"))
    tab.add("R² (matching)", f"SS_subjects / SS_total = {_g(ss_subj)} / {_g(ss_total)}", ss_subj / ss_total if ss_total > 0 else NAN, _ev(result, "r_squared_matching"))
    tab.table = pd.DataFrame({"source": ["Treatments", "Subjects", "Residual", "Total"], "SS": [ss_treat, ss_subj, ss_resid, ss_total],
                              "df": [dfn, df_s, dfd, n * k - 1], "MS": [ms_t, ms_s, ms_r, NAN], "F": [f, f_s, NAN, NAN]})
    blocks = [head, ss, eps_block, tab]
    sd_set = options.get("sd") if options.get("comparisons") not in (None, "none") or options.get("trend") else None
    separate = "only the two columns" in (getattr(result, "methods", "") or "")
    if separate:
        def pair(i: int, j: int) -> tuple[float, float]:
            dd = Y[:, i] - Y[:, j]
            return float(np.std(dd, ddof=1) / math.sqrt(n)), float(n - 1)
    else:
        var = ms_r if sd_set is None else float(sd_set) ** 2

        def pair(i: int, j: int) -> tuple[float, float]:
            return math.sqrt(2 * var / n), float(df_err)

    blocks += _comparison_blocks(result, options, names, col, pair, "pooled")
    return blocks


def _mixed(Y: np.ndarray, names: list[str], options: dict, result: Any) -> list[Block]:
    """Rows with a blank: the engine fitted a mixed-effects model by REML, which is not redone here. The inputs and the engine's values."""

    present = np.isfinite(Y)
    head = Block("Data used", note="Rows with a blank cannot be analyzed by repeated-measures ANOVA, so the analysis fitted a mixed-effects model "
                 "(random intercept per subject, REML). Nothing below this block is recomputed by hand: the values are the analysis's own.")
    head.add("Number of subjects (rows)", "rows with at least one value", Y.shape[0], _ev(result, "n_subjects"))
    head.add("Number of values", "non-blank cells", int(present.sum()), _ev(result, "n_values"))
    rows = []
    for j, name in enumerate(names):
        x = Y[present[:, j], j]
        rows.append({"column": name, "n": x.size, "mean of the values": float(x.mean()) if x.size else NAN})
        head.add(f"{name}: n", "non-blank cells", x.size)
        head.add(f"{name}: mean of its values", f"Σx / n = {_g(x.sum())} / {x.size}", float(x.mean()) if x.size else NAN)
    head.table = pd.DataFrame(rows)
    model = Block("Mixed-effects model (REML)", note="Fixed effect of treatment tested by a Wald F test; the fitted group means differ from the plain column means when values are missing.")
    for label, key in (("F (treatment)", "f"), ("df numerator", "dfn"), ("df denominator", "dfd"), ("P value", "p"), ("Geisser-Greenhouse ε", "epsilon"),
                       ("Subject variance", "var_subject"), ("Residual variance", "var_residual"), ("Chi-square of the matching test", "lr_matching"),
                       ("P value of the matching test", "p_matching")):
        value = _ev(result, key)
        if value is not None:
            _by_engine(model, label, "the mixed-effects model fitted by REML", value)
    for j, name in enumerate(names):
        value = _list_engine(result, "means", j)
        if value is not None:
            _by_engine(model, f"{name}: fitted mean", "the mixed-effects model fitted by REML", value)
    return [head, model, *_engine_only_comparisons(result)]


def _rank_table(names: list[str], values: list[np.ndarray], ranks: list[np.ndarray]) -> pd.DataFrame:
    return pd.DataFrame({"group": np.repeat(names, [v.size for v in values]), "value": np.concatenate(values), "rank": np.concatenate(ranks)})


def _kruskal_wallis(table: Any, options: dict, result: Any) -> list[Block]:
    names, samples = _groups(table)
    pooled = np.concatenate(samples)
    big_n = pooled.size
    ranks = stats.rankdata(pooled)
    sizes = [s.size for s in samples]
    split = np.split(ranks, np.cumsum(sizes)[:-1])
    n = np.array(sizes, float)
    k = len(samples)
    sums = np.array([r.sum() for r in split])
    rank_block = Block("Ranks", note="All values are ranked together from smallest to largest; tied values get the average of their ranks.")
    rank_block.table = _rank_table(names, samples, split)
    for i, name in enumerate(names):
        rank_block.add(f"{name}: n", "count", sizes[i], _list_engine(result, "n", i))
        rank_block.add(f"{name}: sum of ranks, R", "Σ ranks of the group", float(sums[i]), _list_engine(result, "rank_sums", i))
        rank_block.add(f"{name}: mean rank", f"R / n = {_g(sums[i])} / {sizes[i]}", float(sums[i] / n[i]), _list_engine(result, "mean_ranks", i))
    stat = Block("Kruskal-Wallis H")
    total = float(np.sum(sums**2 / n))
    h_raw = 12 / (big_n * (big_n + 1)) * total - 3 * (big_n + 1)
    ties = _tie_sum(pooled)
    corr = 1 - ties / (big_n**3 - big_n)
    h = h_raw / corr
    stat.add("Number of values, N", "Σ n_i", big_n)
    stat.add("Σ R_i² / n_i", " + ".join(f"{_g(r)}²/{s}" for r, s in zip(sums, sizes)), total)
    stat.add("H before the tie correction", f"12 / (N(N+1)) · Σ R²/n − 3(N+1) = 12 / ({big_n}·{big_n + 1}) · {_g(total)} − {3 * (big_n + 1)}", h_raw, _ev(result, "h_uncorrected"))
    stat.add("Tie sum, Σ(t³ − t)", "over groups of tied values", ties)
    stat.add("Tie correction", f"1 − Σ(t³−t) / (N³ − N) = 1 − {_g(ties)} / {big_n ** 3 - big_n}", corr)
    stat.add("H", f"H before correction / tie correction = {_g(h_raw)} / {_g(corr)}", h, _ev(result, "h"))
    stat.add("df", f"k − 1 = {k - 1}", k - 1, _ev(result, "df"))
    stat.add("P value (chi-square approximation)", f"P(χ²{_sub(k - 1)} > {_g(h)})", float(stats.chi2.sf(h, k - 1)), _ev(result, "p_chi2"))
    if _ev(result, "exact"):
        _by_engine(stat, "P value (exact, as the sheet reports it)", "exact permutation of the ranks into groups of these sizes", _ev(result, "p"))
    else:
        stat.add("P value", f"P(χ²{_sub(k - 1)} > {_g(h)})", float(stats.chi2.sf(h, k - 1)), _ev(result, "p"))
    blocks = [rank_block, stat]
    variance = big_n * (big_n + 1) / 12 - ties / (12 * (big_n - 1))
    blocks += _comparison_blocks(result, options, names, sums / n, lambda i, j: (math.sqrt(variance * (1 / n[i] + 1 / n[j])), math.inf), "rank")
    if blocks[-1].title.startswith("Multiple") and blocks[-1].note:
        blocks[-1].note += f" Differences are of mean ranks; SE = √(σ² (1/n_i + 1/n_j)), σ² = N(N+1)/12 − Σ(t³−t)/(12(N−1)) = {_g(variance)}."
    return blocks


def _friedman(table: Any, options: dict, result: Any) -> list[Block]:
    names = table.names
    rows = max(c.values.size for c in table.columns)
    Y = np.full((rows, len(names)), np.nan)
    for j, c in enumerate(table.columns):
        Y[: c.values.size, j] = c.values
    present = np.isfinite(Y).any(axis=1)
    complete = np.isfinite(Y).all(axis=1)
    dropped = int((present & ~complete).sum())
    Y = Y[complete]
    n, k = Y.shape
    R = np.apply_along_axis(stats.rankdata, 1, Y)
    sums = R.sum(axis=0)
    block = Block("Ranks within each row", note="Each row (subject) is ranked across the columns, smallest = 1; ties get the average rank.")
    block.table = pd.DataFrame({"row": np.arange(1, n + 1), **{f"{name} (value)": Y[:, j] for j, name in enumerate(names)}, **{f"{name} (rank)": R[:, j] for j, name in enumerate(names)}})
    block.add("Number of subjects, n", "complete rows", n, _ev(result, "n_subjects"))
    block.add("Rows left out", "rows with a blank", dropped)
    for j, name in enumerate(names):
        block.add(f"{name}: sum of ranks, R", "Σ of its ranks", float(sums[j]), _list_engine(result, "rank_sums", j))
        block.add(f"{name}: mean rank", f"R / n = {_g(sums[j])} / {n}", float(sums[j] / n), _list_engine(result, "mean_ranks", j))
    stat = Block("Friedman statistic")
    ties = sum(_tie_sum(row) for row in Y)
    denom = n * k * (k + 1) - ties / (k - 1)
    centre = n * (k + 1) / 2
    num = 12 * float(np.sum((sums - centre) ** 2))
    q = num / denom
    stat.add("Expected rank sum, n(k+1)/2", f"{n}·{k + 1}/2", centre)
    stat.add("Numerator", f"12 Σ (R_j − n(k+1)/2)² = 12 · {_g(np.sum((sums - centre) ** 2))}", num)
    stat.add("Tie sum, Σ(t³ − t) over the rows", "ties within rows", ties)
    stat.add("Denominator", f"n k (k+1) − Σ(t³−t)/(k−1) = {n}·{k}·{k + 1} − {_g(ties)}/{k - 1}", denom)
    stat.add("Q", f"numerator / denominator = {_g(num)} / {_g(denom)}", q, _ev(result, "statistic"))
    stat.add("df", f"k − 1 = {k - 1}", k - 1, _ev(result, "df"))
    stat.add("P value (chi-square approximation)", f"P(χ²{_sub(k - 1)} > {_g(q)})", float(stats.chi2.sf(q, k - 1)), _ev(result, "p_chi2"))
    if _ev(result, "exact"):
        _by_engine(stat, "P value (exact, as the sheet reports it)", "exact permutation of the ranks within rows", _ev(result, "p"))
    else:
        stat.add("P value", f"P(χ²{_sub(k - 1)} > {_g(q)})", float(stats.chi2.sf(q, k - 1)), _ev(result, "p"))
    blocks = [block, stat]
    blocks += _comparison_blocks(result, options, names, sums / n, lambda i, j: (math.sqrt(k * (k + 1) / (6 * n)), math.inf), "rank")
    if blocks[-1].title.startswith("Multiple") and blocks[-1].note:
        blocks[-1].note += f" Differences are of mean ranks; SE = √(k(k+1)/(6n)) = {_g(math.sqrt(k * (k + 1) / (6 * n)))}."
    return blocks


@register("anova")
def _anova(source: Any, options: dict, result: Any) -> list[Block]:
    test = options.get("test", "ordinary")
    if test == "ordinary":
        return _ordinary(source, options, result)
    if test in ("welch", "brown_forsythe"):
        return _welch(source, options, result)
    if test in ("repeated_measures", "mixed"):
        return _repeated_measures(source, options, result)
    if test == "kruskal_wallis":
        return _kruskal_wallis(source, options, result)
    if test == "friedman":
        return _friedman(source, options, result)
    return []


# ---------------------------------------------------------------------------------------------
# Nested
# ---------------------------------------------------------------------------------------------


@register("nested")
def _nested(source: Any, options: dict, result: Any) -> list[Block]:
    long = source.long()
    names, groups = [], []
    for name in source.names:
        rows = long[long["group"] == name]
        if rows.empty:
            continue
        order = list(dict.fromkeys(rows["unit"]))
        names.append(name)
        groups.append([(unit, rows.loc[rows["unit"] == unit, "value"].to_numpy(float)) for unit in order])
    k = len(names)
    units = [[u for _, u in g] for g in groups]
    n_gr = [np.array([u.size for u in g], float) for g in units]
    n_g = np.array([n.sum() for n in n_gr])
    big_n, r = float(n_g.sum()), sum(len(g) for g in units)
    every = np.concatenate([u for g in units for u in g])
    grand = float(every.mean())
    means = np.array([np.concatenate(g).mean() for g in units])
    unit_means = [np.array([u.mean() for u in g]) for g in units]
    detail = Block("Replicates", note="Each replicate (biological replicate, N) is a block of values (n) within a group.")
    detail.table = pd.DataFrame([{"group": names[gi], "replicate": unit, "n": u.size, "mean": float(u.mean()), "SS within": float(np.sum((u - u.mean()) ** 2))}
                                 for gi, g in enumerate(groups) for unit, u in g])
    detail.add("Number of groups, k", "columns with values", k)
    detail.add("Number of replicates, R", "replicates over all groups", r)
    detail.add("Number of values, N", "values over all replicates", int(big_n))
    for gi, name in enumerate(names):
        detail.add(f"{name}: mean of all its values", f"Σx / n = {_g(np.concatenate(units[gi]).sum())} / {int(n_g[gi])}", float(means[gi]), _list_engine(result, "means", gi))
    detail.add("Grand mean", "Σ all values / N", grand)
    ss_g = float(np.sum(n_g * (means - grand) ** 2))
    ss_r = float(sum(np.sum(n * (m - means[i]) ** 2) for i, (n, m) in enumerate(zip(n_gr, unit_means))))
    ss_e = float(sum(np.sum((u - u.mean()) ** 2) for g in units for u in g))
    df_g, df_r, df_e = k - 1, r - k, big_n - r
    ms_g, ms_r, ms_e = ss_g / df_g, ss_r / df_r, ss_e / df_e
    ss = Block("Nested sums of squares and mean squares")
    section = "ANOVA table"

    def sheet(label: str, key: str) -> float | None:
        value = _row_value(result, section, label)
        return float(value[key]) if isinstance(value, dict) and value.get(key) is not None else None

    ss.add("Between groups, SS_groups", "Σ n_g (ȳ_g − ȳ)²", ss_g, sheet("Between groups (columns)", "ss"))
    ss.add("Among replicates, SS_replicates", "Σ n_gr (ȳ_gr − ȳ_g)²", ss_r, sheet("Among replicates (within columns)", "ss"))
    ss.add("Residual, SS_residual", "Σ (y − ȳ_gr)² within replicates", ss_e, sheet("Residual (within replicates)", "ss"))
    ss.add("df groups", f"k − 1 = {k} − 1", df_g, sheet("Between groups (columns)", "df"))
    ss.add("df replicates", f"R − k = {r} − {k}", df_r, sheet("Among replicates (within columns)", "df"))
    ss.add("df residual", f"N − R = {int(big_n)} − {r}", df_e, sheet("Residual (within replicates)", "df"))
    ss.add("MS groups", f"SS / df = {_g(ss_g)} / {df_g}", ms_g, sheet("Between groups (columns)", "ms"))
    ss.add("MS replicates", f"SS / df = {_g(ss_r)} / {df_r}", ms_r, sheet("Among replicates (within columns)", "ms"))
    ss.add("MS residual", f"SS / df = {_g(ss_e)} / {_g(df_e)}", ms_e, sheet("Residual (within replicates)", "ms"))
    within = float(sum(np.sum(n**2) / n.sum() for n in n_gr))
    c_r = (big_n - within) / df_r
    c_g = (within - float(sum(np.sum(n**2) for n in n_gr)) / big_n) / df_g
    ratio = c_g / c_r
    den = Block("Denominator of the group test", note="E(MS replicates) = σ² + c_r σ²_rep, E(MS groups) = σ² + c_g σ²_rep + group effect. With equal numbers of values the denominator is MS replicates; "
                "otherwise it is built from MS replicates and MS residual, with Satterthwaite's df.")
    den.add("Σ over replicates of n_gr² / n_g", "Σ_g Σ_r n_gr² / n_g", within)
    den.add("c_r", f"(N − Σ n_gr²/n_g) / (R − k) = ({int(big_n)} − {_g(within)}) / {df_r}", c_r)
    den.add("c_g", f"(Σ n_gr²/n_g − Σ n_gr²/N) / (k − 1) = {_g(c_g)}", c_g)
    ms_den = ratio * ms_r + (1 - ratio) * ms_e
    parts = (ratio * ms_r) ** 2 / df_r + ((1 - ratio) * ms_e) ** 2 / df_e
    df_den = ms_den**2 / parts if parts > 0 else float(df_r)
    balanced = abs(ratio - 1) < 1e-12
    if balanced:
        ms_den, df_den = ms_r, float(df_r)
    den.add("c_g / c_r", f"{_g(c_g)} / {_g(c_r)}", ratio)
    den.add("MS denominator", "MS replicates" if balanced else f"(c_g/c_r)·MS_replicates + (1 − c_g/c_r)·MS_residual = {_g(ratio)}·{_g(ms_r)} + {_g(1 - ratio)}·{_g(ms_e)}", ms_den)
    df_calc = df_den
    df_set = _df_setting(options, "df", min(float(df_r), df_calc))
    if not balanced:
        den.add("Denominator df (Satterthwaite)", "MS_den² / ((c·MS_rep)²/df_rep + ((1−c)·MS_res)²/df_res)", df_calc, _ev(result, "dfd") if df_set is None else None)
    if df_set is not None:
        df_den = df_set
    den.add("Denominator df used", "MS replicates df, R − k" if balanced and df_set is None else ("set by hand under Advanced" if df_set is not None else "Satterthwaite"), df_den, _ev(result, "dfd"))
    f_g = ms_g / ms_den if ms_den > 0 else NAN
    f_r = ms_r / ms_e if ms_e > 0 else NAN
    test = Block("Tests")
    test.add("F (groups)", f"MS_groups / MS_denominator = {_g(ms_g)} / {_g(ms_den)}", f_g, _ev(result, "f"))
    p_g = float(stats.f.sf(f_g, df_g, df_den)) if math.isfinite(f_g) else NAN
    test.add("P value (groups)", f"P(F{_sub(df_g)},{_sub(df_den)} > {_g(f_g)})", p_g, _ev(result, "p") if k > 2 else sheet("Between groups (columns)", "p"))
    test.add("F (replicates)", f"MS_replicates / MS_residual = {_g(ms_r)} / {_g(ms_e)}", f_r, _ev(result, "f_replicates"))
    test.add("P value (replicates)", f"P(F{_sub(df_r)},{_sub(df_e)} > {_g(f_r)})", float(stats.f.sf(f_r, df_r, df_e)) if math.isfinite(f_r) else NAN, _ev(result, "p_replicates"))
    var_rep_raw = (ms_r - ms_e) / c_r
    var_rep = max(var_rep_raw, 0.0)
    comps = Block("Variance components", note="Method of moments; a negative estimate of the replicate variance is set to 0 for the share below.")
    comps.add("Among replicates, σ²_rep", f"(MS_replicates − MS_residual) / c_r = ({_g(ms_r)} − {_g(ms_e)}) / {_g(c_r)}", var_rep, _ev(result, "var_replicates"))
    comps.add("Within replicates, σ²", "MS_residual", ms_e, _ev(result, "var_residual"))
    comps.add("Intraclass correlation, ICC", f"σ²_rep / (σ²_rep + σ²) = {_g(var_rep)} / ({_g(var_rep)} + {_g(ms_e)})", var_rep / (var_rep + ms_e) if var_rep + ms_e > 0 else NAN, _ev(result, "icc"))
    var_means = np.array([var_rep_raw * np.sum(n**2) / n.sum() ** 2 + ms_e / n.sum() for n in n_gr])
    floor = np.array([ms_e / n.sum() / max(len(n), 1) for n in n_gr])
    var_means = np.maximum(var_means, floor * 1e-6)
    blocks = [detail, ss, den, test, comps]
    if k == 2:
        alt = _alternative(result, options)
        ci = float(_opt(result, options, "ci", 0.95))
        diff = float(means[1] - means[0])
        se = math.sqrt(float(var_means.sum()))
        t = _div(diff, se) if se > 0 else NAN
        two = Block("Nested t test", note="The variance of each group mean comes from the variance components: σ²_rep Σ n_gr² / n_g² + σ² / n_g.")
        for gi in range(2):
            two.add(f"Variance of the mean of {names[gi]}", f"σ²_rep(raw) · Σn_gr²/n_g² + MS_residual / n_g = {_g(var_rep_raw)} · {_g(np.sum(n_gr[gi] ** 2) / n_g[gi] ** 2)} + {_g(ms_e)} / {int(n_g[gi])}", float(var_means[gi]))
        two.add("Difference of means, B − A", f"{_g(means[1])} − {_g(means[0])}", diff, _ev(result, "difference"))
        two.add("Standard error of the difference, SE", f"√(sum of the two variances) = √{_g(var_means.sum())}", se, _ev(result, "se"))
        two.add("t", f"difference / SE = {_g(diff)} / {_g(se)}", t, _ev(result, "t"))
        two.add("Degrees of freedom, df", "denominator df of the group test", df_den, _ev(result, "df"))
        p, formula = _t_tail(t, df_den, alt)
        two.add("P value", formula, p, _ev(result, "p_t") if _ev(result, "p_t") is not None else _ev(result, "p"))
        crit = float(stats.t.ppf(1.0 - (1.0 - ci) / 2.0, df_den))
        two.add(f"Critical value, t* ({100 * ci:g}% CI)", f"t({_g(1 - (1 - ci) / 2)}, df = {_g(df_den)})", crit)
        two.add("Lower limit of the CI", f"difference − t*·SE = {_g(diff)} − {_g(crit)}·{_g(se)}", diff - crit * se, _ev(result, "ci_low"))
        two.add("Upper limit of the CI", f"difference + t*·SE = {_g(diff)} + {_g(crit)}·{_g(se)}", diff + crit * se, _ev(result, "ci_high"))
        blocks.append(two)
    else:
        blocks += _comparison_blocks(result, options, names, means, lambda i, j: (math.sqrt(float(var_means[i] + var_means[j])), float(df_den)), "pooled")
    return blocks


# ---------------------------------------------------------------------------------------------
# Outliers
# ---------------------------------------------------------------------------------------------


@register("outliers")
def _outliers(source: Any, options: dict, result: Any) -> list[Block]:
    method = options.get("method", "rout")
    alpha, q = float(options.get("alpha", 0.05)), float(options.get("q", 0.01))
    chosen = [name for name in (options.get("columns") or source.names) if name in source.names]
    details = (getattr(result, "values", None) or {}).get("details", {})
    found = (getattr(result, "values", None) or {}).get("outliers", {})
    blocks = []
    for column in source.columns:
        if column.name not in chosen:
            continue
        x_all = column.values
        finite = np.isfinite(x_all)
        n_found = len(found.get(column.name, []))
        if method == "rout":
            blocks.append(_rout_block(column.name, x_all, details.get(column.name, {}), q, n_found))
            continue
        positions = list(np.flatnonzero(finite))
        block = Block(f"Grubbs' test: {column.name}" + (" (repeated until no outlier)" if method == "iterative_grubbs" else ""),
                      note="G = largest |x − x̄| / s. The value is an outlier when G exceeds the critical value for alpha (two-sided, Bonferroni over n).")
        rounds_engine = details.get(column.name, {}).get("rounds", [])
        rows = []
        for round_no in range(1, len(positions) + 1):
            if len(positions) < 3:
                break
            x = x_all[positions]
            n = x.size
            mean, sd = float(x.mean()), float(np.std(x, ddof=1))
            if not sd > 0:
                break
            dev = np.abs(x - mean)
            idx = int(np.argmax(dev))
            g = float(dev[idx] / sd)
            tc = float(stats.t.isf(alpha / (2 * n), n - 2))
            critical = (n - 1) / math.sqrt(n) * math.sqrt(tc * tc / (n - 2 + tc * tc))
            denom = (n - 1) ** 2 - n * g * g
            p = 0.0 if denom <= 0 else float(min(1.0, 2 * n * stats.t.sf(math.sqrt(n * (n - 2) * g * g / denom), n - 2)))
            tag = f"round {round_no}: " if method == "iterative_grubbs" else ""
            engine = rounds_engine[round_no - 1] if round_no - 1 < len(rounds_engine) else {}
            block.add(f"{tag}n", "values left", n, engine.get("n"))
            block.add(f"{tag}mean", f"Σx / n", mean)
            block.add(f"{tag}SD", "√(Σ(x − x̄)² / (n − 1))", sd)
            block.add(f"{tag}most extreme value", f"the value furthest from the mean (row {positions[idx] + 1})", float(x_all[positions[idx]]), engine.get("value"))
            block.add(f"{tag}G", f"|x − x̄| / s = {_g(dev[idx])} / {_g(sd)}", g, engine.get("g"))
            block.add(f"{tag}t for the critical value", f"t(α/(2n), n − 2) upper point = t({_g(alpha / (2 * n))}, df = {n - 2})", tc)
            block.add(f"{tag}critical G", f"(n − 1)/√n · √(t² / (n − 2 + t²)) = {n - 1}/√{n} · √({_g(tc * tc)} / ({n - 2} + {_g(tc * tc)}))", critical, engine.get("critical"))
            block.add(f"{tag}approximate P", f"min(1, 2n · P(T{_sub(n - 2)} > √(n(n−2)G² / ((n−1)² − nG²)))) = min(1, {2 * n} · P(T{_sub(n - 2)} > {_g(math.sqrt(n * (n - 2) * g * g / denom)) if denom > 0 else '∞'}))",
                      p, engine.get("p"))
            outlier = g > critical
            block.add(f"{tag}outlier?", f"G > critical G: {_g(g)} > {_g(critical)}", outlier, engine.get("outlier"))
            rows.append({"round": round_no, "n": n, "row": positions[idx] + 1, "value": float(x_all[positions[idx]]), "G": g, "critical G": critical, "outlier": outlier})
            if not outlier or method != "iterative_grubbs":
                break
            del positions[idx]
        block.table = pd.DataFrame(rows)
        block.add("Number of outliers", "values flagged", int(sum(r["outlier"] for r in rows)), n_found)
        blocks.append(block)
    return blocks


def _rout_block(name: str, x_all: np.ndarray, detail: dict, q: float, n_found: int) -> Block:
    """ROUT: the robust fit is found by iteration and is the analysis's own; the t, P and Benjamini-Hochberg step are redone from it."""

    block = Block(f"ROUT: {name}", note="The robust centre and robust SD (RSDR) come from an iterative fit of a Lorentzian merit function, which is not redone here; "
                  "they are the analysis's own values. Each value's t and P, and the Benjamini-Hochberg step, are computed from them below.")
    finite = np.isfinite(x_all)
    x = x_all[finite]
    n = x.size
    block.add("Number of values, n", "count of non-blank cells", n, detail.get("n"))
    centre, rsdr = detail.get("centre"), detail.get("rsdr")
    if n < 3 or centre is None or not (isinstance(rsdr, float) and rsdr > 0) or not math.isfinite(centre):
        block.add("Outliers found", "needs at least 3 values and a robust SD above 0", 0, n_found)
        return block
    _by_engine(block, "Robust centre, μ", "the iterative robust fit", float(centre))
    _by_engine(block, "Robust SD of the residuals, RSDR", "the iterative robust fit", float(rsdr))
    t = np.abs(x - centre) / rsdr
    p = 2 * stats.t.sf(t, n - 1)
    order = np.argsort(p, kind="mergesort")
    cap = max(1, int(math.floor(0.30 * n + 1e-9)))
    threshold = q * np.arange(1, n + 1) / n
    passing = np.nonzero(p[order] <= threshold)[0]
    passing = passing[passing < cap]
    flagged = np.zeros(n, bool)
    if passing.size:
        flagged[order[: passing.max() + 1]] = True
    rank_of = np.empty(n, int)
    rank_of[order] = np.arange(1, n + 1)
    block.table = pd.DataFrame({"row": np.flatnonzero(finite) + 1, "value": x, "t = |x − μ|/RSDR": t, "P": p, "P rank j": rank_of, "j·Q/n": q * rank_of / n,
                                "flagged": flagged}).sort_values("P rank j").reset_index(drop=True)
    block.add("Q (desired false discovery rate)", "set in the options", q)
    block.add("Most values that may be flagged", f"30% of n, rounded down, at least 1 = {cap}", cap)
    block.add("Outliers found", "the j most extreme values for the largest j with P(j) ≤ j·Q/n (and j ≤ the limit above)", int(flagged.sum()), n_found)
    return block


# ---------------------------------------------------------------------------------------------
# Column statistics and normality
# ---------------------------------------------------------------------------------------------


def _wide_text(result: Any, name: str, section: str, label: str) -> str | None:
    """The text the sheet shows for one column's statistic."""

    if not isinstance(result, pd.DataFrame) or name not in result.columns:
        return None
    for (sec, stat), text in result[name].items():
        if sec == section and stat == label:
            return str(text)
    return None


def _shown(value: float, text: str | None) -> Any:
    """What to pass as the engine value when the sheet shows ``text`` (a rounded number): the value itself when it rounds to that text, else the text."""

    if text is None:
        return None
    cleaned = text.rstrip("%")
    return value if fmt(value) == cleaned or fmt(value) == text else text


@register("colstats")
def _colstats(source: Any, options: dict, result: Any) -> list[Block]:
    ci = float(options.get("ci", 0.95))
    level = f"{100 * ci:g}%"
    blocks = []
    for column in source.columns:
        x = column.finite
        n = x.size
        block = Block(f"Column statistics: {column.name}",
                      note="The sheet shows 4 significant digits; a check mark means the value here rounds to what the sheet shows." if isinstance(result, pd.DataFrame) else "")
        shown = lambda label, value: _shown(value, _wide_text(result, column.name, "Column statistics", label))  # noqa: E731
        block.add("Number of values, n", "count of non-blank cells", n, shown("Number of values", float(n)))
        if n == 0:
            blocks.append(block)
            continue
        mean = float(x.mean())
        ss = float(np.sum((x - mean) ** 2))
        sd = math.sqrt(ss / (n - 1)) if n > 1 else NAN
        sem = sd / math.sqrt(n) if n > 1 else NAN
        crit = float(stats.t.ppf(1 - (1 - ci) / 2, n - 1)) if n > 1 else NAN
        half = crit * sem
        block.add("Sum", "Σx", float(x.sum()), shown("Sum", float(x.sum())))
        block.add("Mean, x̄", f"Σx / n = {_g(x.sum())} / {n}", mean, shown("Mean", mean))
        block.add("Sum of squared deviations", "Σ(x − x̄)²", ss)
        block.add("Standard deviation, SD", f"√(Σ(x − x̄)² / (n − 1)) = √({_g(ss)} / {n - 1})", sd, shown("Std. Deviation", sd))
        block.add("Standard error of the mean, SEM", f"SD / √n = {_g(sd)} / √{n}", sem, shown("Std. Error of Mean", sem))
        block.add(f"Critical value, t* ({level} CI)", f"t({_g(1 - (1 - ci) / 2)}, df = {n - 1})", crit)
        block.add(f"Lower {level} limit of the mean", f"x̄ − t*·SEM = {_g(mean)} − {_g(crit)}·{_g(sem)}", mean - half, shown(f"Lower {level} CI of mean", mean - half))
        block.add(f"Upper {level} limit of the mean", f"x̄ + t*·SEM = {_g(mean)} + {_g(crit)}·{_g(sem)}", mean + half, shown(f"Upper {level} CI of mean", mean + half))
        median = float(np.median(x))
        block.add("Median", "middle value of the sorted data (mean of the two middle values when n is even)", median, shown("Median", median) if _wide_text(result, column.name, "Column statistics", "Median") else None)
        block.add("Minimum", "smallest value", float(x.min()), shown("Minimum", float(x.min())))
        block.add("Maximum", "largest value", float(x.max()), shown("Maximum", float(x.max())))
        if n > 1 and mean != 0:
            block.add("Coefficient of variation, CV", f"100 · SD / mean = 100 · {_g(sd)} / {_g(mean)}", 100 * sd / mean, None)
        block.table = pd.DataFrame({"x": np.sort(x), "x − x̄": np.sort(x) - mean, "(x − x̄)²": (np.sort(x) - mean) ** 2})
        blocks.append(block)
    return blocks


@register("normality")
def _normality(source: Any, options: dict, result: Any) -> list[Block]:
    blocks = []
    for column in source.columns:
        x = column.finite
        n = x.size
        for prefix, values in (("Normality", x), ("Lognormality", np.log(x) if n and np.all(x > 0) else None)):
            if values is None:
                continue
            m = values.size
            mean = float(values.mean()) if m else NAN
            sd = float(np.std(values, ddof=1)) if m > 1 else NAN
            block = Block(f"{prefix}: {column.name}", note="The tests themselves (D'Agostino-Pearson, Shapiro-Wilk, Anderson-Darling, Kolmogorov-Smirnov with the Lilliefors P) are not redone by hand; "
                          "their statistics and P values are the analysis's own, as the sheet shows them." + (" The tests are run on the natural logs of the values." if prefix == "Lognormality" else ""))
            block.add("Number of values, n", "count of non-blank cells", m)
            block.add("Mean" + (" of the logs" if prefix == "Lognormality" else ""), f"Σx / n = {_g(values.sum())} / {m}" if m else "", mean)
            block.add("SD" + (" of the logs" if prefix == "Lognormality" else ""), "√(Σ(x − x̄)² / (n − 1))", sd)
            if isinstance(result, pd.DataFrame) and column.name in result.columns:
                for (section, label), text in result[column.name].items():
                    if str(section).startswith(prefix + ":"):
                        number: Any = text
                        try:
                            number = float(text)
                        except (TypeError, ValueError):
                            pass
                        block.add(f"{str(section).split(': ', 1)[1]}: {label}", "computed by the test; as shown on the sheet", number)
            blocks.append(block)
    return blocks
