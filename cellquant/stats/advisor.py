"""The analysis guide: questions about an experiment, and the analysis they recommend.

``recommend(answers, facts)`` turns the answers (what the question is, what one value in the table is,
paired or not, Gaussian or not, ...) and what is known about the table (``facts``: group sizes, the
replicate unit of an import, a table of replicate means) into a :class:`Recommendation`: the test, why,
the alternatives, its degrees of freedom worked out from the table, warnings (pseudoreplication, too
few replicates for a rank test to reach significance) and a sentence to report it. No Qt here, so the
logic is tested on its own; ``cellquant.gui.quantanalysis.guide`` asks the questions.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
from scipy import special

from cellquant.stats.grouped import GroupedTable
from cellquant.stats.tables import ColumnTable
from cellquant.stats.xy import XYTable

GOALS = {
    "groups": "Whether groups (conditions) differ",
    "two_factors": "How two factors (e.g. genotype × treatment) act",
    "xy": "How one measure changes with another (X, Y)",
    "survival": "How long until an event (survival)",
}
DOTS = {
    "replicate": "A biological replicate (animal, culture, experiment)",
    "image": "An image (field of view)",
    "cell": "A cell or object",
}
GAUSSIAN = {"yes": "Yes", "log": "No, but their logs are (long right tail)", "no": "No", "unsure": "Not sure"}
COMPARISONS = ("control", "all", "selected", "trend", "none")

# Names as the analysis dialogs show them (cellquant.gui.quantanalysis.project.COMPARE_TESTS / ANOVA_NAMES).
TEST_NAMES = {
    "welch_t": "Welch's t test", "unpaired_t": "Unpaired t test", "lognormal_t": "Unpaired t test on logs (lognormal)",
    "mann_whitney": "Mann-Whitney test", "paired_t": "Paired t test", "ratio_paired_t": "Ratio paired t test",
    "wilcoxon": "Wilcoxon matched-pairs signed rank test",
    "ordinary": "Ordinary one-way ANOVA", "welch": "Welch's and Brown-Forsythe ANOVA", "repeated_measures": "Repeated measures one-way ANOVA",
    "kruskal_wallis": "Kruskal-Wallis test", "friedman": "Friedman test",
    "twoway": "Two-way ANOVA", "linreg": "Linear regression", "pearson": "Pearson correlation", "spearman": "Spearman correlation",
}


@dataclass
class Answers:
    goal: str = "groups"            # a key of GOALS
    dot: str = "replicate"          # a key of DOTS: what one value in the table is
    independent: bool = False       # images or cells, but each from its own independent sample (so each is a replicate)
    groups: int = 2                 # 2, or 3 for "three or more"
    paired: bool = False            # the same replicate (or experiment) gave a value in every group
    gaussian: str = "yes"           # a key of GAUSSIAN
    equal_sd: bool = False
    comparisons: str = "control"    # three or more groups: a member of COMPARISONS
    tails: str = "two-sided"        # or "greater": the direction was predicted before the experiment
    interaction: bool = True        # two factors: does the effect of one depend on the other?
    xy: str = "line"                # "line" (linear regression) or "correlation"
    xy_replicates: str = "each"     # several Y per X: "each" value, or the "mean" of each X


@dataclass
class Facts:
    """What the selected table says about the experiment. Every field is optional."""

    shape: str | None = None                  # "column", "grouped" or "xy"
    names: list[str] = field(default_factory=list)
    sizes: list[int] = field(default_factory=list)       # values per group (column, or cell of a grouped table)
    paired_table: bool = False
    pairs: int | None = None                  # paired table: rows with a value in every column
    sds: list[float] = field(default_factory=list)
    dot: str | None = None                    # an import's dot: "replicate", "image" or "cell"
    replicates: list[int] = field(default_factory=list)  # biological replicates per group behind images or cells
    replicate_means: bool = False             # a table of replicates: the tests use each replicate's mean (or median)
    replicate_summary: str = "mean"           # what a table of replicates gives each replicate: "mean" or "median"
    rows: int = 0                             # grouped: rows (first factor) and data sets (second factor)
    data_sets: int = 0
    points: int = 0                           # xy: X values with a Y (one point per X, replicates averaged)
    points_each: int = 0                      # xy: every (X, Y) pair, replicates counted separately
    y_per_x: int = 1                          # xy: Y replicates per X
    normality: dict[str, float] = field(default_factory=dict)  # Shapiro-Wilk P per group (n ≥ 3)


@dataclass
class Recommendation:
    kind: str                                 # "compare", "anova", "twoway", "linreg", "xycorr" or "survival" (a window analysis)
    options: dict
    name: str
    why: str
    then: str = ""
    alternatives: list[str] = field(default_factory=list)
    unit: str = ""                            # what N is
    dof: list[tuple[str, str]] = field(default_factory=list)  # (what, value with its formula)
    df: float | None = None                   # the error (or test) df the analysis will use, when the table gives it
    warnings: list[str] = field(default_factory=list)
    report: str = ""
    pseudoreplicated: bool = False            # the values are images or cells of shared samples
    mixed: bool = False                       # paired with blanks: the engine fits a mixed-effects model


def _finite(values: np.ndarray) -> np.ndarray:
    return values[np.isfinite(values)]


def _shapiro(values: np.ndarray) -> float | None:
    from scipy import stats

    if values.size < 3 or np.ptp(values) == 0:
        return None
    return float(stats.shapiro(values).pvalue)


def facts_of(table: ColumnTable | None = None, grouped: GroupedTable | None = None, xy: XYTable | None = None, *,
             dot: str | None = None, replicates: list[int] | None = None, replicate_means: bool = False,
             replicate_summary: str = "mean") -> Facts:
    """What a data sheet's table tells the guide. ``dot`` is the import's dot ("unit" is read as "replicate", "object" as
    "cell"); ``replicates`` the number of biological replicates behind each group's images or cells."""

    dot = {"unit": "replicate", "object": "cell"}.get(dot or "", dot)
    facts = Facts(dot=dot, replicates=list(replicates or []), replicate_means=replicate_means,
                  replicate_summary="median" if replicate_summary == "median" else "mean")
    if xy is not None:
        facts.shape = "xy"
        facts.names = list(xy.sets)
        facts.y_per_x = int(xy.replicates)
        facts.points = max((int(xy.points(j, means=True)[0].size) for j in range(len(xy.sets))), default=0)
        facts.points_each = max((int(xy.points(j)[0].size) for j in range(len(xy.sets))), default=0)
        return facts
    if grouped is not None:
        facts.shape = "grouped"
        facts.rows, facts.data_sets = len(grouped.rows), len(grouped.columns)
        facts.sizes = [int(_finite(grouped.cell(i, j)).size) for i in range(facts.rows) for j in range(facts.data_sets)]
        return facts
    if table is None:
        return facts
    facts.shape = "column"
    facts.names = list(table.names)
    columns = [column.values for column in table.columns]
    facts.sizes = [int(_finite(values).size) for values in columns]
    facts.sds = [float(np.std(_finite(values), ddof=1)) if _finite(values).size > 1 else float("nan") for values in columns]
    facts.paired_table = bool(table.paired)
    if columns:
        rows = min(values.size for values in columns)
        facts.pairs = int(np.all([np.isfinite(values[:rows]) for values in columns], axis=0).sum()) if rows else 0
    for name, values in zip(facts.names, columns):
        p = _shapiro(_finite(values))
        if p is not None:
            facts.normality[name] = p
    return facts


def suggested(facts: Facts, answers: Answers | None = None) -> Answers:
    """Answers the table already gives: the question (from the table's shape), what a dot is, the number of groups
    and whether the table is paired. Everything else keeps ``answers`` (or the defaults)."""

    answers = answers or Answers()
    changes = {}
    if facts.shape:
        changes["goal"] = {"column": "groups", "grouped": "two_factors", "xy": "xy"}[facts.shape]
    if facts.replicate_means:
        changes["dot"] = "replicate"
    elif facts.dot in DOTS:
        changes["dot"] = facts.dot
    if facts.shape == "column" and facts.sizes:
        changes["groups"] = 2 if len(facts.sizes) <= 2 else 3
        changes["paired"] = facts.paired_table
    if facts.shape == "xy" and facts.y_per_x > 1:
        changes["xy_replicates"] = "each"
    return replace(answers, **changes)


# --- Small N --------------------------------------------------------------------------------------------------


def smallest_p(test: str, n_a: int, n_b: int = 0) -> float:
    """The smallest two-tailed P an exact rank test can give with these sample sizes (no ties)."""

    if test == "mann_whitney":
        return min(1.0, 2.0 / float(special.comb(n_a + n_b, n_a))) if n_a and n_b else 1.0
    if test == "wilcoxon":
        return min(1.0, 2.0 / 2.0**n_a) if n_a else 1.0
    return 0.0


def _fmt(value: float) -> str:
    if value is None or not np.isfinite(value):
        return "—"
    return f"{value:.0f}" if abs(value - round(value)) < 1e-9 else f"{value:.2f}"


def welch_df(sizes: list[int], sds: list[float]) -> float | None:
    """Welch-Satterthwaite df for two groups: (s₁²/n₁ + s₂²/n₂)² / [(s₁²/n₁)²/(n₁−1) + (s₂²/n₂)²/(n₂−1)]."""

    if len(sizes) < 2 or min(sizes[:2]) < 2 or not all(np.isfinite(sds[:2])):
        return None
    a, b = (sds[i] ** 2 / sizes[i] for i in range(2))
    if a + b == 0:
        return None
    return float((a + b) ** 2 / (a * a / (sizes[0] - 1) + b * b / (sizes[1] - 1)))


def welch_anova_df(sizes: list[int], sds: list[float]) -> float | None:
    """Welch's ANOVA denominator df: (k² − 1) / (3Λ), Λ = Σ (1 − wᵢ/Σw)² / (nᵢ − 1), wᵢ = nᵢ/sᵢ²."""

    n, s = np.asarray(sizes, float), np.asarray(sds, float)
    if n.size < 2 or n.min() < 2 or not np.all(np.isfinite(s)) or np.any(s == 0):
        return None
    w = n / s**2
    lam = np.sum((1 - w / w.sum()) ** 2 / (n - 1))
    return float((n.size**2 - 1) / (3 * lam))


# --- The recommendation ---------------------------------------------------------------------------------------


def _unit(answers: Answers, facts: Facts, rec: Recommendation, sizes: list[int]) -> None:
    """What N is, and the pseudoreplication warning when the values are images or cells of shared samples."""

    sizes_text = " and ".join(str(n) for n in sizes) if 0 < len(sizes) <= 6 else (f"{min(sizes)} to {max(sizes)}" if sizes else "")
    if facts.replicate_means:
        what = facts.replicate_summary
        rec.unit = (f"Each value in the analysis is one biological replicate's {what} (a table of replicates, as in a SuperPlot): the tests "
                    f"use N, the number of replicates{f' ({sizes_text} per group)' if sizes_text else ''}, not n, the cells or images "
                    f"behind each {what}. Keep the number of cells per replicate similar: each replicate's {what} counts equally.")
        if not answers.paired:
            rec.warnings.append("If each experiment included every condition (run side by side on the same day), answer Paired: a paired "
                                "test of the replicate means removes the run-to-run variation, and ignoring that link makes the test too "
                                "conservative (real differences missed).")
        return
    if answers.dot == "replicate" or answers.independent:
        rec.unit = (f"Each value is a biological replicate, so N = {sizes_text} per group" if sizes_text else
                    "Each value is a biological replicate, so N is the number of values per group") + (
                    ". Images or cells of one replicate are its technical replicates (n): average them first." if answers.dot == "replicate"
                    else ". You said each image or cell comes from its own independent sample, so each counts as a replicate.")
        return
    what = "images" if answers.dot == "image" else "cells"
    behind = (f" They come from {' and '.join(str(n) for n in facts.replicates)} biological replicates per group, so the right N is that, "
              "not the number of values." if facts.replicates else "")
    rec.unit = f"Each value is one of the {what} (n), not an independent sample (N)."
    rec.pseudoreplicated = True
    rec.warnings.insert(0, (
        f"Pseudoreplication: {what} from one animal, culture or experiment share its biology and handling, so they are not independent tests "
        f"of the treatment, which was applied once to the whole sample. Testing them as if they were makes the degrees of freedom far too large "
        f"and P far too small: false positives can exceed half of all tests.{behind} Use one value per biological replicate (the mean of its "
        f"{what}): import again with “a dot is a biological replicate”, or use a table of replicates, where each replicate's mean is tested "
        f"and a SuperPlot still shows every one of the {what}. Only if each of the {what} was treated or assigned on its own (for example each "
        "cell injected separately) is it its own replicate: then answer that it is."))


def _two_groups(answers: Answers, facts: Facts) -> Recommendation:
    paired, gaussian, sizes = answers.paired, answers.gaussian, facts.sizes[:2]
    if paired:
        test = {"no": "wilcoxon", "log": "ratio_paired_t"}.get(gaussian, "paired_t")
    else:
        test = {"no": "mann_whitney", "log": "lognormal_t"}.get(gaussian, "unpaired_t" if answers.equal_sd else "welch_t")
    why = {
        "welch_t": "Two unpaired groups with Gaussian values, without assuming equal SDs. Welch's test stays correct when the SDs differ "
                   "and loses almost nothing when they are equal, so it is the safer default.",
        "unpaired_t": "Two unpaired groups, Gaussian values, and the same SD in both (check the SDs below).",
        "lognormal_t": "Two unpaired groups of positive values whose logarithms are Gaussian (intensities, areas, counts with a long right "
                       "tail). The test compares the logs and reports the ratio of the geometric means. It assumes the logs have the same "
                       "SD in both groups, whatever the answer about SDs.",
        "mann_whitney": "Two unpaired groups, and no Gaussian assumption: the test compares ranks.",
        "paired_t": "Each replicate (or each experiment, run side by side) gave a value in both groups, so the test looks at each pair's "
                    "difference: run-to-run variation drops out, which gives more power. Ignoring the pairing makes the test too conservative.",
        "ratio_paired_t": "Paired, positive values where the effect is a ratio (treated is 1.5× control in every experiment, though the "
                          "experiments differ in absolute level): the test compares the logs of each pair's ratio, so there is no need to "
                          "normalise each experiment to its control first.",
        "wilcoxon": "Paired values with no Gaussian assumption: the test ranks each pair's difference.",
    }[test]
    alternatives = {
        "welch_t": ["Unpaired t test if you are sure the SDs are equal", "Mann-Whitney if the values are not Gaussian",
                    "Unpaired t test on logs if the values are positive with a long right tail"],
        "unpaired_t": ["Welch's t test if the SDs may differ (the safer default)", "Mann-Whitney if the values are not Gaussian"],
        "lognormal_t": ["Welch's t test on the raw values if they are roughly symmetric", "Mann-Whitney"],
        "mann_whitney": ["Welch's t test when the values (or replicate means) are close to Gaussian: more power at small N",
                         "Unpaired t test on logs for positive values with a long right tail"],
        "paired_t": ["Ratio paired t test if the experiments differ in absolute level but the effect is proportional",
                     "Wilcoxon if the differences are not Gaussian"],
        "ratio_paired_t": ["Paired t test if the effect is a difference"],
        "wilcoxon": ["Paired t test when the differences are close to Gaussian: more power at small N"],
    }[test]
    alternatives = alternatives + (
        ["Bland-Altman agreement if the two columns are two methods measuring the same samples (how well they agree, not whether "
         "they differ)"] if paired else
        ["ROC curve if the question is how well the measurement tells the two groups apart (a cutoff that calls each sample)"])
    rec = Recommendation("compare", {"test": test, "alternative": answers.tails}, TEST_NAMES[test], why, alternatives=alternatives)
    if paired:
        pairs = facts.pairs if facts.pairs is not None else (min(sizes) if sizes else None)
        _unit(answers, facts, rec, [pairs] * 2 if pairs else [])
        if pairs and test != "wilcoxon":
            rec.df = float(pairs - 1)
            rec.dof.append(("t test df", f"pairs − 1 = {pairs} − 1 = {pairs - 1}"))
        elif not pairs:
            rec.dof.append(("t test df", "pairs − 1"))
        if test == "wilcoxon":
            rec.dof.append(("Degrees of freedom", "none: a rank test gives an exact P from the number of pairs"))
            if pairs and smallest_p("wilcoxon", pairs) > 0.05:
                rec.warnings.append(f"With {pairs} pairs the smallest two-tailed P Wilcoxon can give is {smallest_p('wilcoxon', pairs):.3g}: "
                                    "it needs 6 pairs or more to reach P < 0.05.")
        if facts.shape == "column" and not facts.paired_table:
            rec.warnings.append("The table is not marked Paired. A paired test lines up row i of each column, so each row must be one "
                                "replicate: tick Paired on the data table.")
    else:
        _unit(answers, facts, rec, sizes)
        if test in ("unpaired_t", "lognormal_t") and len(sizes) == 2:
            rec.df = float(sum(sizes) - 2)
            rec.dof.append(("t test df", f"N₁ + N₂ − 2 = {sizes[0]} + {sizes[1]} − 2 = {sum(sizes) - 2}"))
        elif test == "welch_t" and len(sizes) == 2:
            df = welch_df(sizes, facts.sds[:2]) if len(facts.sds) >= 2 else None
            rec.df = df
            rec.dof.append(("Welch df", f"Welch-Satterthwaite = {_fmt(df)}, between min(N) − 1 = {min(sizes) - 1} and N₁ + N₂ − 2 = "
                                        f"{sum(sizes) - 2}; usually not a whole number" if df is not None else "Welch-Satterthwaite, from the SDs"))
        elif test == "mann_whitney":
            rec.dof.append(("Degrees of freedom", "none: a rank test gives an exact P from the group sizes"))
            if len(sizes) == 2 and min(sizes) > 0 and smallest_p("mann_whitney", *sizes) > 0.05:
                letter = "N" if answers.dot == "replicate" or answers.independent or facts.replicate_means else "n"
                rec.warnings.append(f"With {letter} = {sizes[0]} and {sizes[1]} the smallest two-tailed P Mann-Whitney can give is "
                                    f"{smallest_p('mann_whitney', *sizes):.3g}, so it cannot reach P < 0.05 whatever the data. It needs "
                                    "larger groups (for example 4 and 4, or 3 and 5); a one-tailed P is half as large. Add replicates, or "
                                    "use Welch's t test if its assumptions hold.")
        else:
            rec.dof.append(("t test df", "N₁ + N₂ − 2" if test != "welch_t" else "Welch-Satterthwaite, from the SDs"))
        if test == "unpaired_t" and len(facts.sds) >= 2 and all(np.isfinite(facts.sds[:2])) and min(facts.sds[:2]) > 0:
            ratio = max(facts.sds[:2]) / min(facts.sds[:2])
            if ratio > 2:
                rec.warnings.append(f"One SD is {ratio:.1f}× the other; with unequal SDs the unpaired t test's P is wrong (more so with "
                                    "unequal N). Welch's t test does not assume equal SDs.")
    if answers.tails != "two-sided":
        rec.warnings.append("One-tailed: only if the direction was predicted before collecting the data, and a change the other way "
                            "would be reported as no effect. Otherwise use two tails.")
    rec.report = _report(rec, sizes if not paired else [])
    return rec


def _many_groups(answers: Answers, facts: Facts) -> Recommendation:
    paired, gaussian, sizes = answers.paired, answers.gaussian, facts.sizes
    k = len(sizes) if len(sizes) >= 3 else 3
    if paired:
        test = "friedman" if gaussian == "no" else "repeated_measures"
    elif gaussian == "no":
        test = "kruskal_wallis"
    else:
        test = "ordinary" if answers.equal_sd else "welch"
    goal = answers.comparisons if answers.comparisons in COMPARISONS else "control"
    rank = test in ("kruskal_wallis", "friedman")
    why = {
        "ordinary": "Three or more unpaired groups with Gaussian values and similar SDs. One test asks whether any mean differs, instead "
                    "of a t test for every pair (with 3 groups and 3 t tests the chance of at least one false positive is about 14%, not 5%).",
        "welch": "Three or more unpaired groups with Gaussian values, without assuming equal SDs: Welch's and Brown-Forsythe ANOVA stay "
                 "correct when the SDs differ.",
        "repeated_measures": "The same replicate (or experiment) gave a value in every group, so the analysis compares groups within each "
                             "replicate. Keep the Geisser-Greenhouse correction on.",
        "kruskal_wallis": "Three or more unpaired groups with no Gaussian assumption: the test compares ranks.",
        "friedman": "Matched replicates with no Gaussian assumption: each replicate's values are ranked across the groups.",
    }[test]
    then = {
        "control": "Dunn's tests compare each group with the reference group(s)." if rank else
                   "Dunnett's tests compare each group with the reference group(s). With two references (a negative and a positive control), "
                   "each is its own family.",
        "all": "Dunn's tests compare every pair." if rank else
               "Tukey's test compares every pair (Games-Howell when the SDs differ); a compact letter display summarises them on a graph.",
        "selected": "Only the pairs you tick are compared, so the correction counts only those. Choose them before seeing the results.",
        "trend": "A test for a linear trend across the columns, in column order (for example increasing doses).",
        "none": "Only the overall test; it does not say which groups differ.",
    }[goal]
    alternatives = {
        "ordinary": ["Welch's and Brown-Forsythe ANOVA if the SDs may differ", "Kruskal-Wallis if the values are not Gaussian"],
        "welch": ["Ordinary ANOVA if you are sure the SDs are equal", "Kruskal-Wallis if the values are not Gaussian"],
        "repeated_measures": ["Friedman test if the values are not Gaussian"],
        "kruskal_wallis": ["Welch's ANOVA when the values (or replicate means) are close to Gaussian: more power at small N"],
        "friedman": ["Repeated measures ANOVA when the values are close to Gaussian: more power at small N"],
    }[test]
    if gaussian == "log":
        alternatives.insert(0, "The values' logarithms are Gaussian: make a log table first (Analyze → Transform…) and analyze that")
    rec = Recommendation("anova", {"test": test, "comparisons": None if goal == "none" else goal, "method": None},
                         TEST_NAMES[test], why, then=then, alternatives=alternatives)
    if facts.shape == "column" and len(sizes) < 3:
        rec.warnings.append(f"The table has {len(sizes)} column(s); comparing three or more groups needs a column per group.")
        sizes = []
    mixed = paired and bool(sizes) and facts.pairs is not None and facts.pairs < max(sizes)
    if mixed:
        rec.mixed = True
        rec.warnings.append(f"Only {facts.pairs} of the rows have a value in every column, so the analysis fits a mixed-effects model "
                            "instead of repeated-measures ANOVA (it keeps the incomplete rows). Its degrees of freedom come from the model, "
                            "and degrees of freedom, ε or an SD cannot be set by hand.")
    if paired and mixed:
        _unit(answers, facts, rec, [max(sizes)] * k)
        rec.dof.append(("Degrees of freedom", "from the mixed-effects model (shown in the results)"))
    elif paired:
        n = facts.pairs if facts.pairs is not None else (min(sizes) if sizes else None)
        _unit(answers, facts, rec, [n] * k if n else [])
        if n:
            if test == "repeated_measures":
                rec.df = float((k - 1) * (n - 1))
                rec.dof.append(("Between groups", f"k − 1 = {k} − 1 = {k - 1}"))
                rec.dof.append(("Residual (error)", f"(k − 1)(N − 1) = {k - 1} × {n - 1} = {(k - 1) * (n - 1)}"))
                rec.dof.append(("Geisser-Greenhouse", "multiplies both by ε (between 1/(k − 1) and 1, estimated from the data; shown in the results)"))
            else:
                rec.dof.append(("Friedman χ² df", f"k − 1 = {k - 1}"))
        if facts.shape == "column" and not facts.paired_table:
            rec.warnings.append("The table is not marked Paired. Each row must be one replicate: tick Paired on the data table.")
    else:
        _unit(answers, facts, rec, sizes)
        total = sum(sizes)
        if sizes:
            rec.dof.append(("Between groups", f"k − 1 = {k} − 1 = {k - 1}"))
            if test == "ordinary":
                rec.df = float(total - k)
                rec.dof.append(("Residual (error)", f"N − k = {total} − {k} = {total - k}; the multiple comparisons use it too"))
            elif test == "welch":
                df = welch_anova_df(sizes, facts.sds) if len(facts.sds) == len(sizes) else None
                rec.df = df
                rec.dof.append(("Welch denominator", f"(k² − 1)/(3Λ) = {_fmt(df)}, from the SDs; at most N − k = {total - k}"
                                if df is not None else "(k² − 1)/(3Λ), from the SDs"))
            else:
                rec.dof.append(("Kruskal-Wallis H df", f"k − 1 = {k - 1} (H is compared with χ²)"))
        else:
            rec.dof.append(("Between groups", "k − 1"))
            rec.dof.append(("Residual (error)", "N − k" if test == "ordinary" else "from the test"))
    if rank and sizes and min(sizes) <= 3:
        rec.warnings.append(f"With {min(sizes)} replicates in a group, Dunn's tests can rarely reach P < 0.05 after correction. "
                            "Add replicates, or use the ANOVA if the values are close to Gaussian.")
    if test == "ordinary" and len(facts.sds) == len(sizes) and all(np.isfinite(facts.sds)) and min(facts.sds or [0]) > 0:
        ratio = max(facts.sds) / min(facts.sds)
        if ratio > 3:
            rec.warnings.append(f"The largest SD is {ratio:.1f}× the smallest; ordinary ANOVA assumes equal SDs. Welch's ANOVA does not.")
    rec.report = _report(rec, sizes if not paired else [])
    return rec


def _two_factors(answers: Answers, facts: Facts) -> Recommendation:
    r, c = facts.rows, facts.data_sets
    total = sum(facts.sizes)
    one_each = bool(facts.sizes) and max(facts.sizes) <= 1
    interaction = answers.interaction and not one_each
    why = ("Two factors (the table's rows and its data sets). The two-way ANOVA tests each factor and, with the interaction, whether the effect "
           "of one depends on the other (does the treatment work differently in the two genotypes?).")
    cells = sum(1 for size in facts.sizes if size > 0)
    rec = Recommendation("twoway", {"interaction": interaction}, TEST_NAMES["twoway"], why,
                         then="Multiple comparisons within each row (or data set) say where the effects are.",
                         alternatives=["Without the interaction, when you assume the factors act independently: more residual df"])
    _unit(answers, facts, rec, [])
    if answers.paired:
        rec.warnings.append("Repeated measures (the same replicate in every cell) is not available for two-way ANOVA yet; the analysis treats "
                            "every value as independent, so it is not right for matched data.")
    if answers.gaussian == "no":
        rec.warnings.append("There is no rank version of the two-way ANOVA. If the logs look Gaussian, analyze a log table; otherwise "
                            "compare the groups within each level of one factor separately.")
    if r and c:
        rec.dof.append(("Rows factor", f"r − 1 = {r - 1}"))
        rec.dof.append(("Data set factor", f"c − 1 = {c - 1}"))
        if interaction:
            rec.dof.append(("Interaction", f"(r − 1)(c − 1) = {(r - 1) * (c - 1)}"))
            rec.df = float(total - cells)
            rec.dof.append(("Residual (error)", f"N − rc = {total} − {r * c} = {total - r * c}" if cells == r * c else
                            f"N − (cells with values) = {total} − {cells} = {total - cells}"))
        else:
            rec.df = float(total - r - c + 1)
            rec.dof.append(("Residual (error)", f"N − r − c + 1 = {total - r - c + 1}"))
        if one_each:
            rec.warnings.append("One value per cell: the interaction cannot be tested (no residual left), so it is left out.")
        if cells < r * c:
            rec.warnings.append(f"{r * c - cells} cell(s) are empty: some effects or comparisons may not be estimable, and the results say which.")
    rec.report = _report(rec, [])
    return rec


def _xy(answers: Answers, facts: Facts) -> Recommendation:
    n = facts.points_each if answers.xy_replicates == "each" else facts.points
    if answers.xy == "correlation":
        method = "spearman" if answers.gaussian == "no" else "pearson"
        rec = Recommendation("xycorr", {"method": method, "replicates": answers.xy_replicates}, TEST_NAMES[method],
                             "Whether X and Y go together, without treating either as the cause: r (Pearson, for straight-line relations of "
                             "Gaussian values) or rₛ (Spearman, ranks: any steadily rising or falling relation).",
                             alternatives=["Linear regression to predict Y from X, or compare slopes between data sets"])
    else:
        rec = Recommendation("linreg", {"replicates": answers.xy_replicates}, TEST_NAMES["linreg"],
                             "Fits a straight line to predict Y from X, with the slope's 95% CI; with several data sets it tests whether the "
                             "slopes, then the intercepts, differ.",
                             alternatives=["Correlation if neither variable depends on the other",
                                           "A curve fit if the relation is not straight (dose-response, saturation)"])
    _unit(answers, facts, rec, [])
    if facts.y_per_x > 1 and answers.xy_replicates == "each":
        rec.warnings.append(f"Each X has {facts.y_per_x} Y values. If those are technical replicates (images of one sample), fit their mean "
                            "instead: otherwise N, and the df, count the same sample several times.")
    if n:
        rec.df = float(n - 2)
        rec.dof.append(("Residual df" if rec.kind == "linreg" else "t test of r", f"N − 2 = {n} − 2 = {n - 2}"))
    else:
        rec.dof.append(("Residual df", "N − 2 (N points)"))
    rec.report = _report(rec, [])
    return rec


def _survival(answers: Answers, facts: Facts) -> Recommendation:
    rec = Recommendation("survival", {}, "Survival curves (Kaplan-Meier)",
                         "Times to an event (death, relapse, a cell dividing) where some subjects are still event-free when last seen "
                         "(censored): Kaplan-Meier curves use every subject, censored ones until they drop out, and the log-rank test compares "
                         "the curves. Averaging the times, or leaving out the censored subjects, would be biased.",
                         alternatives=["Gehan-Breslow-Wilcoxon test (on the same sheet) when early differences matter most",
                                       "The log-rank test for trend when the groups are in order (dose, grade)"],
                         unit="N = the subjects (one row each)")
    rec.dof.append(("Log-rank test", "chi-square with groups − 1 df"))
    rec.warnings.append("Make the table with File → New survival table: one row per subject, its time in X and 1 (event) or 0 (censored) in "
                        "its group's column. The hazard ratio assumes the hazards stay proportional: look for crossing curves.")
    rec.report = ("Kaplan-Meier survival; log-rank (Mantel-Cox) χ²(df) = …, P = …; median survival … (95% CI … to …) per group; hazard ratio "
                  "… (95% CI … to …); N subjects and events per group.")
    return rec


def recommend(answers: Answers, facts: Facts | None = None) -> Recommendation:
    facts = facts or Facts()
    if answers.goal == "survival":
        return _survival(answers, facts)
    if answers.goal == "xy":
        rec = _xy(answers, facts)
    elif answers.goal == "two_factors":
        rec = _two_factors(answers, facts)
    elif answers.groups == 2:
        rec = _two_groups(answers, facts)
    else:
        rec = _many_groups(answers, facts)
    if answers.gaussian == "unsure":
        rec.warnings.append("Not sure about Gaussian? Look at the dots: roughly symmetric, no long tail, nothing piled at 0 or 100%? Means of "
                            "many cells per replicate are usually close to Gaussian. With N below about 8 a normality test cannot tell, and "
                            "with large N it flags departures that do not matter, so do not rely on it alone.")
    counts = facts.sizes if (answers.dot == "replicate" or answers.independent or facts.replicate_means) else facts.replicates
    if rec.kind in ("compare", "anova") and counts and min(counts) < 3:
        rec.warnings.append(f"A group has {min(counts)} biological replicate(s). Three is a practical minimum, and subtle differences need "
                            "more: adding replicates raises power far more than measuring more cells in each.")
    if answers.dot != "replicate" and not answers.independent and not facts.replicate_means:
        rec.alternatives.append("Nested t test or nested one-way ANOVA (first tick Replicates above the table and name each row's replicate, "
                                "or import with a replicate column): keeps every value, tests the groups against the variation among "
                                "replicates and shows how much of the variation lies among them; with the same number of values per "
                                "replicate its test equals the test on replicate means")
    small = [name for name, p in facts.normality.items() if p < 0.05]
    if small and answers.gaussian in ("yes", "unsure") and rec.kind in ("compare", "anova"):
        rec.warnings.append(f"Shapiro-Wilk P < 0.05 for {', '.join(small)}: those values may not be Gaussian (with large N this can be a "
                            "small departure that does not matter).")
    return rec


REPORTING = ("In the figure or legend give N (independent experiments or biological replicates) and how it was counted, n (cells or "
             "images per replicate) if shown, that the test was run on the replicate means, and what the error bars are: SD describes the "
             "spread, SEM or 95% CI calculated from the N replicate means shows how reproducible the result is.")


def _report(rec: Recommendation, sizes: list[int]) -> str:
    df = _fmt(rec.df) if rec.df is not None else "df"
    if rec.pseudoreplicated:
        n = ", N = the biological replicates per group (not the cells or images: see the warning)"
    else:
        n = f", N = {' and '.join(str(s) for s in sizes)} biological replicates per group" if 0 < len(sizes) <= 6 else ", with N per group"
    two_tails = "two-tailed" if rec.options.get("alternative", "two-sided") == "two-sided" else "one-tailed"
    if rec.kind == "compare":
        test = rec.options["test"]
        stat = "U = …" if test == "mann_whitney" else "W = …" if test == "wilcoxon" else f"t({df}) = …"
        effect = "median difference (Hodges-Lehmann) … (95% CI … to …)" if test in ("mann_whitney", "wilcoxon") else \
            "ratio of geometric means … (95% CI … to …)" if test in ("lognormal_t", "ratio_paired_t") else "difference of means … (95% CI … to …)"
        return f"{rec.name}, {two_tails}{n}: {stat}, P = …; {effect}."
    if rec.kind == "anova":
        test = rec.options["test"]
        stat = "H = …" if test == "kruskal_wallis" else "χ²ᵣ = …" if test == "friedman" else f"F(between, {df}) = …"
        return f"{rec.name}{n}: {stat}, P = …; then each comparison's adjusted P."
    if rec.kind == "twoway":
        return f"Two-way ANOVA: each factor and the interaction as F(effect df, {df}) = …, P = …."
    if rec.kind == "linreg":
        return f"Linear regression: slope … (95% CI … to …), R² = …, F(1, {df}) = …, P = … (N points)."
    return f"{rec.name}: r = … (95% CI … to …), P = …, N = … pairs (df {df})."


__all__ = ["REPORTING", "Answers", "Facts", "Recommendation", "facts_of", "recommend", "suggested", "smallest_p", "welch_df", "welch_anova_df",
           "GOALS", "DOTS", "GAUSSIAN", "COMPARISONS", "TEST_NAMES"]
