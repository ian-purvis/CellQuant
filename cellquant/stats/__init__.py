"""Statistics for column tables, modelled on a commercial package's "Column" analyses.

Two columns: t tests and rank tests (``compare``). Three or more: one-way ANOVA in its ordinary,
Welch/Brown-Forsythe, repeated-measures and mixed-effects forms, Kruskal-Wallis and Friedman
(``one_way``), each with multiple comparisons. Also P value adjustment for a stack of P values,
compact letter displays and outlier identification (ROUT, Grubbs).

No viewer is needed. Each analysis returns an ``AnalysisResult`` laid out like a a commercial package results
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
from cellquant.stats.multcomp import ComparisonPlan, adjust_p, compact_letter_display, p_value_stack
from cellquant.stats.normality import lognormality, normality
from cellquant.stats.outliers import grubbs_fit, identify_outliers, rout_fit
from cellquant.stats.pvalues import STYLES, format_p, p_summary, p_text
from cellquant.stats.results import AnalysisResult, Row, Section
from cellquant.stats.tables import Column, ColumnTable
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

__all__ = [
    "ANOVA_TESTS",
    "STYLES",
    "TESTS",
    "AnalysisResult",
    "Column",
    "ColumnTable",
    "ComparisonPlan",
    "Row",
    "Section",
    "adjust_p",
    "bartlett_test",
    "brown_forsythe_test",
    "column_statistics",
    "column_statistics_table",
    "compact_letter_display",
    "compare",
    "describe",
    "effect_sizes",
    "format_p",
    "friedman",
    "geisser_greenhouse_epsilon",
    "grubbs_fit",
    "hedges_j",
    "identify_outliers",
    "kolmogorov_smirnov",
    "kruskal_wallis",
    "kurtosis",
    "lognormal_unpaired_t",
    "lognormality",
    "mann_whitney",
    "mixed_effects_anova",
    "normality",
    "one_sample_t",
    "one_way",
    "ordinary_anova",
    "p_summary",
    "p_text",
    "p_value_stack",
    "paired_effect_sizes",
    "paired_t",
    "percentiles",
    "ratio_paired_t",
    "repeated_measures_anova",
    "rout_fit",
    "skewness",
    "unpaired_t",
    "welch_anova",
    "welch_t",
    "wilcoxon_matched_pairs",
    "wilcoxon_signed_rank",
]
