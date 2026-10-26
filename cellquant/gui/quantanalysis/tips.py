"""Hover text that teaches: what each option means, when to pick it, and a short generic example.

Kept in one place so the words stay consistent. Docs: docs/STATISTICS.md (Help → Statistics guide…).
"""

from __future__ import annotations

PAIRED = (
    "Paired: the same biological replicate was measured in both conditions, so row i of each column belongs together "
    "(for example the left and right half of one sample, or one replicate before and after a treatment). "
    "A paired test compares each replicate with itself, which removes the replicate-to-replicate variation.\n"
    "Unpaired: different replicates in each group (for example treated samples vs separate control samples)."
)
PARAMETRIC = (
    "Parametric (t tests) compare means and assume each group's values come from a Gaussian (normal) distribution.\n"
    "Nonparametric (rank tests) compare ranks and assume less, but have less power with small N: "
    "at 3 vs 3 an exact Mann-Whitney test can never give P < 0.05.\n"
    "Decide from what you know about this kind of measurement and from looking at the data, not only from a normality test."
)
WELCH = (
    "Welch's t test does not assume the two groups have the same SD. It is the safer default for unpaired data: "
    "when the SDs are equal it gives almost the same P as the pooled (Student's) t test, and when they differ it stays correct."
)
POOLED = "The classic (Student's) unpaired t test pools the two SDs, so it assumes both groups vary equally."
LOGNORMAL = (
    "For values that are skewed to the right and always positive (concentrations, areas, intensities): "
    "the t test is run on log10 of the values and reports the ratio of geometric means."
)
RATIO = "Paired t test on log10(B/A): for paired data where the treatment changes values by a factor rather than by an amount."
MANN_WHITNEY = "Ranks all values and asks whether one group tends to have larger values. Reports the medians and the Hodges-Lehmann difference."
KS = "Kolmogorov-Smirnov compares the whole distributions (any difference in shape or position), not just the middle."
WILCOXON = "Wilcoxon matched-pairs: the rank test for paired data, on the differences within each replicate."
TAILS = (
    "Two-tailed (the default) tests for a difference in either direction.\n"
    "One-tailed is valid only if you predicted the direction before seeing the data and would have called a change "
    "in the other direction 'no effect'. When in doubt, use two-tailed."
)
ALPHA = "Alpha is the P value below which a result is called significant. 0.05 is usual; it is the false-positive rate you accept when there is no real difference."
CI_LEVEL = "The confidence level of the interval around the difference. A 95% CI contains the true difference in 95% of experiments like this one."
P_STYLE = (
    "How P values and stars are written. GP: a commercial package's current style (* P ≤ 0.0332, ** ≤ 0.0021, *** ≤ 0.0002, **** < 0.0001). "
    "Older: * ≤ 0.05, ** ≤ 0.01, *** ≤ 0.001. APA and NEJM: 3 decimals."
)
COLUMNS = "The two columns to compare. Every difference is B − A (the second minus the first), so put the control first."

DOT = (
    "What one dot (one value in the table) is.\n"
    "Biological replicate (N): an independent sample, for example one subject, one culture or one independently prepared sample. "
    "Its images are pooled into one value. Use this for tests: conditions are compared across biological replicates.\n"
    "Image (technical replicate, n): one value per image. Images of the same replicate are not independent.\n"
    "Object (technical replicate, n): one value per segmented cell. Thousands of cells from one sample are still one sample, "
    "so tests on cells give P values that are far too small (pseudoreplication)."
)
UNIT = (
    "The column that names the biological replicate (N), usually the folder that holds a replicate's images (Folder 2, …). "
    "Each replicate's images are pooled the same way as units_by_<unit>.csv: the sum of their counts over the sum of their denominators."
)
MEASURE = (
    "The number to analyse. % of target: marker-positive and target-positive cells over target-positive cells. "
    "% of segmented cells: marker-positive cells over every segmented object. Per-object measures (area, intensity) are averaged per image or replicate."
)
GROUP_BY = "The column whose values become the table's columns (the groups or conditions), for example Folder 1. A marker's +/− call splits the objects of each image into two columns."
ORDER = "Drag to set the column order; untick a group to leave it out. Put the control or reference group first: differences are B − A."
APPROVED = "Use only images you approved in step 5. Unchecked images may still have wrong objects or cutoffs."
SUGGESTED = (
    "One click sets the usual analysis for image data: % of target (or % of segmented cells), one dot per biological replicate (N) "
    "pooled from its images (n), and a SuperPlot with mean ± SD. Then compare two groups with Welch's t test (difference and 95% CI, estimation plot), "
    "or Mann-Whitney when you cannot assume a Gaussian distribution (it needs N ≥ 4 per group to reach P < 0.05)."
)

ERROR = {
    "sd": "SD: how much the values vary around the mean. Shows the spread of the data. The usual choice for describing replicates.",
    "sem": "SEM = SD/√N: how precisely the mean is known. It shrinks with more replicates and is not the spread of the data. Say which one you show.",
    "ci": "95% CI of the mean: where the true mean probably lies. Like SEM it shows precision, on a scale that is easier to read.",
    "median_iqr": "Median with the 25th–75th percentiles: for skewed data, or next to a rank test.",
    "none": "Only the dots and the line at the mean.",
}
LOG_Y = "A log Y axis spreads values that span several powers of ten (for example intensities or areas). Values ≤ 0 cannot be shown."
BRACKET = "A bracket between the two compared columns, with the P summary (stars) or the P value of the linked comparison."
DOT_SIZE = "Dot area in points². 0 picks a size from the number of dots."
GRAPH_SIZE = "The size of the exported figure. Journals often ask for 85 mm (one column) or 175 mm (two columns) wide."
COLOR = "Click to choose this column's colour."
SHOW_N = "Write n (images or cells) or N (biological replicates) under each column."
