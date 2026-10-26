"""Statistics for column tables, modelled on GraphPad Prism's "Column" analyses.

No viewer is needed. Each analysis returns an ``AnalysisResult`` laid out like a Prism results
sheet, with the raw numbers in ``result.values``. See docs/STATISTICS.md for definitions and
equations. The Anderson-Darling and Kolmogorov-Smirnov normality tests need statsmodels
(``pip install "cellquant[stats]"``); everything else needs only numpy, scipy and pandas.
"""

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
from cellquant.stats.normality import lognormality, normality
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
    "STYLES",
    "TESTS",
    "AnalysisResult",
    "Column",
    "ColumnTable",
    "Row",
    "Section",
    "column_statistics",
    "column_statistics_table",
    "compare",
    "describe",
    "effect_sizes",
    "format_p",
    "hedges_j",
    "kolmogorov_smirnov",
    "kurtosis",
    "lognormal_unpaired_t",
    "lognormality",
    "mann_whitney",
    "normality",
    "one_sample_t",
    "p_summary",
    "p_text",
    "paired_effect_sizes",
    "paired_t",
    "percentiles",
    "ratio_paired_t",
    "skewness",
    "unpaired_t",
    "welch_t",
    "wilcoxon_matched_pairs",
    "wilcoxon_signed_rank",
]
