"""Show working for models (see cellquant.stats.steps): two-way ANOVA, linear regression, XY correlation, curve fitting,
correlation matrix and principal component analysis."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from cellquant.stats.results import fmt
from cellquant.stats.steps import Block, register

NAN = float("nan")
_SUB = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def _f(value: Any, digits: int = 5) -> str:
    return fmt(value, digits) if value is not None else ""


def _sub(value: Any) -> str:
    return _f(value).translate(_SUB)


def _t_two_sided(t: float, df: float) -> float:
    return float(2 * stats.t.sf(abs(t), df)) if math.isfinite(t) else NAN


def _p_t_text(t: float, df: float) -> str:
    return f"2 · P(T{_sub(df)} > {_f(abs(t))})"


def _p_f_text(f: float, dfn: float, dfd: float) -> str:
    return f"P(F{_sub(dfn)},{_sub(dfd)} > {_f(f)})"


def _f_p(f: float, dfn: float, dfd: float) -> float:
    return float(stats.f.sf(f, dfn, dfd)) if math.isfinite(f) and dfn > 0 and dfd > 0 else NAN


def _df_option(options: dict) -> float | None:
    """The residual df set by hand (Advanced), or None."""

    value = options.get("df")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _zero_if_tiny(ss: float, total: float, y: np.ndarray) -> float:
    """No scatter beyond rounding counts as 0, as in the analyses."""

    return 0.0 if ss <= 1e-12 * max(total, float((y**2).sum()), 1e-300) else ss


def _level(options: dict) -> float:
    try:
        return float(options.get("ci", 0.95))
    except (TypeError, ValueError):
        return 0.95


# ---------------------------------------------------------------------------------------------
# Two-way ANOVA
# ---------------------------------------------------------------------------------------------


def _codes(index: np.ndarray, levels: int) -> np.ndarray:
    """Sum-to-zero coding: level l < L-1 is unit vector l; the last level is -1 in every column."""

    out = np.zeros((index.size, levels - 1))
    for position, level in enumerate(index):
        if level == levels - 1:
            out[position] = -1.0
        else:
            out[position, level] = 1.0
    return out


def _rss(X: np.ndarray, y: np.ndarray) -> tuple[float, int]:
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    resid = y - X @ beta
    return float(resid @ resid), int(np.linalg.matrix_rank(X))


@register("twoway")
def _twoway(table, options: dict, result) -> list[Block]:
    r, c = len(table.rows), len(table.columns)
    I, J, Y = [], [], []
    for i in range(r):
        for j in range(c):
            x = table.cell(i, j)
            I += [i] * x.size
            J += [j] * x.size
            Y += list(x)
    I, J, Y = np.array(I, int), np.array(J, int), np.array(Y, float)
    n = Y.size
    counts = np.zeros((r, c), int)
    np.add.at(counts, (I, J), 1)
    wanted = options.get("interaction")
    interaction = (True if wanted is None else bool(wanted)) and n > r * c
    ev = result.values
    blocks = []

    # Cell means
    cells = pd.DataFrame([{"row": table.rows[i], "data set": table.columns[j], "n": int(counts[i, j]),
                           "sum": float(table.cell(i, j).sum()), "mean": float(table.cell(i, j).mean()) if counts[i, j] else NAN,
                           "SD": float(table.cell(i, j).std(ddof=1)) if counts[i, j] > 1 else NAN} for i in range(r) for j in range(c)])
    grand = float(Y.mean())
    ss_total = float(((Y - grand) ** 2).sum())
    b = Block("Cell means", table=cells)
    b.add("Number of values N", "count of all values", n, ev.get("n"))
    b.add("Grand mean ȳ", f"Σy / N = {_f(Y.sum())} / {n}", grand)
    b.add("Total sum of squares SS_total", "Σ(y − ȳ)²", ss_total, ev.get("ss_total"))
    b.add("Interaction in the model", f"{'yes' if interaction else 'no'} (needs more values than the {r * c} cells)", interaction,
          ev.get("interaction"))
    b.note = ("Model: y = μ + α_row + β_data set" + (" + (αβ)_interaction" if interaction else "") + " + ε, sum-to-zero coding. "
              + ("" if counts.min() == counts.max() else "Cells have different numbers of values, so each effect's SS is Type III: "
                 "the rise in the residual SS when that effect alone is left out of the full model. "))
    blocks.append(b)

    # Sums of squares by the model comparison
    a, bb = _codes(I, r), _codes(J, c)
    parts = {"intercept": np.ones((n, 1)), "row": a, "column": bb}
    if interaction:
        parts["interaction"] = np.einsum("na,nb->nab", a, bb).reshape(n, -1)
    names = {"row": "Row factor", "column": "Column factor", "interaction": "Interaction"}

    def design(skip: str | None = None) -> np.ndarray:
        return np.hstack([m for key, m in parts.items() if key != skip])

    rss, rank = _rss(design(), Y)
    df_res = n - rank
    rss = _zero_if_tiny(rss, ss_total, Y)
    ms_res = rss / df_res if df_res > 0 else NAN
    used = _df_option(options)
    df_p = used if used is not None else df_res
    sums = Block("Sums of squares (full model against the model without each effect)")
    sums.add("Residual SS of the full model", ("Σ(y − cell mean)²" if interaction else "Σ(y − fitted)²") + " by least squares", rss,
             ev.get("ss_resid"))
    sums.add("Rank of the full model", "number of independent parameters", rank)
    sums.add("Residual df", f"N − rank = {n} − {rank}", df_res, ev.get("df_resid") if used is None else None)
    sums.add("Residual mean square MS_resid", f"SS_resid / df = {_f(rss)} / {df_res}", ms_res, ev.get("ms_resid"))
    terms = {}
    for key in ("interaction", "row", "column"):
        if key not in parts:
            continue
        reduced, reduced_rank = _rss(design(key), Y)
        df = rank - reduced_rank
        ss = max(reduced - rss, 0.0)
        terms[key] = (ss, df)
        sums.add(f"{names[key]}: residual SS without it", "least squares without this effect", reduced)
        sums.add(f"{names[key]}: SS", f"{_f(reduced)} − {_f(rss)}", ss)
        sums.add(f"{names[key]}: df", f"rank {rank} − rank without it {reduced_rank}", df)
    blocks.append(sums)
    by_term = {row["term"]: row for row in ev.get("anova", [])}
    for key, (ss, df) in terms.items():
        e = by_term.get(key, {})
        sums.add(f"{names[key]}: SS (analysis)", "as above", ss, e.get("ss"))
        sums.add(f"{names[key]}: df (analysis)", "as above", df, e.get("df"))

    # Balanced shortcut: the classical formulas
    if counts.min() == counts.max():
        m = int(counts.flat[0])
        mean = np.array([[table.cell(i, j).mean() for j in range(c)] for i in range(r)])
        row_means, col_means = mean.mean(axis=1), mean.mean(axis=0)
        short = Block("Equal numbers per cell: the classical formulas")
        ss_row = c * m * float(((row_means - grand) ** 2).sum())
        ss_col = r * m * float(((col_means - grand) ** 2).sum())
        short.add("SS_row", f"c · n · Σ(row mean − ȳ)² = {c} · {m} · {_f(((row_means - grand) ** 2).sum())}", ss_row, by_term.get("row", {}).get("ss"))
        short.add("SS_column", f"r · n · Σ(column mean − ȳ)² = {r} · {m} · {_f(((col_means - grand) ** 2).sum())}", ss_col,
                  by_term.get("column", {}).get("ss"))
        if interaction:
            inter = m * float(((mean - row_means[:, None] - col_means[None, :] + grand) ** 2).sum())
            short.add("SS_interaction", "n · Σ(cell mean − row mean − column mean + ȳ)²", inter, by_term.get("interaction", {}).get("ss"))
            within = float(sum(((table.cell(i, j) - mean[i, j]) ** 2).sum() for i in range(r) for j in range(c)))
            short.add("SS_residual", "Σ(y − cell mean)²", within, ev.get("ss_resid"))
        else:
            short.add("SS_residual", "SS_total − SS_row − SS_column", ss_total - ss_row - ss_col, ev.get("ss_resid"))
        blocks.append(short)

    # ANOVA table
    anova = Block("ANOVA table")
    rows = []
    for key, (ss, df) in terms.items():
        ms = ss / df if df else NAN
        f = ms / ms_res if ms_res and ms_res > 0 else NAN
        p = _f_p(f, df, df_p)
        e = by_term.get(key, {})
        anova.add(f"{names[key]}: mean square", f"SS / df = {_f(ss)} / {df}", ms, e.get("ms"))
        anova.add(f"{names[key]}: F", f"MS / MS_resid = {_f(ms)} / {_f(ms_res)}", f, e.get("f"))
        anova.add(f"{names[key]}: P", _p_f_text(f, df, df_p), p, e.get("p"))
        rows.append({"source": names[key], "SS": ss, "df": df, "MS": ms, "F": f, "P": p})
    rows.append({"source": "Residual", "SS": rss, "df": df_res, "MS": ms_res, "F": NAN, "P": NAN})
    rows.append({"source": "Total", "SS": ss_total, "df": n - 1, "MS": NAN, "F": NAN, "P": NAN})
    anova.table = pd.DataFrame(rows)
    if used is not None:
        anova.note = f"The residual df was set by hand to {_f(used)}: it is used for every P (F and MS use the computed {df_res})."
        anova.add("Residual df used for P", "set by hand (Advanced)", used, ev.get("df_resid"))
    blocks.append(anova)
    return blocks


# ---------------------------------------------------------------------------------------------
# Linear regression
# ---------------------------------------------------------------------------------------------


def _line_block(name: str, x: np.ndarray, y: np.ndarray, origin: bool, ci: float, df_set: float | None, engine: dict | None,
                alpha_note: bool = True) -> tuple[Block, dict]:
    e = engine or {}
    n = x.size
    params = 1 if origin else 2
    block = Block(f"Data set {name}")
    mx, my = float(x.mean()), float(y.mean())
    if origin:
        sxx, sxy, syy = float((x**2).sum()), float((x * y).sum()), float((y**2).sum())
        slope, intercept = sxy / sxx, 0.0
        block.add("Points n", "count", n, e.get("n"))
        block.add("Σx²", "Σx²", sxx)
        block.add("Σxy", "Σxy", sxy)
        block.add("Σy² (the total SS, not centred)", "Σy²", syy)
        block.add("Slope a", f"Σxy / Σx² = {_f(sxy)} / {_f(sxx)}", slope, e.get("slope"))
        block.add("Y-intercept b", "forced to 0", 0.0, e.get("intercept"))
        cov_slope, cov_int = 1 / sxx, 0.0
        ss_total = syy
    else:
        sxx = float(((x - mx) ** 2).sum())
        sxy = float(((x - mx) * (y - my)).sum())
        syy = float(((y - my) ** 2).sum())
        slope = sxy / sxx
        intercept = my - slope * mx
        block.add("Points n", "count", n, e.get("n"))
        block.add("Mean of X, x̄", f"Σx / n = {_f(x.sum())} / {n}", mx, e.get("x_mean"))
        block.add("Mean of Y, ȳ", f"Σy / n = {_f(y.sum())} / {n}", my)
        block.add("S_xx", "Σ(x − x̄)²", sxx)
        block.add("S_xy", "Σ(x − x̄)(y − ȳ)", sxy)
        block.add("S_yy (SS_total)", "Σ(y − ȳ)²", syy)
        block.add("Slope a", f"S_xy / S_xx = {_f(sxy)} / {_f(sxx)}", slope, e.get("slope"))
        block.add("Y-intercept b", f"ȳ − a·x̄ = {_f(my)} − {_f(slope)} · {_f(mx)}", intercept, e.get("intercept"))
        cov_slope, cov_int = 1 / sxx, 1 / n + mx**2 / sxx
        ss_total = syy
    fitted = slope * x + intercept
    resid = y - fitted
    ss_res = _zero_if_tiny(float((resid**2).sum()), ss_total, y)
    df_c = n - params
    df = df_set if df_set is not None else df_c
    ms = ss_res / df_c
    sy = math.sqrt(ms)
    detail = pd.DataFrame({"x": x, "y": y, "ŷ": fitted, "residual": resid, "residual²": resid**2})
    if not origin:
        detail.insert(2, "(x−x̄)(y−ȳ)", (x - mx) * (y - my))
        detail.insert(2, "(x−x̄)²", (x - mx) ** 2)
    block.table = detail
    block.add("Residual SS", "Σ(y − ŷ)²", ss_res, e.get("ss_resid"))
    block.add("Residual df", f"n − {params} = {n} − {params}", df_c, e.get("df_computed", e.get("df")))
    block.add("Residual mean square", f"SS_res / df = {_f(ss_res)} / {df_c}", ms, e.get("ms"))
    block.add("Sy.x", f"√MS_res = √{_f(ms)}", sy, e.get("sy_x"))
    se_slope = sy * math.sqrt(cov_slope)
    se_int = sy * math.sqrt(cov_int)
    block.add("SE of slope", f"Sy.x / √S_xx = {_f(sy)} / √{_f(sxx)}", se_slope, e.get("se_slope"))
    if not origin:
        block.add("SE of intercept", f"Sy.x · √(1/n + x̄²/S_xx) = {_f(sy)} · √(1/{n} + {_f(mx)}²/{_f(sxx)})", se_int, e.get("se_intercept"))
    t_crit = float(stats.t.ppf(0.5 + ci / 2, df))
    block.add(f"Critical t* ({100 * ci:g}% CI)", f"t* = T{_sub(df)} quantile at {_f(0.5 + ci / 2)}", t_crit)
    sci = e.get("slope_ci", (None, None))
    block.add("Slope CI lower", f"a − t*·SE = {_f(slope)} − {_f(t_crit)} · {_f(se_slope)}", slope - t_crit * se_slope, sci[0])
    block.add("Slope CI upper", f"a + t*·SE", slope + t_crit * se_slope, sci[1])
    if not origin:
        ici = e.get("intercept_ci", (None, None))
        block.add("Intercept CI lower", f"b − t*·SE = {_f(intercept)} − {_f(t_crit)} · {_f(se_int)}", intercept - t_crit * se_int, ici[0])
        block.add("Intercept CI upper", "b + t*·SE", intercept + t_crit * se_int, ici[1])
    t_slope = slope / se_slope if se_slope > 0 else NAN
    block.add("t of the slope", f"a / SE = {_f(slope)} / {_f(se_slope)}", t_slope)
    p_t = _t_two_sided(t_slope, df)
    block.add("P of the slope (t test)", _p_t_text(t_slope, df), p_t, e.get("p"))
    r2 = 1 - ss_res / ss_total if ss_total > 0 else NAN
    block.add("R²", f"1 − SS_res / SS_total = 1 − {_f(ss_res)} / {_f(ss_total)}", r2, e.get("r2"))
    f = (ss_total - ss_res) / ms if ms > 0 else NAN
    block.add("F (slope ≠ 0)", f"(SS_total − SS_res) / MS_res = ({_f(ss_total)} − {_f(ss_res)}) / {_f(ms)}", f, e.get("f"))
    block.add("P of F", _p_f_text(f, 1, df), _f_p(f, 1, df), e.get("p"))
    if not origin:
        block.add("X-intercept", f"−b / a = −{_f(intercept)} / {_f(slope)}", -intercept / slope if slope else NAN, e.get("x_intercept"))
    notes = ["F = t², so the t test of the slope and the F test have the same P."]
    if df_set is not None:
        notes.append(f"The residual df was set by hand to {_f(df_set)}: it is used for t*, the CIs and P; MS_res divides by {df_c}.")
    if origin:
        notes.append("Through the origin R² uses Σy² as the total, so it is not comparable with an ordinary R².")
    block.note = " ".join(notes)
    return block, {"ss_res": ss_res, "df": df_c, "sxx": sxx, "sxy": sxy, "syy": syy, "x": x, "y": y}


@register("linreg")
def _linreg(table, options: dict, result) -> list[Block]:
    origin = bool(options.get("through_origin", False))
    means = options.get("replicates", "each") == "mean"
    ci = _level(options)
    df_set = _df_option(options)
    fits = result.values.get("fits", {})
    comparison = result.values.get("comparison")
    blocks, mine = [], {}
    params = 1 if origin else 2
    for j, name in enumerate(table.sets):
        x, y = table.points(j, means=means)
        if x.size < params + 1 or np.ptp(x) == 0:
            continue
        block, info = _line_block(name, x, y, origin, ci, df_set, fits.get(name))
        blocks.append(block)
        mine[name] = info
    if len(mine) >= 2:
        m = len(mine)
        infos = list(mine.values())
        ss_sep = sum(i["ss_res"] for i in infos)
        df_sep = sum(i["df"] for i in infos)
        total_n = sum(i["x"].size for i in infos)
        sxx_sum, sxy_sum = sum(i["sxx"] for i in infos), sum(i["sxy"] for i in infos)
        syy_sum = sum(i["syy"] for i in infos)
        ss_common = syy_sum - sxy_sum**2 / sxx_sum
        df_common = total_n - 1 if origin else total_n - m - 1
        e = comparison or {}
        b = Block("Comparing the data sets (analysis of covariance)")
        b.add("SS of the separate lines", "Σ SS_res of each data set", ss_sep)
        b.add("df of the separate lines", "Σ (n − parameters)", df_sep, e.get("slopes_dfd"))
        b.add("Common slope", ("Σxy / Σx² over all data sets" if origin else "Σ S_xy / Σ S_xx over the data sets"), sxy_sum / sxx_sum)
        b.add("SS with one common slope", ("Σy² − (Σxy)² / Σx²" if origin else "Σ S_yy − (Σ S_xy)² / Σ S_xx"), ss_common)
        b.add("df with one common slope", f"N − {1 if origin else m + 1}", df_common)
        dfn = df_common - df_sep
        ms_sep = ss_sep / df_sep if df_sep > 0 else NAN
        f = (ss_common - ss_sep) / dfn / ms_sep if ms_sep > 0 and dfn > 0 else NAN
        b.add("DFn for the slopes", "df_common − df_separate", dfn, e.get("slopes_dfn"))
        b.add("F, are the slopes equal?", f"((SS_common − SS_sep) / DFn) / (SS_sep / df_sep) = (({_f(ss_common)} − {_f(ss_sep)}) / {dfn}) / ({_f(ss_sep)} / {df_sep})",
              f, e.get("slopes_f"))
        b.add("P for the slopes", _p_f_text(f, dfn, df_sep), _f_p(f, dfn, df_sep), e.get("slopes_p"))
        if not origin:
            x_all = np.concatenate([i["x"] for i in infos])
            y_all = np.concatenate([i["y"] for i in infos])
            sxx_all = float(((x_all - x_all.mean()) ** 2).sum())
            sxy_all = float(((x_all - x_all.mean()) * (y_all - y_all.mean())).sum())
            syy_all = float(((y_all - y_all.mean()) ** 2).sum())
            ss_single = syy_all - sxy_all**2 / sxx_all
            ms_common = ss_common / df_common if df_common > 0 else NAN
            f2 = (ss_single - ss_common) / (m - 1) / ms_common if ms_common > 0 else NAN
            b.add("SS with one line for everything", "S_yy − S_xy² / S_xx over all points pooled", ss_single)
            b.add("F, are the intercepts equal?", f"((SS_single − SS_common) / {m - 1}) / (SS_common / {df_common})", f2, e.get("intercepts_f"))
            b.add("P for the intercepts", _p_f_text(f2, m - 1, df_common), _f_p(f2, m - 1, df_common), e.get("intercepts_p"))
            b.note = "The sheet shows the intercept test only when the slopes are not significantly different (parallel lines)."
        blocks.append(b)
    return blocks


# ---------------------------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------------------------


def _pearson_steps(block: Block, x: np.ndarray, y: np.ndarray, ci: float, symbol: str, df_set: float | None, e: dict) -> dict:
    n = x.size
    mx, my = float(x.mean()), float(y.mean())
    sxx, syy = float(((x - mx) ** 2).sum()), float(((y - my) ** 2).sum())
    sxy = float(((x - mx) * (y - my)).sum())
    block.add("Pairs n", "count of pairs with both values", n, e.get("n"))
    block.add("Mean of x", "Σx / n", mx)
    block.add("Mean of y", "Σy / n", my)
    block.add("S_xx", "Σ(x − x̄)²", sxx)
    block.add("S_yy", "Σ(y − ȳ)²", syy)
    block.add("S_xy", "Σ(x − x̄)(y − ȳ)", sxy)
    r = sxy / math.sqrt(sxx * syy)
    block.add(symbol, f"S_xy / √(S_xx·S_yy) = {_f(sxy)} / √({_f(sxx)} · {_f(syy)})", r, e.get("r"))
    block.add(f"{symbol}²", f"{symbol}·{symbol}", r * r, e.get("r2"))
    df = df_set if df_set is not None else n - 2
    t = r * math.sqrt(n - 2) / math.sqrt(1 - r * r) if abs(r) < 1 else math.copysign(math.inf, r)
    block.add("t", f"{symbol}·√(n − 2) / √(1 − {symbol}²) = {_f(r)} · √{n - 2} / √(1 − {_f(r * r)})", t)
    p = 0.0 if abs(r) >= 1 else _t_two_sided(t, df)
    block.add("df", "n − 2" if df_set is None else "set by hand (Advanced)", df)
    block.add("P (two-tailed)", _p_t_text(t, df), p, e.get("p"))
    low = high = NAN
    if n > 3 and abs(r) < 1:
        z = math.atanh(r)
        se = 1 / math.sqrt(n - 3)
        zc = float(stats.norm.ppf(0.5 + ci / 2))
        block.add("Fisher z", f"atanh({symbol}) = {_f(z)}", z)
        block.add("SE of z", f"1 / √(n − 3) = 1 / √{n - 3}", se)
        block.add(f"Critical z* ({100 * ci:g}% CI)", "standard normal quantile", zc)
        low, high = math.tanh(z - zc * se), math.tanh(z + zc * se)
        block.add("CI lower", f"tanh(z − z*·SE) = tanh({_f(z)} − {_f(zc)} · {_f(se)})", low, e.get("ci_low"))
        block.add("CI upper", "tanh(z + z*·SE)", high, e.get("ci_high"))
    elif abs(r) >= 1:
        low = high = r
        block.add("CI", f"|{symbol}| = 1: the limits are {symbol}", low, e.get("ci_low"))
    else:
        block.add("CI", "needs n > 3", NAN, e.get("ci_low"))
    return {"r": r, "t": t, "p": p, "low": low, "high": high, "n": n, "df": df}


@register("xycorr")
def _xycorr(table, options: dict, result) -> list[Block]:
    method = options.get("method", "pearson")
    means = options.get("replicates", "each") == "mean"
    ci = _level(options)
    df_set = _df_option(options)
    values = result.values.get("correlations", {})
    blocks = []
    for j, name in enumerate(table.sets):
        x, y = table.points(j, means=means)
        if x.size < 3 or np.ptp(x) == 0 or np.ptp(y) == 0:
            continue
        block = Block(f"Data set {name}")
        e = values.get(name, {})
        if method == "spearman":
            rx, ry = stats.rankdata(x), stats.rankdata(y)
            block.table = pd.DataFrame({"x": x, "y": y, "rank x": rx, "rank y": ry})
            block.note = ("Spearman rs is Pearson's formula on the ranks (ties share their average rank); the CI by Fisher's z is approximate.")
            _pearson_steps(block, rx, ry, ci, "rs", df_set, e)
        else:
            block.table = pd.DataFrame({"x": x, "y": y, "x−x̄": x - x.mean(), "y−ȳ": y - y.mean(), "(x−x̄)(y−ȳ)": (x - x.mean()) * (y - y.mean())})
            _pearson_steps(block, x, y, ci, "r", df_set, e)
        blocks.append(block)
    return blocks


# ---------------------------------------------------------------------------------------------
# Curve fitting
# ---------------------------------------------------------------------------------------------


def _poly(x, p):
    return sum(coef * x**power for power, coef in enumerate(p))


# key: (parameters, function of (x, p), linear in the parameters)
_MODELS = {
    "log_dose_response": (("Bottom", "Top", "LogEC50", "HillSlope"),
                          lambda x, p: p[0] + (p[1] - p[0]) / (1 + np.power(10.0, (p[2] - x) * p[3])), False),
    "dose_response": (("Bottom", "Top", "EC50", "HillSlope"), lambda x, p: p[0] + (p[1] - p[0]) / (1 + np.power(p[2] / x, p[3])), False),
    "michaelis_menten": (("Vmax", "Km"), lambda x, p: p[0] * x / (p[1] + x), False),
    "one_phase": (("Y0", "Plateau", "K"), lambda x, p: p[1] + (p[0] - p[1]) * np.exp(-p[2] * x), False),
    "exponential_growth": (("Y0", "K"), lambda x, p: p[0] * np.exp(p[1] * x), False),
    "gaussian": (("Amplitude", "Mean", "SD"), lambda x, p: p[0] * np.exp(-0.5 * ((x - p[1]) / p[2]) ** 2), False),
    "line": (("Intercept", "Slope"), _poly, True),
    "polynomial_2": (("B0", "B1", "B2"), _poly, True),
    "polynomial_3": (("B0", "B1", "B2", "B3"), _poly, True),
}


def _aicc(ss: float, n: int, k: int) -> float:
    k += 1
    if n - k - 1 <= 0:
        return NAN
    return n * math.log(max(ss, 1e-300) / n) + 2 * k + 2 * k * (k + 1) / (n - k - 1)


def _jacobian(f, x: np.ndarray, p: np.ndarray, index: list[int]) -> np.ndarray:
    """Central differences of the model in each free parameter, step 6e-6 of the parameter."""

    columns = []
    with np.errstate(all="ignore"):
        for i in index:
            h = 6e-6 * abs(p[i]) if p[i] != 0 else 6e-6
            up, down = p.copy(), p.copy()
            up[i] += h
            down[i] -= h
            columns.append((f(x, up) - f(x, down)) / (up[i] - down[i]))
    return np.column_stack(columns)


def _cov_unscaled(J: np.ndarray) -> np.ndarray:
    """(JᵀJ)⁻¹ with the columns of J scaled to unit length first, so parameters in very different units invert accurately."""

    norms = np.linalg.norm(J, axis=0)
    Jn = J / norms
    return np.linalg.inv(Jn.T @ Jn) / np.outer(norms, norms)


def _fit_block(name: str, model: str, x: np.ndarray, y: np.ndarray, fit: dict, ci: float) -> list[Block]:
    names, f, linear = _MODELS[model]
    free, fixed = fit["free"], fit["fixed"]
    n, k = x.size, len(free)
    p = np.array([fit["params"][q] for q in names], float)
    index = [names.index(q) for q in free]
    blocks = []
    b = Block(f"Data set {name}: fitted parameters")
    for q in names:
        if q in fixed:
            b.add(q, "fixed by you", fit["params"][q], fixed[q])
        elif linear:
            base = np.array([fit["params"][o] if o in fixed else 0.0 for o in names], float)
            A = np.column_stack([x ** names.index(o) for o in free])
            with np.errstate(all="ignore"):
                target = y - f(x, base)
            solved = dict(zip(free, np.linalg.lstsq(A, target, rcond=None)[0]))
            b.add(q, "least squares (linear in the parameters)", float(solved[q]), fit["params"][q])
        else:
            b.add(q, "computed by iterative least squares (no closed form)", fit["params"][q])
    if not linear:
        b.note = ("The parameters come from an iterative search for the smallest sum of squares; it cannot be repeated by hand, "
                  "but every number below is computed from these values.")
    if fit.get("equation"):
        b.note = (fit["equation"] + ". " + b.note).strip()
    blocks.append(b)
    with np.errstate(all="ignore"):
        fitted = f(x, p)
    resid = y - fitted
    d = Block(f"Data set {name}: goodness of fit")
    d.table = pd.DataFrame({"x": x, "y": y, "ŷ": fitted, "residual": resid, "residual²": resid**2})
    ss_total = float(((y - y.mean()) ** 2).sum())
    ss = _zero_if_tiny(float((resid**2).sum()), ss_total, y)
    df = n - k
    ms = ss / df
    d.add("Points n", "count", n, fit.get("n"))
    d.add("Residual SS", "Σ(y − ŷ)²", ss, fit.get("ss_resid"))
    d.add("Degrees of freedom", f"n − free parameters = {n} − {k}", df, fit.get("df"))
    d.add("Residual mean square", f"SS / df = {_f(ss)} / {df}", ms, fit.get("ms"))
    d.add("Sy.x", f"√MS = √{_f(ms)}", math.sqrt(ms), fit.get("sy_x"))
    d.add("Total SS", "Σ(y − ȳ)²", ss_total)
    d.add("R²", f"1 − SS / SS_total = 1 − {_f(ss)} / {_f(ss_total)}", 1 - ss / ss_total if ss_total > 0 else NAN, fit.get("r2"))
    d.add("AICc", "n·ln(SS/n) + 2K + 2K(K+1)/(n − K − 1), K = free parameters + 1", _aicc(ss, n, k), fit.get("aicc"))
    blocks.append(d)

    e = Block(f"Data set {name}: standard errors and confidence intervals")
    t_df = fit.get("t_df", df)
    if fit.get("ambiguous"):
        e.add("Standard errors", "not estimable: the data do not pin down every parameter", NAN, NAN)
        e.note = "The sheet gives no SE or CI for this fit."
        blocks.append(e)
        return blocks
    J = _jacobian(f, x, p, index)
    unscaled = _cov_unscaled(J)
    cov = unscaled * ms
    e.table = pd.DataFrame({"x": x, **{f"∂Y/∂{q}": J[:, i] for i, q in enumerate(free)}})
    e.note = ("Table: the Jacobian J, the slope of the curve in each free parameter at the fit (central differences). "
              "Covariance of the parameters = Sy.x² · (JᵀJ)⁻¹; SE = square root of its diagonal.")
    t_crit = float(stats.t.ppf(0.5 + ci / 2, t_df))
    e.add(f"Critical t* ({100 * ci:g}% CI)", f"T{_sub(t_df)} quantile at {_f(0.5 + ci / 2)}", t_crit)
    cis = fit.get("ci", {})
    for i, q in enumerate(free):
        se = math.sqrt(cov[i, i]) if cov[i, i] >= 0 else NAN
        e.add(f"SE of {q}", f"Sy.x · √[(JᵀJ)⁻¹]_{i + 1}{i + 1} = {_f(math.sqrt(ms))} · √{_f(unscaled[i, i])}", se, fit["se"].get(q))
        pair = cis.get(q, (None, None))
        e.add(f"{q} CI lower", f"{_f(p[names.index(q)])} − {_f(t_crit)} · {_f(se)}", p[names.index(q)] - t_crit * se, pair[0])
        e.add(f"{q} CI upper", "estimate + t*·SE", p[names.index(q)] + t_crit * se, pair[1])
    derived = fit.get("derived", {})
    pv = dict(zip(names, p))
    ln2 = math.log(2.0)
    extra = {}
    if model == "log_dose_response":
        extra["EC50"] = ("10^LogEC50", 10 ** pv["LogEC50"])
    if model in ("log_dose_response", "dose_response"):
        extra["Span"] = ("Top − Bottom", pv["Top"] - pv["Bottom"])
    if model == "one_phase":
        extra["Span"] = ("Y0 − Plateau", pv["Y0"] - pv["Plateau"])
    if model in ("one_phase", "exponential_growth"):
        extra["Half-life" if model == "one_phase" else "Doubling time"] = ("ln 2 / K", ln2 / pv["K"] if pv["K"] > 0 else NAN)
    if model == "gaussian":
        extra["Width at half height"] = ("2·√(2 ln 2)·|SD|", 2 * math.sqrt(2 * ln2) * abs(pv["SD"]))
    for q, (formula, value) in extra.items():
        e.add(q, formula, value, derived.get(q))
    blocks.append(e)
    return blocks


@register("curvefit")
def _curvefit(table, options: dict, result) -> list[Block]:
    model = result.options.get("model") or options.get("model")
    means = result.options.get("replicates", "each") == "mean"
    ci = float(result.options.get("ci", 0.95))
    ev = result.values
    fits = ev.get("fits", {})
    blocks = []
    data = {}
    for j, name in enumerate(table.sets):
        if name not in fits:
            continue
        x, y = table.points(j, means=means)
        data[name] = (x, y)
        blocks += _fit_block(name, model, x, y, fits[name], ci)
    for name, item in ev.get("comparisons", {}).items():
        s, c = item["simple"], item["complex"]
        b = Block(f"Data set {name}: which model fits better?")
        n = s["n"]
        b.add("Simpler fit: residual SS", "from its own fit", s["ss_resid"])
        b.add("Simpler fit: df", "n − free parameters", s["df"], n - len(s["free"]))
        b.add("Fuller fit: residual SS", "from its own fit", c["ss_resid"])
        b.add("Fuller fit: df", "n − free parameters", c["df"], n - len(c["free"]))
        if item["nested"]:
            dfn, dfd = s["df"] - c["df"], c["df"]
            f = max(s["ss_resid"] - c["ss_resid"], 0.0) / dfn / (c["ss_resid"] / dfd) if dfn > 0 and dfd > 0 and c["ss_resid"] > 0 else NAN
            b.add("F (extra sum of squares)", f"((SS_simple − SS_full) / {dfn}) / (SS_full / {dfd})", f, item.get("f"))
            b.add("P", _p_f_text(f, dfn, dfd), _f_p(f, dfn, dfd), item.get("p"))
        sa, ca = _aicc(s["ss_resid"], n, len(s["free"])), _aicc(c["ss_resid"], n, len(c["free"]))
        delta = ca - sa
        b.add("AICc of the simpler fit", "n·ln(SS/n) + 2K + 2K(K+1)/(n − K − 1)", sa, s["aicc"])
        b.add("AICc of the fuller fit", "same formula", ca, c["aicc"])
        b.add("AICc difference (full − simple)", f"{_f(ca)} − {_f(sa)}", delta, item.get("aicc_delta"))
        prob = 1 / (1 + math.exp(-0.5 * delta)) if delta > -1400 else 0.0
        b.add("Probability the simpler model is correct", f"1 / (1 + e^(−Δ/2)), Δ = {_f(delta)}", prob, item.get("aicc_simple"))
        b.note = "Each fit's SS and df come from its own iterative fit; the tests below are computed from them."
        blocks.append(b)
    ds = ev.get("datasets")
    if ds:
        b = Block("Do the data sets differ?")
        fits_used = [fits[nm] for nm in ds["data_sets"]]
        ss_sep = sum(f["ss_resid"] for f in fits_used)
        df_sep = sum(f["df"] for f in fits_used)
        k_sep = sum(len(f["free"]) for f in fits_used)
        sh = ds["shared"]
        b.add("Separate fits: SS", "Σ SS of each data set", ss_sep)
        b.add("Separate fits: df", "Σ df of each data set", df_sep, ds["dfd"])
        b.add("Shared fit: SS", "computed by one iterative fit of all data sets together", sh["ss_resid"])
        b.add("Shared fit: df", "N − parameters of the shared fit", sh["df"], sh["n"] - sh["k"])
        dfn = sh["df"] - df_sep
        f = max(sh["ss_resid"] - ss_sep, 0.0) / dfn / (ss_sep / df_sep) if dfn > 0 and df_sep > 0 and ss_sep > 0 else NAN
        b.add("F", f"((SS_shared − SS_separate) / {dfn}) / (SS_separate / {df_sep})", f, ds.get("f"))
        b.add("DFn", "df_shared − df_separate", dfn, ds.get("dfn"))
        b.add("P", _p_f_text(f, dfn, df_sep), _f_p(f, dfn, df_sep), ds.get("p"))
        asep = _aicc(ss_sep, sh["n"], k_sep)
        ash = _aicc(sh["ss_resid"], sh["n"], sh["k"])
        delta = asep - ash
        b.add("AICc difference (separate − shared)", f"{_f(asep)} − {_f(ash)}", delta, ds.get("aicc_delta"))
        b.add("Probability the shared fit is correct", "1 / (1 + e^(−Δ/2))", 1 / (1 + math.exp(-0.5 * delta)) if delta > -1400 else 0.0,
              ds.get("aicc_shared"))
        b.note = "The shared fit is found by one iterative search over all data sets; the comparison uses only its SS and df."
        blocks.append(b)
    return blocks


# ---------------------------------------------------------------------------------------------
# Several variables
# ---------------------------------------------------------------------------------------------


def _variables(table, options: dict) -> tuple[list[str], np.ndarray]:
    chosen = options.get("variables")
    names = table.names if not isinstance(chosen, (list, tuple)) else [name for name in table.names if name in chosen]
    columns = [np.asarray(table[name].values, float).reshape(-1) for name in names]
    rows = max((c.size for c in columns), default=0)
    data = np.full((rows, len(names)), np.nan)
    for j, col in enumerate(columns):
        data[: col.size, j] = col
    data[~np.isfinite(data)] = np.nan
    return list(names), data


@register("corrmatrix")
def _corrmatrix(table, options: dict, result) -> list[Block]:
    method = options.get("method", "pearson") if isinstance(options.get("method"), str) else "pearson"
    missing = options.get("missing", "pairwise") if isinstance(options.get("missing"), str) else "pairwise"
    adjust = options.get("adjust", "none") if isinstance(options.get("adjust"), str) else "none"
    ci = _level(options)
    names, data = _variables(table, options)
    if missing == "listwise":
        data = data[np.isfinite(data).all(axis=1)]
    ev = result.values
    symbol = "r" if method == "pearson" else "rs"
    blocks, summary = [], []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            keep = np.isfinite(data[:, i]) & np.isfinite(data[:, j])
            x, y = data[keep, i], data[keep, j]
            if x.size < 3 or np.ptp(x) == 0 or np.ptp(y) == 0:
                continue
            e = {"r": ev["r"][i, j], "p": ev["p"][i, j], "n": int(ev["n"][i, j]), "ci_low": ev["ci_low"][i, j], "ci_high": ev["ci_high"][i, j]}
            block = Block(f"{names[i]} vs. {names[j]}")
            if method == "spearman":
                rx, ry = stats.rankdata(x), stats.rankdata(y)
                block.table = pd.DataFrame({names[i]: x, names[j]: y, f"rank {names[i]}": rx, f"rank {names[j]}": ry})
                block.note = "Spearman: Pearson's formula on the ranks (ties share their average rank); the CI is approximate."
                out = _pearson_steps(block, rx, ry, ci, symbol, None, e)
            else:
                out = _pearson_steps(block, x, y, ci, symbol, None, e)
            adj = ev["p_adjusted"][i, j]
            if adjust != "none":
                block.add("Adjusted P", f"computed by adjusting all the pairs' P values together (method: {adjust})", adj)
                block.note = (block.note + " The adjusted P comes from the adjustment of the whole family of pairs.").strip()
            blocks.append(block)
            summary.append({"pair": f"{names[i]} vs. {names[j]}", "n": out["n"], symbol: out["r"], "t": out["t"], "P": out["p"],
                            "CI low": out["low"], "CI high": out["high"], "adjusted P": adj})
    head = Block("All pairs", table=pd.DataFrame(summary))
    head.note = "Pairs with fewer than 3 cases, or a variable that does not vary, are not computed."
    return [head] + blocks


@register("pca")
def _pca(table, options: dict, result) -> list[Block]:
    ev = result.values
    standardize = options.get("standardize", True) is not False
    select = options.get("select") if isinstance(options.get("select"), str) else "parallel"
    names, data = _variables(table, options)
    complete = np.isfinite(data).all(axis=1)
    x = data[complete]
    n, k = x.shape
    mean = x.mean(axis=0)
    sd = x.std(axis=0, ddof=1)
    z = (x - mean) / sd if standardize else x - mean
    rows = np.flatnonzero(complete) + 1
    blocks = []

    b = Block("Means and standard deviations")
    b.table = pd.DataFrame({"variable": names, "mean": mean, "SD": sd})
    b.add("Cases used", "rows with every variable", n, ev.get("n"))
    b.add("Variables", "count", k)
    blocks.append(b)

    zt = Block("Centred" + (" and scaled" if standardize else "") + " values")
    zt.table = pd.DataFrame(z, columns=[f"z {q}" if standardize else f"{q} − mean" for q in names]).assign(row=rows)[
        ["row"] + [f"z {q}" if standardize else f"{q} − mean" for q in names]]
    zt.note = "z = (value − mean) / SD" if standardize else "value − mean (no scaling: the covariance matrix is analyzed)"
    blocks.append(zt)

    C = z.T @ z / (n - 1)
    cm = Block("Correlation matrix C" if standardize else "Covariance matrix C", table=pd.DataFrame(C, index=names, columns=names).reset_index(
        names="variable"))
    total = float(np.trace(C))
    cm.add("Total variance", "trace of C (sum of the diagonal)", total)
    cm.note = "C = ZᵀZ / (n − 1), with Z the table above."
    blocks.append(cm)

    values, vectors = np.linalg.eigh(C)
    order = np.argsort(values)[::-1]
    values, vectors = np.clip(values[order], 0.0, None), vectors[:, order]
    for c in range(k):
        if vectors[np.argmax(np.abs(vectors[:, c])), c] < 0:
            vectors[:, c] = -vectors[:, c]
    usable = min(k, n - 1)
    values, vectors = values[:usable], vectors[:, :usable]
    share = values / total
    cum = np.cumsum(share)
    labels = [f"PC{c + 1}" for c in range(usable)]
    threshold = ev.get("threshold")
    if select == "parallel":
        keep = 0
        while keep < usable and threshold is not None and values[keep] > threshold[keep]:
            keep += 1
    elif select == "kaiser":
        keep = int((values > (1.0 if standardize else total / k)).sum())
    elif select == "variance":
        target = float(options.get("variance", 0.8))
        keep = int(np.searchsorted(cum, target - 1e-12) + 1)
    else:
        keep = int(options.get("number", 2))
    keep = max(1, min(keep, usable))

    eb = Block("Eigenvalues and shares")
    eb.add("Method", "numpy.linalg.eigh(C): eigenvalues λ and eigenvectors, largest first", select)
    eg = ev.get("eigenvalues")
    for c in range(usable):
        eb.add(f"Eigenvalue λ{c + 1}", f"{labels[c]}: eigenvalue of C", values[c], eg[c] if eg is not None else None)
        eb.add(f"Share of variance {labels[c]}", f"λ / total = {_f(values[c])} / {_f(total)}", share[c], ev["share"][c])
        eb.add(f"Cumulative share {labels[c]}", "running sum of the shares", cum[c], ev["cumulative"][c])
        if threshold is not None:
            eb.add(f"Parallel analysis threshold {labels[c]}", "computed by simulation (95th percentile of random-data eigenvalues)",
                   float(threshold[c]))
    rule = {"parallel": "each eigenvalue above its parallel-analysis threshold, in order", "kaiser": "eigenvalue > 1" if standardize
            else "eigenvalue above the mean eigenvalue", "variance": "fewest components reaching the share of variance",
            "number": "number set by hand"}[select]
    eb.add("Components kept", rule, keep, ev.get("keep"))
    eb.note = ("The thresholds come from simulating random normal data of the same size (seeded); they are shown as the analysis gives them."
               if threshold is not None else "")
    blocks.append(eb)

    vb = Block("Eigenvectors, loadings and scores")
    vb.table = pd.DataFrame(vectors[:, :keep], index=names, columns=[f"vector {q}" for q in labels[:keep]]).reset_index(names="variable")
    loadings = vectors * np.sqrt(values)
    for c in range(keep):
        vb.table[f"loading {labels[c]}"] = loadings[:, c]
    vb.add("Largest difference of the eigenvectors from the analysis", "max |difference| (signs fixed: largest coefficient positive)",
           float(np.abs(vectors[:, :keep] - ev["vectors"][:, :keep]).max()), 0.0)
    vb.add("Largest difference of the loadings from the analysis", "loading = eigenvector · √eigenvalue",
           float(np.abs(loadings - ev["loadings"]).max()), 0.0)
    scores = z @ vectors[:, :keep]
    eng_scores = ev["scores"][labels[:keep]].to_numpy(float)
    vb.add("Largest difference of the scores from the analysis", "score = Z · eigenvector",
           float(np.abs(scores - eng_scores).max()), 0.0)
    sc = Block("Scores", table=pd.DataFrame(scores, columns=labels[:keep]).assign(row=rows)[["row"] + labels[:keep]])
    sc.note = "Score of each case on each kept component: the row of Z times the eigenvector."
    blocks += [vb, sc]
    return blocks
