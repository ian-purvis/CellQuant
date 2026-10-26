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

# --- Compare three or more columns ---------------------------------------------------------------------------------

ANOVA_PAIRED = (
    "Unpaired: different biological replicates in each group (for example conditions prepared separately).\n"
    "Paired / repeated measures: the same replicate gives a value in every group (for example one sample measured in three "
    "conditions), so row i of every column belongs together. The analysis removes the replicate-to-replicate variation."
)
ANOVA_GAUSSIAN = (
    "Yes: the values of each group are roughly Gaussian, so the groups are compared by their means (ANOVA).\n"
    "No: the groups are compared by ranks (Kruskal-Wallis, or Friedman when paired). Rank tests assume less but have less "
    "power with a small N; for example four groups of 3 replicates can still reach P < 0.05 overall, but Dunn's tests between "
    "pairs often cannot."
)
ANOVA_ORDINARY = (
    "Ordinary one-way ANOVA assumes every group has the same SD. Fine when the groups are alike in spread "
    "(for example % of target around 40 to 60 in every condition). The Brown-Forsythe and Bartlett tests on the sheet check it."
)
ANOVA_WELCH = (
    "Welch's and Brown-Forsythe ANOVA do not assume equal SDs; the sheet reports both. Safer when one group varies much more than "
    "another (for example a control near 0% with almost no spread next to a treated group between 10% and 60%)."
)
ANOVA_RM = (
    "Repeated-measures ANOVA for matched rows: each row is one biological replicate measured in every condition. "
    "With a blank cell it fits a mixed-effects model instead, as a commercial package does."
)
KRUSKAL = (
    "Kruskal-Wallis ranks all values together and asks whether the groups differ in their typical rank. "
    "Use it when you cannot assume a Gaussian distribution, for example scores, or percentages piled near 0. "
    "Follow it with Dunn's tests."
)
FRIEDMAN = "Friedman is the rank test for matched rows: each replicate's values are ranked across the conditions, then the ranks are compared."
GREENHOUSE = (
    "Sphericity means the differences between every pair of conditions vary equally. Repeated measures often break it. "
    "Geisser-Greenhouse (recommended) shrinks the degrees of freedom by epsilon, so P stays valid when it is broken "
    "and is about the same when it holds. Untick only if you know the conditions are spherical."
)
COMPARISONS_GOAL = {
    "none": "Only the overall test: do the groups differ at all? It does not say which groups differ.",
    "all": "Compare every group with every other group. With k groups that is k(k-1)/2 comparisons, so the correction is stricter. "
           "Use when you have no reference group, for example four conditions that are all of interest.",
    "control": "Compare every group with one or two reference (control) groups, for example a negative and a positive control. "
               "Fewer comparisons than all pairs, so more power. The usual choice for image data with controls.",
    "selected": "Compare only the pairs you tick, for example treated 1 vs treated 2 and nothing else. Choose them before seeing the data.",
    "trend": "Asks whether the group means rise or fall in a straight line across the columns, taken in column order and equally spaced "
             "(for example increasing doses). Put the groups in order of dose first.",
}
CONTROL = (
    "The reference group that every other group is compared with, usually the negative control. "
    "Differences are reported as reference minus group."
)
CONTROL2 = (
    "Optional second reference group, for example a positive control next to the negative control. "
    "Both are compared with every other group, and on a graph the second reference's stars are drawn in another colour."
)
FAMILY = (
    "With two reference groups: each reference is its own family (like running a vs-control analysis once per reference, "
    "the usual choice), or both are corrected together as one family, which is stricter because there are more comparisons to correct for."
)
PAIRS = "Tick the pairs to compare. Choose them before looking at the results; the correction counts only the ticked pairs."
METHOD = (
    "The correction for making several comparisons, so that the chance of any false positive stays at alpha. "
    "The list shows only methods that are valid for the design you chose. Hover each choice for when to use it."
)
METHOD_TIPS = {
    "tukey": "Tukey: every pair of groups, equal SDs. The standard choice for all pairs; it has confidence intervals.",
    "newman_keuls": "Newman-Keuls: all pairs, more powerful than Tukey but it does not control the family-wise error rate and gives no adjusted P. Prefer Tukey.",
    "dunnett": "Dunnett: each group against one reference group. More powerful than Tukey when you do not need the other pairs.",
    "sidak": "Šidák: a few chosen comparisons; slightly more powerful than Bonferroni. Works for any set of comparisons.",
    "holm_sidak": "Holm-Šidák: step-down version of Šidák, more powerful. Gives adjusted P values but no confidence intervals.",
    "bonferroni": "Bonferroni: multiplies each P by the number of comparisons. Simple and conservative.",
    "fisher_lsd": "Fisher's LSD: no correction for several comparisons. Only for one or two planned comparisons; otherwise false positives pile up.",
    "bky": "FDR (two-stage step-up, Benjamini-Krieger-Yekutieli): controls the fraction of false discoveries instead of any false positive. "
           "Useful when screening many comparisons; gives q values.",
    "bh": "FDR (Benjamini-Hochberg): controls the false discovery rate; valid when the tests are independent or positively correlated.",
    "by": "FDR (Benjamini-Yekutieli): controls the false discovery rate under any dependence; more conservative.",
    "games_howell": "Games-Howell: all pairs when SDs differ; best with larger groups (about 6 or more values each).",
    "dunnett_t3": "Dunnett's T3: comparisons when SDs differ; better than Games-Howell with small groups.",
    "tamhane_t2": "Tamhane's T2: Welch t tests with a Šidák correction; conservative, for SDs that differ.",
    "dunn": "Dunn: the standard follow-up to Kruskal-Wallis or Friedman; compares mean ranks with a Bonferroni correction.",
    "dunn_uncorrected": "Uncorrected Dunn: the same z tests without any correction. Only for one or two planned comparisons.",
}
ANOVA_ALPHA = "Alpha is the family-wise significance level: the chance of at least one false positive among the comparisons you chose. For FDR methods it is the desired false discovery rate, Q."
ANOVA_CI = "The confidence level of the interval around each difference between groups. Rank tests give no interval."
ANOVA_STYLE = P_STYLE
ANOVA_TREND = COMPARISONS_GOAL["trend"]

# --- Identify outliers ---------------------------------------------------------------------------------------------

OUTLIER_METHOD = (
    "ROUT finds several outliers at once and rarely flags a value that belongs. Grubbs' test finds at most one; "
    "iterative Grubbs' repeats it, but two similar outliers can hide each other. Start with ROUT."
)
ROUT = (
    "ROUT (robust regression, then outlier removal): fits the column robustly and flags values whose distance from the centre is "
    "too large at a false discovery rate Q. Good default, for example for % of target values where one replicate is far from the rest."
)
GRUBBS = "Grubbs' test: flags the single most extreme value if it is too far from the mean for a Gaussian sample. Needs Gaussian values."
ITERATIVE_GRUBBS = "Iterative Grubbs': applies Grubbs' test again after removing each outlier. Can miss outliers when two lie close together."
OUTLIER_Q = "Q is the false discovery rate for ROUT: the fraction of flagged values that may be false. 1% (default) flags only clear outliers; 5% flags more."
OUTLIER_ALPHA = "Alpha for Grubbs' test: the chance of flagging a value in a sample that has no outlier. 0.05 is usual; 0.01 is more cautious."
OUTLIER_COLUMNS = "Tick the columns to test. Each column is tested on its own and needs at least 3 values."
CLEAN = (
    "Make a new data table in which the flagged values are blank. The original table is never changed, and the new table follows it: "
    "if you edit the original, the cleaned copy updates. Remove an outlier only for a reason you can report, for example a failed replicate."
)
OUTLIERS_SHOWN = (
    "Flagged values stay in the original table and its analyses. They are shaded in the data grid and drawn as hollow points on graphs. "
    "Analyze the cleaned table to leave them out."
)

# --- Stack of P values ---------------------------------------------------------------------------------------------

PSTACK_TEXT = (
    "Paste one P value per line, optionally with a label before it (for example “Comparison 1  0.012”). "
    "Use this to correct P values that came from separate tests, for example one test per measurement."
)
PSTACK_METHOD = (
    "FDR methods control the fraction of false discoveries (q values); use them when screening many tests. "
    "Holm-Šidák, Šidák, Holm and Bonferroni control the chance of any false positive (adjusted P values); "
    "use them for a few tests. Bonferroni is the simplest and most conservative."
)
PSTACK_ALPHA = "For FDR methods, Q is the false discovery rate you accept (5% is usual). For the others, the family-wise alpha."

# --- Graphs of multiple comparisons ---------------------------------------------------------------------------------

BRACKET_MODE = (
    "Brackets: a bracket joins the two compared columns, one per comparison.\n"
    "Asterisks above columns: only the stars, over each compared column (a commercial package's “vs control” style); "
    "needs comparisons against a reference group."
)
HIDE_NS = "Leave out the comparisons that are not significant (ns), so only the differences found are marked."
REF_COLORS = (
    "With two reference groups, the stars and brackets of each reference get their own colour so you can tell the comparisons apart: "
    "the first reference is black by default, the second red. Click to change."
)
LETTERS = (
    "Compact letters above the columns: groups that share a letter are not significantly different; groups with no letter in common are. "
    "Needs comparisons of all pairs. Replaces brackets. Example: A, AB and B means A and B differ but neither differs from AB."
)
GRAPH_COMPARISON = "The comparison whose P values the brackets show, and whose difference the estimation plot draws. A two-column test or an ANOVA with multiple comparisons."
LETTER_STYLE = "How the letters are written: A B C (a commercial package's default), a b c, or numbers 1 2 3. Groups that share one are not significantly different."
