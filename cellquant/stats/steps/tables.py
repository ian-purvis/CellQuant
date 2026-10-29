"""Show working for tables (see cellquant.stats.steps): contingency tables, ROC curves, Bland-Altman agreement,
survival curves and a stack of P values."""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from cellquant.stats.results import fmt
from cellquant.stats.steps import Block, register


def _eng(result: Any, *keys: str) -> Any:
    """The analysis's own value under ``result.values[keys…]``, or None when it reports none."""

    value: Any = getattr(result, "values", None)
    for key in keys:
        try:
            value = value[key]
        except (KeyError, IndexError, TypeError):
            return None
    return value


def _number(options: dict, key: str, default: float) -> float:
    try:
        return float(options[key])
    except (KeyError, TypeError, ValueError):
        return default


def _pick(table: Any, value: Any, default: int):
    """The column an option names (its name, or a position); anything unusable falls back to ``default``."""

    if isinstance(value, str) and value in table.names:
        return table[value]
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(table):
        return table[value]
    return table[default]


def _level(ci: float) -> str:
    return f"{100 * ci:g}%"


# --- Contingency tables -----------------------------------------------------------------------------------------


def _wilson(x: int, n: int, z: float) -> tuple[float, float]:
    p = x / n
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (0.0 if x == 0 else max(0.0, center - half)), (1.0 if x == n else min(1.0, center + half))


def _totals_table(counts: np.ndarray, rows: list[str], columns: list[str], values: np.ndarray | None = None) -> pd.DataFrame:
    body = counts if values is None else values
    frame = pd.DataFrame(body, columns=columns)
    frame.insert(0, "row", rows)
    if values is None:
        frame["Total"] = counts.sum(axis=1)
        frame.loc[len(frame)] = ["Total", *counts.sum(axis=0), counts.sum()]
    return frame


@register("contingency")
def _contingency(source: Any, options: dict, result: Any) -> list[Block]:
    counts = source.checked()
    rows, columns = source.rows, source.columns
    opts = {**options, **(getattr(result, "options", None) or {})}
    test = opts.get("test") if isinstance(opts.get("test"), str) else "auto"
    ci, alpha = _number(opts, "ci", 0.95), _number(opts, "alpha", 0.05)
    yates, diagnostic, trend = bool(opts.get("yates")), bool(opts.get("diagnostic")), bool(opts.get("trend"))
    square = counts.shape == (2, 2)
    use_fisher = square and test in ("auto", "fisher")
    use_yates = yates and square and not use_fisher
    n_rows, n_cols = counts.shape
    row_totals, col_totals, total = counts.sum(axis=1), counts.sum(axis=0), int(counts.sum())
    blocks: list[Block] = []

    block = Block("Totals", table=_totals_table(counts, rows, columns),
                  note="Row totals are the counts of a row added up across the columns, column totals down the rows.")
    block.add("Total count N", "sum of every cell", total, _eng(result, "n"))
    blocks.append(block)

    expected = np.outer(row_totals, col_totals) / total
    block = Block("Expected counts", table=_totals_table(counts, rows, columns, expected),
                  note="Expected count of a cell = row total × column total / N, the count if rows and columns were unrelated.")
    engine_expected = _eng(result, "chi2", "expected")
    for i in range(n_rows):
        for j in range(n_cols):
            block.add(f"Expected E({rows[i]}, {columns[j]})", f"{row_totals[i]} · {col_totals[j]} / {total}", float(expected[i, j]),
                      None if engine_expected is None else float(np.asarray(engine_expected)[i, j]))
    blocks.append(block)

    diff = counts - expected
    if use_yates:
        diff = np.sign(diff) * (np.abs(diff) - np.minimum(0.5, np.abs(diff)))
    contrib = diff**2 / expected
    chi2 = float(contrib.sum())
    df = (n_rows - 1) * (n_cols - 1)
    cells = pd.DataFrame({"row": [rows[i] for i in range(n_rows) for _ in range(n_cols)],
                          "column": [columns[j] for _ in range(n_rows) for j in range(n_cols)],
                          "O": counts.reshape(-1), "E": expected.reshape(-1), "O-E": (counts - expected).reshape(-1),
                          "term": contrib.reshape(-1)})
    if use_yates:
        cells.insert(5, "|O-E|-0.5", np.abs(diff).reshape(-1))
    block = Block("Chi-square test" + (" with Yates' correction" if use_yates else ""), table=cells)
    if use_yates:
        block.note = "Yates' correction: each |O − E| is reduced by 0.5 (by |O − E| if that is smaller) before it is squared."
    block.add("Chi-square χ²", ("Σ (|O − E| − 0.5)² / E" if use_yates else "Σ (O − E)² / E") + " over the cells",
              chi2, _eng(result, "chi2", "chi2"))
    block.add("Degrees of freedom df", f"({n_rows} − 1)({n_cols} − 1)", df, _eng(result, "chi2", "df"))
    p_chi = float(stats.chi2.sf(chi2, df))
    block.add("P value of the chi-square test", f"P(χ²{_sub(df)} > {fmt(chi2)})", p_chi, _eng(result, "chi2", "p"))
    if not use_fisher:
        block.add("P value (two-sided)", f"P(χ²{_sub(df)} > {fmt(chi2)})", p_chi, _eng(result, "p"))
    else:
        block.note = (block.note + " " if block.note else "") + "Shown for reference: the reported P value is Fisher's exact test (below)."
    if not square:
        v = math.sqrt(chi2 / (total * (min(counts.shape) - 1)))
        block.add("Cramér's V", f"√(χ² / (N · (min(rows, columns) − 1))) = √({fmt(chi2)} / ({total} · {min(counts.shape) - 1}))", v,
                  _eng(result, "cramers_v"))
    blocks.append(block)

    if use_fisher:
        blocks.append(_fisher(counts, rows, columns, result))
    if trend and _eng(result, "trend") is not None:
        blocks.append(_trend(counts, rows, columns, result))
    if square:
        group, outcome = (opts.get(k) if isinstance(opts.get(k), int) and opts.get(k) in (0, 1) and not isinstance(opts.get(k), bool) else 0
                          for k in ("group", "outcome"))
        order_rows, order_cols = [group, 1 - group], [outcome, 1 - outcome]
        two = counts[order_rows][:, order_cols]
        names = ([rows[i] for i in order_rows], [columns[j] for j in order_cols])
        z = float(stats.norm.ppf(0.5 + ci / 2))
        blocks.append(_diagnostic(two, names, z, ci, result) if diagnostic else _effects(two, names, z, ci, result))
    return blocks


def _sub(df: int) -> str:
    return "".join("₀₁₂₃₄₅₆₇₈₉"[int(d)] for d in str(df))


def _fisher(counts: np.ndarray, rows: list[str], columns: list[str], result: Any) -> Block:
    (a, b), (c, d) = counts.tolist()
    r1, c1, n = a + b, a + c, int(counts.sum())
    low, high = max(0, r1 + c1 - n), min(r1, c1)

    def prob(x: int) -> float:
        return math.comb(r1, x) * math.comb(n - r1, c1 - x) / math.comb(n, c1)

    observed = prob(a)
    records = []
    for x in range(low, high + 1):
        records.append({"a": x, "b": r1 - x, "c": c1 - x, "d": n - r1 - c1 + x, "prob": prob(x),
                        "counted": "yes" if prob(x) <= observed * (1 + 1e-7) else "", "observed": "◄" if x == a else ""})
    table = pd.DataFrame(records)
    block = Block("Fisher's exact test", table=table,
                  note=f"Every 2 × 2 table with the observed row totals ({r1}, {n - r1}) and column totals ({c1}, {n - c1}) is listed "
                       "with its hypergeometric probability C(r₁, a) · C(r₂, c₁ − a) / C(N, c₁). Cells are a b / c d in the order of "
                       f"the sheet: {rows[0]} / {rows[1]} by {columns[0]} / {columns[1]}. P is the sum over the tables no more likely "
                       "than the observed one.")
    block.add("Probability of the observed table", f"C({r1}, {a}) · C({n - r1}, {c1 - a}) / C({n}, {c1})", observed)
    p = float(table.loc[table["counted"] == "yes", "prob"].sum())
    block.add("P value (two-sided)", "sum of the probabilities marked 'counted'", min(p, 1.0), _eng(result, "fisher_p"))
    return block


def _trend(counts: np.ndarray, rows: list[str], columns: list[str], result: Any) -> Block:
    oriented = counts if counts.shape[1] == 2 else counts.T
    k = oriented.shape[0]
    scores = np.arange(1, k + 1, dtype=float)
    first, totals = oriented[:, 0].astype(float), oriented.sum(axis=1).astype(float)
    n, r1 = totals.sum(), first.sum()
    p = r1 / n
    t = float((scores * (first - totals * p)).sum())
    variance = p * (1 - p) * float((totals * scores**2).sum() - (totals * scores).sum() ** 2 / n)
    table = pd.DataFrame({"group": rows if counts.shape[1] == 2 else columns, "score": scores, "first": first, "total": totals,
                          "expected": totals * p, "score*(O-E)": scores * (first - totals * p)})
    block = Block("Chi-square test for trend", table=table, note="Cochran-Armitage test; the groups are scored 1, 2, 3… in table order and "
                  "'first' is the count in the first column (or row).")
    block.add("Overall proportion p", f"{fmt(r1)} / {fmt(n)}", p)
    block.add("T = Σ score · (first − total · p)", "sum of the last column", t)
    block.add("Variance of T", "p (1 − p) [Σ total · score² − (Σ total · score)² / N]", variance)
    if variance > 0:
        chi2 = t * t / variance
        block.add("Chi-square χ²", f"T² / variance = {fmt(t)}² / {fmt(variance)}", chi2, _eng(result, "trend", "chi2"))
        block.add("P value", f"P(χ²₁ > {fmt(chi2)})", float(stats.chi2.sf(chi2, 1)), _eng(result, "trend", "p"))
    return block


def _effects(two: np.ndarray, names: tuple, z: float, ci: float, result: Any) -> Block:
    (a, b), (c, d) = (int(v) for v in two[0]), (int(v) for v in two[1])
    first, second = names[0]
    present, absent = names[1]
    n1, n2 = a + b, c + d
    lv = f"{_level(ci)} CI"
    table = pd.DataFrame([[first, a, b, n1], [second, c, d, n2]], columns=["row", present, absent, "Total"])
    block = Block(f"Effect size: {first} vs {second}, {present}", table=table,
                  note="The table is laid out as a b / c d with the group of interest and the outcome of interest first.")
    ev = lambda *k: _eng(result, "effect", *k)  # noqa: E731
    p1, p2 = a / n1, c / n2
    block.add(f"Proportion with {present} in {first}, p₁", f"a / (a + b) = {a} / {n1}", p1, ev("p1"))
    block.add(f"Proportion with {present} in {second}, p₂", f"c / (c + d) = {c} / {n2}", p2, ev("p2"))
    shift = 0.5 if 0 in (a, c) else 0.0
    aa, cc, m1, m2 = a + shift, c + shift, n1 + 2 * shift, n2 + 2 * shift
    if shift:
        block.add("Continuity shift for the relative risk", "a or c is 0, so 0.5 is added to a and c and 1 to each row total", shift)
    rr = (aa / m1) / (cc / m2)
    se = math.sqrt(1 / aa - 1 / m1 + 1 / cc - 1 / m2)
    block.add("Critical value z", f"z for {lv}", z)
    block.add("Relative risk RR", f"(a / (a + b)) / (c / (c + d)) = {fmt(aa / m1)} / {fmt(cc / m2)}", rr, ev("rr"))
    block.add("SE of ln RR", f"√(1/a − 1/(a+b) + 1/c − 1/(c+d)) = √(1/{fmt(aa)} − 1/{fmt(m1)} + 1/{fmt(cc)} − 1/{fmt(m2)})", se)
    block.add(f"RR {lv}, lower", f"RR · exp(−z · SE) = {fmt(rr)} · exp(−{fmt(z)} · {fmt(se)})", rr * math.exp(-z * se),
              None if ev("rr_ci") is None else ev("rr_ci")[0])
    block.add(f"RR {lv}, upper", f"RR · exp(+z · SE)", rr * math.exp(z * se), None if ev("rr_ci") is None else ev("rr_ci")[1])
    shift = 0.5 if 0 in (a, b, c, d) else 0.0
    aa, bb, cc, dd = a + shift, b + shift, c + shift, d + shift
    if shift:
        block.add("Continuity shift for the odds ratio", "a cell is 0, so 0.5 is added to every cell", shift)
    odds = aa * dd / (bb * cc)
    se = math.sqrt(1 / aa + 1 / bb + 1 / cc + 1 / dd)
    block.add("Odds ratio OR", f"a d / (b c) = ({fmt(aa)} · {fmt(dd)}) / ({fmt(bb)} · {fmt(cc)})", odds, ev("odds_ratio"))
    block.add("SE of ln OR", f"√(1/a + 1/b + 1/c + 1/d)", se)
    block.add(f"OR {lv}, lower", f"OR · exp(−z · SE) = {fmt(odds)} · exp(−{fmt(z)} · {fmt(se)})", odds * math.exp(-z * se),
              None if ev("or_ci") is None else ev("or_ci")[0])
    block.add(f"OR {lv}, upper", "OR · exp(+z · SE)", odds * math.exp(z * se), None if ev("or_ci") is None else ev("or_ci")[1])
    diff = p1 - p2
    block.add("Difference of proportions", f"p₁ − p₂ = {fmt(p1)} − {fmt(p2)}", diff, ev("difference"))
    l1, u1 = _wilson(a, n1, z)
    l2, u2 = _wilson(c, n2, z)
    low = diff - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    high = diff + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    block.add(f"Wilson {lv} of p₁", "Wilson score interval of a / (a + b)", f"{fmt(l1)} to {fmt(u1)}")
    block.add(f"Wilson {lv} of p₂", "Wilson score interval of c / (c + d)", f"{fmt(l2)} to {fmt(u2)}")
    dci = ev("difference_ci")
    block.add("Difference CI, lower (Newcombe)", "(p₁ − p₂) − √((p₁ − l₁)² + (u₂ − p₂)²)", low, None if dci is None else dci[0])
    block.add("Difference CI, upper (Newcombe)", "(p₁ − p₂) + √((u₁ − p₁)² + (p₂ − l₂)²)", high, None if dci is None else dci[1])
    nnt = 1 / abs(diff) if diff else math.inf
    block.add("Number needed to treat", f"1 / |p₁ − p₂| = 1 / {fmt(abs(diff))}", nnt, ev("nnt"))
    denominator = math.sqrt(n1 * n2 * (a + c) * (b + d))
    phi = (a * d - b * c) / denominator if denominator else math.nan
    block.add("Phi", "(a d − b c) / √(row totals · column totals)", phi, ev("phi"))
    return block


def _diagnostic(two: np.ndarray, names: tuple, z: float, ci: float, result: Any) -> Block:
    (a, b), (c, d) = (int(v) for v in two[0]), (int(v) for v in two[1])
    first, second = names[0]
    present, absent = names[1]
    n1, n2 = a + b, c + d
    lv = f"{_level(ci)} CI"
    table = pd.DataFrame([[first, a, b, n1], [second, c, d, n2]], columns=["row", present, absent, "Total"])
    block = Block("Diagnostic test", table=table,
                  note=f"{first} is read as test positive, {second} as test negative; {present} as condition present, {absent} as absent. "
                       "TP = a, FP = b, FN = c, TN = d.")
    dx = _eng(result, "effect", "diagnostic") or {}

    def part(key: str, label: str, x: int, n: int, formula: str) -> float:
        value = x / n if n else math.nan
        block.add(label, f"{formula} = {x} / {n}", value, dx.get(key, (None,))[0])
        if n:
            low, high = _wilson(x, n, z)
            interval = dx.get(key, (None, (None, None)))[1]
            block.add(f"{label}, {lv} lower", "Wilson score interval", low, interval[0])
            block.add(f"{label}, {lv} upper", "Wilson score interval", high, interval[1])
        return value

    sens = part("sensitivity", "Sensitivity", a, a + c, "TP / (TP + FN)")
    spec = part("specificity", "Specificity", d, b + d, "TN / (TN + FP)")
    part("ppv", "Positive predictive value", a, n1, "TP / (TP + FP)")
    part("npv", "Negative predictive value", d, n2, "TN / (TN + FN)")
    with np.errstate(divide="ignore", invalid="ignore"):
        lr_pos = float(np.divide(sens, 1 - spec)) if spec < 1 or sens > 0 else math.nan
        lr_neg = float(np.divide(1 - sens, spec)) if spec > 0 or sens < 1 else math.nan

    def ratio(key: str, label: str, value: float, formula: str, cells: tuple[int, int, int, int]) -> None:
        block.add(label, formula, value, dx.get(key))
        x, nx, y, ny = cells
        if x and y and math.isfinite(value) and value > 0:
            spread = z * math.sqrt(1 / x - 1 / nx + 1 / y - 1 / ny)
            low, high = value * math.exp(-spread), value * math.exp(spread)
        else:
            low = high = math.nan
        interval = dx.get(f"{key}_ci", (None, None))
        block.add(f"{label}, {lv} lower", "LR · exp(−z √(1/x − 1/n₁ + 1/y − 1/n₂))", low, interval[0])
        block.add(f"{label}, {lv} upper", "LR · exp(+z √(1/x − 1/n₁ + 1/y − 1/n₂))", high, interval[1])

    ratio("lr_positive", "Likelihood ratio (positive)", lr_pos, "sensitivity / (1 − specificity)", (a, a + c, b, b + d))
    ratio("lr_negative", "Likelihood ratio (negative)", lr_neg, "(1 − sensitivity) / specificity", (c, a + c, d, b + d))
    return block


# --- ROC curve ----------------------------------------------------------------------------------------------------


def _hanley(auc: float, n_p: int, n_c: int) -> tuple[float, float, float, float]:
    q1 = auc / (2.0 - auc)
    q2 = 2.0 * auc * auc / (1.0 + auc)
    variance = (auc * (1 - auc) + (n_p - 1) * (q1 - auc * auc) + (n_c - 1) * (q2 - auc * auc)) / (n_p * n_c)
    return q1, q2, variance, math.sqrt(max(variance, 0.0))


@register("roc")
def _roc(source: Any, options: dict, result: Any) -> list[Block]:
    controls, patients = _pick(source, options.get("controls"), 0), _pick(source, options.get("patients"), 1)
    ci = _number(options, "ci", 0.95)
    c = np.asarray(controls.values, float)
    p = np.asarray(patients.values, float)
    c, p = c[np.isfinite(c)], p[np.isfinite(p)]
    n_c, n_p = c.size, p.size
    direction = options.get("direction") if options.get("direction") in (">", "<") else "auto"
    pooled = np.concatenate([c, p])
    ranks = stats.rankdata(pooled)
    rank_table = pd.DataFrame({"group": [controls.name] * n_c + [patients.name] * n_p, "value": pooled, "rank": ranks})
    blocks = [Block("Ranks of the pooled values", table=rank_table.sort_values("value", kind="mergesort").reset_index(drop=True),
                    note="Ties get the average of the ranks they span.")]
    rank_c, rank_p = ranks[:n_c], ranks[n_c:]
    block = Block("Area under the curve (Mann-Whitney U / (n₁ n₂))")
    block.add(f"Controls n₁ ({controls.name})", "finite values", n_c, _eng(result, "n_controls"))
    block.add(f"Patients n₂ ({patients.name})", "finite values", n_p, _eng(result, "n_patients"))
    block.add("Sum of the patients' ranks", "from the rank table", float(rank_p.sum()))
    u = float(rank_p.sum() - n_p * (n_p + 1) / 2.0)
    block.add("Mann-Whitney U of the patients", f"R − n₂(n₂ + 1)/2 = {fmt(rank_p.sum())} − {n_p * (n_p + 1) / 2:g}", u)
    wins = int((p[:, None] > c[None, :]).sum())
    ties = int((p[:, None] == c[None, :]).sum())
    block.add("Pairs with the patient higher", f"count over the {n_p * n_c} patient-control pairs", wins)
    block.add("Pairs tied", "count of patient = control", ties)
    block.add("U by counting pairs", f"{wins} + {ties} / 2", wins + ties / 2)
    higher = u / (n_p * n_c)
    block.add("P(patient higher) + half the ties", f"U / (n₁ n₂) = {fmt(u)} / {n_p * n_c}", higher)
    if direction == "auto":
        direction = ">" if rank_p.mean() >= rank_c.mean() else "<"
        engine_direction = _eng(result, "direction")
        block.add("Direction chosen", f"mean rank of patients {fmt(rank_p.mean())} vs controls {fmt(rank_c.mean())}", direction, engine_direction)
    auc = higher if direction == ">" else 1.0 - higher
    block.add("Area under the curve A", "U / (n₁ n₂)" if direction == ">" else "1 − U / (n₁ n₂)", auc, _eng(result, "auc"))
    blocks.append(block)

    z = float(stats.norm.ppf(0.5 + ci / 2))
    q1, q2, variance, se = _hanley(auc, n_p, n_c)
    block = Block("Standard error and P value (Hanley and McNeil)")
    block.add("Q₁", f"A / (2 − A) = {fmt(auc)} / {fmt(2 - auc)}", q1)
    block.add("Q₂", f"2A² / (1 + A) = {fmt(2 * auc * auc)} / {fmt(1 + auc)}", q2)
    block.add("Variance of A", "[A(1 − A) + (n₂ − 1)(Q₁ − A²) + (n₁ − 1)(Q₂ − A²)] / (n₁ n₂)", variance)
    block.add("Standard error SE", f"√variance = √{fmt(variance)}", se, _eng(result, "se"))
    block.add("Critical value z", f"z for {_level(ci)} CI", z)
    block.add(f"{_level(ci)} CI, lower", f"max(A − z · SE, 0) = max({fmt(auc)} − {fmt(z)} · {fmt(se)}, 0)", max(auc - z * se, 0.0), _eng(result, "ci_low"))
    block.add(f"{_level(ci)} CI, upper", "min(A + z · SE, 1)", min(auc + z * se, 1.0), _eng(result, "ci_high"))
    q1n, q2n, var0, se0 = _hanley(0.5, n_p, n_c)
    block.add("Q₁ = Q₂ under the null (A = 0.5)", "0.5 / 1.5 = 2 · 0.25 / 1.5", q1n)
    block.add("SE under the null, SE₀", f"the same formula at A = 0.5: √{fmt(var0)}", se0)
    zstat = abs(auc - 0.5) / se0
    block.add("z statistic", f"|A − 0.5| / SE₀ = {fmt(abs(auc - 0.5))} / {fmt(se0)}", zstat)
    block.add("P value (two-tailed)", f"2 · P(Z > {fmt(zstat)})", float(2 * stats.norm.sf(zstat)), _eng(result, "p"))
    blocks.append(block)

    distinct = np.unique(pooled)
    pad = 0.05 * float(distinct[-1] - distinct[0]) if distinct.size > 1 else max(0.05 * abs(float(distinct[0])), 1.0)
    cutoffs = np.concatenate([[distinct[0] - pad], (distinct[:-1] + distinct[1:]) / 2.0, [distinct[-1] + pad]])
    if direction == ">":
        cutoffs = cutoffs[::-1]
    positive = (lambda v, t: v > t) if direction == ">" else (lambda v, t: v < t)
    tp = np.array([int(positive(p, t).sum()) for t in cutoffs])
    fp = np.array([int(positive(c, t).sum()) for t in cutoffs])
    sens, spec = 100.0 * tp / n_p, 100.0 * (n_c - fp) / n_c
    youden = sens + spec - 100.0
    table = pd.DataFrame({"cutoff": cutoffs, "TP": tp, "FP": fp, "FN": n_p - tp, "TN": n_c - fp, "sens %": sens, "spec %": spec,
                          "J": youden / 100.0})
    best = int(np.argmax(youden))
    word = "above" if direction == ">" else "below"
    block = Block(f"Cutoffs (positive when the value is {word} the cutoff)", table=table,
                  note="Cutoffs are midway between consecutive distinct pooled values, plus one 5% of the range beyond each end. "
                       "Sensitivity = TP / n₂, specificity = TN / n₁, Youden's J = sensitivity + specificity − 1.")
    block.add("Best cutoff (largest J)", f"row {best + 1} of the table: J = {youden[best] / 100:.3f}", float(cutoffs[best]), _eng(result, "best_cutoff"))
    curve = _eng(result, "curve")
    if curve is not None:
        block.add("Largest sensitivity + specificity − 100 (%)", "maximum of 100 J", float(youden[best]),
                  float((curve["sensitivity"] + curve["specificity"] - 100.0).max()))
    blocks.append(block)
    return blocks


# --- Bland-Altman -----------------------------------------------------------------------------------------------


@register("blandaltman")
def _bland_altman(source: Any, options: dict, result: Any) -> list[Block]:
    first, second = _pick(source, options.get("a"), 0), _pick(source, options.get("b"), 1)
    ci = _number(options, "ci", 0.95)
    method = options.get("method") if options.get("method") in ("difference", "ratio", "percent") else "difference"
    a, b = np.asarray(first.values, float), np.asarray(second.values, float)
    size = max(a.size, b.size)
    a, b = (np.concatenate([v, np.full(size - v.size, np.nan)]) for v in (a, b))
    keep = np.isfinite(a) & np.isfinite(b)
    rows = np.flatnonzero(keep) + 1
    a, b = a[keep], b[keep]
    n = a.size
    average = (a + b) / 2.0
    measure = {"difference": a - b, "ratio": a / b, "percent": 100.0 * (a - b) / average}[method]
    name = {"difference": "A − B", "ratio": "A / B", "percent": "100 (A − B) / average"}[method]
    bias = float(measure.sum() / n)
    deviation = measure - bias
    table = pd.DataFrame({"row": rows, "A": a, "B": b, "average": average, name: measure, "dev": deviation, "dev²": deviation**2})
    blocks = [Block("Pairs", table=table, note=f"A = {first.name}, B = {second.name}. Rows with a blank in either column are left out; "
                    "dev is the measure minus the bias.")]
    block = Block(f"Bias and limits of agreement ({name})")
    block.add("Number of pairs n", "rows with both values", n, _eng(result, "n"))
    block.add("Bias (mean of the measure)", f"Σ {name} / n", bias, _eng(result, "bias"))
    ss = float((deviation**2).sum())
    block.add("Sum of squared deviations", "Σ (measure − bias)²", ss)
    sd = math.sqrt(ss / (n - 1))
    block.add("SD of the measure", f"√(SS / (n − 1)) = √({fmt(ss)} / {n - 1})", sd, _eng(result, "sd"))
    low, high = bias - 1.96 * sd, bias + 1.96 * sd
    block.add("Lower limit of agreement", f"bias − 1.96 · SD = {fmt(bias)} − 1.96 · {fmt(sd)}", low, _eng(result, "low"))
    block.add("Upper limit of agreement", "bias + 1.96 · SD", high, _eng(result, "high"))
    t = float(stats.t.ppf(0.5 + ci / 2, n - 1))
    lv = f"{_level(ci)} CI"
    se_bias, se_limit = sd / math.sqrt(n), math.sqrt(3.0 * sd * sd / n)
    block.add("Critical value t*", f"t for {lv}, df = {n - 1}: t(0.5 + {ci:g}/2, {n - 1})", t)
    block.add("SE of the bias", f"SD / √n = {fmt(sd)} / √{n}", se_bias)
    block.add("SE of each limit", f"√(3 SD² / n) = √(3 · {fmt(sd * sd)} / {n})", se_limit)
    for label, centre, se, key in (("bias", bias, se_bias, "bias_ci"), ("lower limit", low, se_limit, "low_ci"),
                                   ("upper limit", high, se_limit, "high_ci")):
        engine = _eng(result, key)
        block.add(f"{lv} of the {label}, lower", f"{fmt(centre)} − t* · SE", centre - t * se, None if engine is None else engine[0])
        block.add(f"{lv} of the {label}, upper", f"{fmt(centre)} + t* · SE", centre + t * se, None if engine is None else engine[1])
    blocks.append(block)
    return blocks


# --- Survival -----------------------------------------------------------------------------------------------------


def _km_table(times: np.ndarray, events: np.ndarray) -> pd.DataFrame:
    survival, greenwood, records = 1.0, 0.0, []
    with np.errstate(divide="ignore", invalid="ignore"):
        for t in np.unique(times):
            n = int((times >= t).sum())
            d = int(((times == t) & (events == 1)).sum())
            cens = int(((times == t) & (events == 0)).sum())
            survival *= 1.0 - np.float64(d) / n
            if d:
                greenwood += np.float64(d) / (np.float64(n) * (n - d))
            se = survival * math.sqrt(greenwood)
            records.append({"time": t, "at risk": n, "events": d, "censored": cens, "S": float(survival), "G": float(greenwood),
                            "SE": se if math.isfinite(se) else math.nan})
    return pd.DataFrame(records)


def _median(curve: pd.DataFrame, z: float) -> tuple[float, float, float]:
    ev = curve[curve["events"] > 0]
    s, g, t = ev["S"].to_numpy(), ev["G"].to_numpy(), ev["time"].to_numpy()
    below = np.flatnonzero(s <= 0.5 + 1e-12)
    median = float(t[below[0]]) if below.size else math.nan
    with np.errstate(divide="ignore", invalid="ignore"):
        r = (np.log(-np.log(s)) - math.log(-math.log(0.5))) / (np.sqrt(g) / np.abs(np.log(s)))
    inside = np.flatnonzero(np.abs(r) <= z)
    if not inside.size:
        return median, math.nan, math.nan
    return median, float(t[inside[0]]), math.inf if inside[-1] == t.size - 1 else float(t[inside[-1] + 1])


def _compare(groups: list[tuple[np.ndarray, np.ndarray]], weight: str):
    """O, E, the covariance of O − E and the per-event-time table, weighted 1 (log-rank) or by the number at risk."""

    k = len(groups)
    pooled_t = np.concatenate([t for t, _ in groups])
    pooled_e = np.concatenate([e for _, e in groups])
    observed, expected, covariance = np.zeros(k), np.zeros(k), np.zeros((k, k))
    records = []
    for time in np.unique(pooled_t[pooled_e == 1]):
        n_g = np.array([(t >= time).sum() for t, _ in groups], dtype=float)
        d_g = np.array([((t == time) & (e == 1)).sum() for t, e in groups], dtype=float)
        n, d = n_g.sum(), d_g.sum()
        w = n if weight == "gehan" else 1.0
        share = n_g / n
        observed += w * d_g
        expected += w * d * share
        cov = w * w * d * (n - d) / (n - 1) * (np.diag(share) - np.outer(share, share)) if n > 1 else np.zeros((k, k))
        covariance += cov
        record: dict[str, Any] = {"time": time, "n": int(n), "d": int(d)}
        if weight == "gehan":
            record["w"] = w
        for i in range(k):
            record[f"n{i + 1}"], record[f"d{i + 1}"], record[f"E{i + 1}"] = int(n_g[i]), int(d_g[i]), w * d * share[i]
        record["V11"] = cov[0, 0]
        records.append(record)
    return observed, expected, covariance, pd.DataFrame(records)


def _quadratic(observed: np.ndarray, expected: np.ndarray, covariance: np.ndarray) -> tuple[float, int]:
    u = (observed - expected)[:-1]
    v = covariance[:-1, :-1]
    try:
        chi = float(u @ np.linalg.solve(v, u))
    except np.linalg.LinAlgError:
        chi = float(u @ np.linalg.pinv(v) @ u)
    return chi, len(observed) - 1


@register("survival")
def _survival(source: Any, options: dict, result: Any) -> list[Block]:
    from cellquant.stats.survival import SurvivalTable  # the table's own reading of the XY layout (blank rows, one group per row)

    table = SurvivalTable.from_columns(source.x, {name: source.values[:, j, 0] for j, name in enumerate(source.sets)}, source.title)
    names, data = [], []
    for g, name in enumerate(table.groups):
        t, e = table.checked(g)
        if t.size:
            names.append(name)
            data.append((t, e))
    ci = _number(options, "ci", 0.95)
    z = float(stats.norm.ppf(0.5 + ci / 2))
    lv = f"{_level(ci)} CI"
    blocks: list[Block] = []
    medians: dict[str, float] = {}
    for name, (t, e) in zip(names, data):
        km = _km_table(t, e)
        engine = _eng(result, "curves", name)
        shown = km.rename(columns={"G": "Greenwood Σ"}).replace([np.inf, -np.inf], np.nan)
        block = Block(f"Kaplan-Meier table: {name}", table=shown,
                      note="At each distinct time: at risk n = subjects with time ≥ t, events d, censored c. S = S(previous) · (1 − d / n). "
                           "Greenwood Σ = Σ d / (n (n − d)) over the event times so far, and SE = S · √(Greenwood Σ). Once every "
                           "subject still at risk has the event (d = n), S is 0 and the sum and SE are undefined, so they are blank.")
        last = km.iloc[-1]
        block.add("Subjects", "rows of this group", int(t.size), _eng(result, "subjects") if len(data) == 1 else None)
        block.add("Events", "Σ d", int(e.sum()))
        block.add("Survival after the last time", "product of (1 − d / n) over all times", last["S"], None if engine is None else engine["survival"].iloc[-1])
        block.add("Greenwood SE after the last time", "S · √G", last["SE"], None if engine is None else engine["se"].iloc[-1])
        median, low, high = _median(km, z)
        medians[name] = median
        engine_median = _eng(result, "medians", name)
        block.add("Median survival", "first event time with S ≤ 0.5", median, None if engine_median is None else engine_median[0])
        block.add(f"Median {lv}, lower", "Brookmeyer-Crowley (log-log): first event time where |(g(S) − g(0.5)) / SE of g| ≤ z", low,
                  None if engine_median is None else engine_median[1])
        block.add(f"Median {lv}, upper", "the event time after the last one inside, ∞ if it is the last", high,
                  None if engine_median is None else engine_median[2])
        blocks.append(block)

    if len(data) >= 2:
        k = len(data)
        for weight, title in (("logrank", "Log-rank (Mantel-Cox) test"), ("gehan", "Gehan-Breslow-Wilcoxon test")):
            observed, expected, covariance, per_time = _compare(data, weight)
            chi, df = _quadratic(observed, expected, covariance)
            p = float(stats.chi2.sf(chi, df)) if np.isfinite(chi) else math.nan
            key = "gehan" if weight == "gehan" else "logrank"
            block = Block(title, table=per_time,
                          note="Groups are numbered 1… in table order (" + "; ".join(f"{i + 1} = {n}" for i, n in enumerate(names)) + "). "
                               "At each event time n, d are the totals; E_i = " + ("w · " if weight == "gehan" else "") +
                               "d · n_i / n; V11 = " + ("w² · " if weight == "gehan" else "") + "d (n − d) / (n − 1) · (n₁/n)(1 − n₁/n)."
                               + (" The weight w is the number at risk n." if weight == "gehan" else "")
                               + (f" For {k} groups χ² = u′V⁻¹u over the first {k - 1} groups, with u = O − E and V the covariance matrix."
                                  if k > 2 else ""))
            for i, name in enumerate(names):
                block.add(f"Observed events O{i + 1} ({name})", "Σ " + ("w · " if weight == "gehan" else "") + f"d{i + 1}", float(observed[i]),
                          _eng(result, key, "observed")[i] if _eng(result, key, "observed") is not None else None)
                block.add(f"Expected events E{i + 1} ({name})", "Σ " + ("w · " if weight == "gehan" else "") + f"d · n{i + 1} / n", float(expected[i]),
                          _eng(result, key, "expected")[i] if _eng(result, key, "expected") is not None else None)
            block.add("Variance V of O₁ − E₁", "Σ of the V11 column", float(covariance[0, 0]))
            formula = f"(O₁ − E₁)² / V = ({fmt(observed[0] - expected[0])})² / {fmt(covariance[0, 0])}" if k == 2 else "u′V⁻¹u"
            block.add("Chi-square χ²", formula, chi, _eng(result, key, "chi2"))
            block.add("Degrees of freedom df", f"groups − 1 = {k} − 1", df, _eng(result, key, "df"))
            block.add("P value", f"P(χ²{_sub(df)} > {fmt(chi)})", p, _eng(result, key, "p"))
            blocks.append(block)
        if _eng(result, "trend") is not None and k >= 3:
            observed, expected, covariance, _ = _compare(data, "logrank")
            scores = np.arange(1, k + 1, dtype=float)
            contrast = scores - scores.mean()
            u = float(contrast @ (observed - expected))
            variance = float(contrast @ covariance @ contrast)
            block = Block("Log-rank test for trend", note="Scores 1, 2, 3… in table order, centered; U = Σ c_i (O_i − E_i), V = c′Vc.")
            block.add("U", "Σ c_i (O_i − E_i)", u)
            block.add("V", "c′ V c", variance)
            chi = u * u / variance if variance > 0 else math.nan
            block.add("Chi-square χ²", f"U² / V = {fmt(u)}² / {fmt(variance)}", chi, _eng(result, "trend", "chi2"))
            block.add("P value", f"P(χ²₁ > {fmt(chi)})", float(stats.chi2.sf(chi, 1)) if np.isfinite(chi) else math.nan, _eng(result, "trend", "p"))
            blocks.append(block)
    if len(data) == 2:
        observed, expected, covariance, _ = _compare(data, "logrank")
        hazard = _eng(result, "hazard_ratio") or {}
        v = covariance[0, 0]
        block = Block(f"Hazard ratio ({names[0]} / {names[1]})")
        if v > 0:
            log_hr = (observed[0] - expected[0]) / v
            spread = z / math.sqrt(v)
            engine = hazard.get("mantel_haenszel", (None, None, None))
            block.add("Hazard ratio, Mantel-Haenszel", f"exp((O₁ − E₁) / V) = exp({fmt(observed[0] - expected[0])} / {fmt(v)})", math.exp(log_hr), engine[0])
            block.add(f"HR {lv}, lower (Mantel-Haenszel)", "exp(ln HR − z / √V)", math.exp(log_hr - spread), engine[1])
            block.add(f"HR {lv}, upper (Mantel-Haenszel)", "exp(ln HR + z / √V)", math.exp(log_hr + spread), engine[2])
        if (observed > 0).all() and (expected > 0).all():
            log_hr = math.log((observed[0] / expected[0]) / (observed[1] / expected[1]))
            spread = z * math.sqrt(1 / expected[0] + 1 / expected[1])
            engine = hazard.get("logrank", (None, None, None))
            block.add("Hazard ratio, log-rank (O/E)", f"(O₁/E₁) / (O₂/E₂) = ({fmt(observed[0])}/{fmt(expected[0])}) / ({fmt(observed[1])}/{fmt(expected[1])})",
                      math.exp(log_hr), engine[0])
            block.add(f"HR {lv}, lower (log-rank)", "exp(ln HR − z √(1/E₁ + 1/E₂))", math.exp(log_hr - spread), engine[1])
            block.add(f"HR {lv}, upper (log-rank)", "exp(ln HR + z √(1/E₁ + 1/E₂))", math.exp(log_hr + spread), engine[2])
        a, b = medians[names[0]], medians[names[1]]
        if np.isfinite(a) and np.isfinite(b) and b > 0:
            block.add("Ratio of median survival", f"{fmt(a)} / {fmt(b)}", a / b, _eng(result, "median_ratio"))
        blocks.append(block)
    return blocks


# --- A stack of P values -------------------------------------------------------------------------------------------


def _parse(text: str) -> tuple[list[float], list[str]]:
    values, labels = [], []
    for line in text.replace(";", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.sub(r",\s+", " ", line.replace("\t", " ")).replace(",", ".").rsplit(None, 1)
        values.append(float(parts[-1].lstrip("Pp=<≤ ").strip()))
        label = re.sub(r"[=<≤\s]+$", "", parts[0]).strip() if len(parts) > 1 else ""
        labels.append(label if label.lower() not in ("", "p", "q") else f"P{len(values)}")
    return values, labels


def _bh(sorted_p: np.ndarray) -> np.ndarray:
    m = sorted_p.size
    return np.minimum(np.minimum.accumulate((sorted_p * m / np.arange(1, m + 1))[::-1])[::-1], 1.0)


@register("pstack")
def _pstack(source: Any, options: dict, result: Any) -> list[Block]:
    values, labels = _parse(str(options.get("text", "")))
    method = str(options.get("method", "bky")).lower().replace("-", "_")
    alpha = _number(options, "alpha", 0.05)
    p = np.asarray(values, float)
    m = p.size
    order = np.argsort(p, kind="mergesort")
    sp = p[order]
    r = np.arange(1, m + 1)
    engine_adj = _eng(result, "adjusted")
    engine_sorted = None if engine_adj is None else np.asarray(engine_adj, float)[order]
    q1 = alpha / (1 + alpha)
    n_true = m
    notes = {
        "bonferroni": ("min(1, m · P)", np.minimum(1.0, m * sp), [f"min(1, {m} · {fmt(x)})" for x in sp]),
        "sidak": ("1 − (1 − P)^m", 1 - (1 - sp) ** m, [f"1 − (1 − {fmt(x)})^{m}" for x in sp]),
        "none": ("P", sp.copy(), [fmt(x) for x in sp]),
    }
    if method in notes:
        rule, adjusted, formulas = notes[method]
        raw = None
    elif method in ("holm", "holm_sidak"):
        factor = m - r + 1
        raw = np.minimum(1.0, factor * sp) if method == "holm" else 1 - (1 - sp) ** factor
        adjusted = np.maximum.accumulate(raw)
        rule = "step-down: max over s ≤ r of " + ("min(1, (m − s + 1) · P(s))" if method == "holm" else "1 − (1 − P(s))^(m − s + 1)")
        formulas = [("min(1, " + f"{f} · {fmt(x)})") if method == "holm" else f"1 − (1 − {fmt(x)})^{f}" for f, x in zip(factor, sp)]
    elif method in ("bh", "by"):
        raw = sp * m / r
        factor = float((1.0 / r).sum()) if method == "by" else 1.0
        adjusted = np.minimum(1.0, _bh(sp) * factor)
        rule = "step-up: min over s ≥ r of m · P(s) / s" + (f", times Σ 1/i = {fmt(factor)}" if method == "by" else "")
        formulas = [f"{m} · {fmt(x)} / {i}" for i, x in zip(r, sp)]
    else:  # bky
        base = _bh(sp)
        r1 = int((base <= q1).sum())
        n_true = m if r1 in (0, m) else m - r1
        raw = base
        adjusted = np.asarray(engine_sorted if engine_sorted is not None else base, float)
        rule = "two-stage step-up: q is the smallest Q at which the comparison is a discovery (found by the engine)"
        formulas = ["computed by the engine's search over Q"] * m
    table = pd.DataFrame({"label": [labels[i] for i in order], "P": sp, "rank": r})
    if raw is not None:
        table["raw"] = raw
    table["adjusted"] = adjusted
    fdr = method in ("bky", "bh", "by")
    block = Block("Sorted P values and their adjustment", table=table,
                  note=f"Rule: {rule}. P values are sorted ascending (ties keep their order); m = {m}."
                       + (" 'raw' is m · P / rank before the running minimum from the largest rank down." if method in ("bh", "by") else "")
                       + (" 'raw' is the BH adjusted P used by both stages." if method == "bky" else "")
                       + (" The q values of the two-stage method are the engine's: they come from searching, interval by interval, for "
                          "the smallest Q at which each comparison is a discovery, which is not redone by hand here." if method == "bky" else ""))
    block.add("Number of P values m", "count", m, _eng(result, "m"))
    if method == "bky":
        r1 = int((_bh(sp) <= q1).sum())
        block.add("Stage 1 level Q′", f"Q / (1 + Q) = {alpha:g} / {1 + alpha:g}", q1)
        block.add("Stage 1 discoveries r₁", "number of BH adjusted P ≤ Q′", r1)
        block.add("Estimated true nulls N_true (m₀)", f"m − r₁ = {m} − {r1} (m when r₁ is 0 or m)", n_true, _eng(result, "m0"))
    for i in range(m):
        label = labels[order[i]]
        block.add(f"Adjusted P of {label} (rank {i + 1})" if not fdr else f"q of {label} (rank {i + 1})", formulas[i], float(adjusted[i]),
                  None if engine_sorted is None else float(engine_sorted[i]))
    significant = adjusted <= alpha if fdr else adjusted < alpha
    if method == "bky":
        r1 = int((_bh(sp) <= q1).sum())
        discoveries = m if r1 == m else 0 if r1 == 0 else int((_bh(sp) <= q1 * m / n_true).sum())
    else:
        discoveries = int(significant.sum())
    block.add("Discoveries" if fdr else "Significant", ("q ≤ " if fdr else "adjusted P < ") + f"{alpha:g}", discoveries, _eng(result, "discoveries"))
    return [block]
