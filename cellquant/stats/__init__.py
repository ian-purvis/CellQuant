"""Statistics for column tables: the standard "column" analyses.

Two columns: t tests and rank tests (``compare``). Three or more: one-way ANOVA in its ordinary,
Welch/Brown-Forsythe, repeated-measures and mixed-effects forms, Kruskal-Wallis and Friedman
(``one_way``), each with multiple comparisons. Also P value adjustment for a stack of P values,
compact letter displays and outlier identification (ROUT, Grubbs).

No viewer is needed. Each analysis returns an ``AnalysisResult`` laid out like a results
sheet, with the raw numbers in ``result.values``. See docs/STATISTICS.md for definitions and
equations. The Anderson-Darling and Kolmogorov-Smirnov normality tests and the mixed-effects model need statsmodels
(``pip install "cellquant[stats]"``); everything else needs only numpy, scipy and pandas.
"""

from cellquant.stats.anova import (
    ANOVA_TESTS,
    bartlett_test,
    brown_forsythe_test,
    friedman,
    geisser_greenhouse_epsilon,
    kruskal_wallis,
    mixed_effects_anova,
    one_way,
    ordinary_anova,
    repeated_measures_anova,
    welch_anova,
)
from cellquant.stats.contingency import ContingencyTable, contingency
from cellquant.stats.descriptive import (
    column_statistics,
    column_statistics_table,
    describe,
    kurtosis,
    one_sample_t,
    percentiles,
    skewness,
    wilcoxon_signed_rank,
)
from cellquant.stats.diagnostic import bland_altman, roc_curve
from cellquant.stats.grouped import GroupedTable, two_way_anova
from cellquant.stats.multcomp import ComparisonPlan, adjust_p, compact_letter_display, p_value_stack
from cellquant.stats.normality import lognormality, normality
from cellquant.stats.outliers import grubbs_fit, identify_outliers, rout_fit
from cellquant.stats.pvalues import STYLES, format_p, p_summary, p_text
from cellquant.stats.results import AnalysisResult, Row, Section
from cellquant.stats.tables import Column, ColumnTable
from cellquant.stats.transforms import column_math, fraction_of_total, normalize, process, remove_baseline, transform
from cellquant.stats.ttests import (
    TESTS,
    compare,
    effect_sizes,
    hedges_j,
    kolmogorov_smirnov,
    lognormal_unpaired_t,
    mann_whitney,
    paired_effect_sizes,
    paired_t,
    ratio_paired_t,
    unpaired_t,
    welch_t,
    wilcoxon_matched_pairs,
)
from cellquant.stats.curvefit import MODELS as CURVE_MODELS, fit_curve, nonlinear_regression
from cellquant.stats.xy import XYTable, linear_regression, xy_correlation

__all__ = [
    "ANOVA_TESTS",
    "CURVE_MODELS",
    "STYLES",
    "TESTS",
    "AnalysisResult",
    "Column",
    "ColumnTable",
    "ComparisonPlan",
    "ContingencyTable",
    "GroupedTable",
    "Row",
    "Section",
    "XYTable",
    "adjust_p",
    "bartlett_test",
    "bland_altman",
    "brown_forsythe_test",
    "column_math",
    "column_statistics",
    "column_statistics_table",
    "compact_letter_display",
    "compare",
    "contingency",
    "describe",
    "effect_sizes",
    "fit_curve",
    "format_p",
    "fraction_of_total",
    "friedman",
    "geisser_greenhouse_epsilon",
    "grubbs_fit",
    "hedges_j",
    "identify_outliers",
    "kolmogorov_smirnov",
    "kruskal_wallis",
    "kurtosis",
    "linear_regression",
    "nonlinear_regression",
    "lognormal_unpaired_t",
    "lognormality",
    "mann_whitney",
    "mixed_effects_anova",
    "normality",
    "normalize",
    "one_sample_t",
    "one_way",
    "ordinary_anova",
    "p_summary",
    "p_text",
    "p_value_stack",
    "paired_effect_sizes",
    "paired_t",
    "percentiles",
    "process",
    "ratio_paired_t",
    "remove_baseline",
    "repeated_measures_anova",
    "roc_curve",
    "rout_fit",
    "skewness",
    "transform",
    "two_way_anova",
    "unpaired_t",
    "welch_anova",
    "welch_t",
    "wilcoxon_matched_pairs",
    "wilcoxon_signed_rank",
    "xy_correlation",
]
