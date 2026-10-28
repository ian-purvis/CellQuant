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
    "How P values and stars are written. classic: four-star P value thresholds (* P ≤ 0.05, ** ≤ 0.01, *** ≤ 0.001, **** ≤ 0.0001). "
    "APA and NEJM: three stars at most, 3 decimals."
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
    "With a blank cell it fits a mixed-effects model instead."
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
    "Asterisks above columns: only the stars, over each compared column (the “vs control” style); "
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
GRAPH_COMPARISON = ("The comparison whose P values the brackets show, and whose difference the estimation plot draws. A two-column test, an ANOVA "
                    "with multiple comparisons, or (for a grouped table) a two-way ANOVA with multiple comparisons. For an XY table: the linear "
                    "regression or curve fit whose lines or curves to draw.")
LETTER_STYLE = "How the letters are written: A B C (the default), a b c, or numbers 1 2 3. Groups that share one are not significantly different."

# --- Formatting graphs (Format graph, Format axes, styles) -----------------------------------------------------------

FORMAT_GRAPH = "Open the Format graph dialog: every data set's symbols, bars, error bars and lines, the title, legend, text boxes and significance marks."
FORMAT_AXES = "Open the Format axes dialog: the frame, each axis's range, scale, ticks, numbering, breaks and title."
SELECT = "Click any part of the graph (a data set, an axis, the title, the legend or a text box) to edit it here; double-click opens its dialog."
FORMAT = {
    # The graph
    "font_family": "The font of every text on the graph. Journals often ask for Arial or Helvetica; a font that is not installed shows as the default sans-serif.",
    "font_size": "Font size of every label, in points at the exported size. 7 to 9 pt suits most journals. Each text can override it.",
    # Data sets
    "dataset": "The data set (column) to format. Click a data set on the graph to pick it here.",
    "dataset_color": "The data set's colour. Symbols, bars, boxes and borders left on Auto use it.",
    "visible": "Untick to leave this data set off the graph; it stays in the table and in every analysis.",
    "axis": "Draw this data set against the left or the right Y axis, for example a measure on another scale.",
    "show_symbols": "Draw each value as a symbol. Keep them on bars and boxes so readers see the data, not only the summary.",
    "symbol": "The shape of the symbols. Different shapes keep data sets apart in black and white.",
    "symbol_size": "Symbol area in points². Auto picks a size from the number of values.",
    "symbol_fill": "Fill colour of the symbols. Auto uses the data set's colour.",
    "symbol_border": "Border colour of the symbols. Auto uses the data set's colour; white separates overlapping symbols.",
    "symbol_border_width": "Border thickness of the symbols, in points. 0 for no border.",
    "symbol_alpha": "Opacity of the symbols, 100% is solid. Lower it when many values overlap. Auto is 90% (60% with 200 values or more).",
    "hollow": "Open symbols: only the border, in the fill colour. Useful for a second data set in black and white.",
    "show_bar": "Draw the bar at the mean (or median). Untick to keep only the symbols and error bars.",
    "bar_fill": "Fill colour of the bar. Auto uses the data set's colour.",
    "bar_alpha": "Opacity of the bar fill. Lighter bars let the symbols on top stand out; 100% for solid bars.",
    "hatch": "A fill pattern drawn in the border colour. Tells bars apart in black and white or for colour-blind readers.",
    "bar_border": "Border colour of the bar, also used for its pattern. Auto uses the data set's colour.",
    "bar_border_width": "Border thickness of the bar, in points.",
    "bar_width": "Bar width as a share of the space for each column; the gap between bars is the rest (0.6: bars 60%, gaps 40%).",
    "box_fill": "Fill colour of the box or violin. Auto uses the data set's colour.",
    "box_alpha": "Opacity of the box or violin fill.",
    "box_line": "Outline colour of the box or violin. Auto uses the data set's colour.",
    "box_line_width": "Outline thickness of the box or violin, in points.",
    "box_width": "Width of the box or violin as a share of the space for each column. Auto: 0.5 for a box, 0.75 for a violin.",
    "show_center": "Draw the line at the mean (or the median with median and interquartile range, and on boxes and violins).",
    "center_color": "Colour of the mean or median line.",
    "center_width": "Thickness of the mean or median line, in points.",
    "center_length": "Length of the mean or median line, as a share of the space for each column.",
    "show_error": "Draw the error bars (on boxes: the whiskers).",
    "error_direction": "Both: the bar goes above and below the mean. Above only: the usual choice on bar graphs, where the bar hides the lower half.",
    "error_color": "Colour of the error bars.",
    "error_width": "Thickness of the error bars, in points.",
    "error_cap": "Width of the caps at the ends of the error bars, as a share of the space for each column. 0 for no caps.",
    "apply_all": "Give every data set this one's format (symbols, bars, lines and error bars). Their colours stay their own.",
    "reset_dataset": "Back to the graph's default format for this data set.",
    "pair_lines": "Before-after graphs: the line joining each row's values (one replicate across the conditions).",
    "connect_means": "A line joining the means (or medians) of the columns, for example to show a trend across ordered conditions.",
    "connect_style": "Solid, dashed or dotted.",
    # Axes
    "frame": "How the axis lines meet. Offset X and Y axes is the default: the axes stand slightly apart. Box draws all four sides.",
    "frame_offset": "How far the offset axes stand from the data area, in points.",
    "axis_width": "Thickness of the axis lines and ticks, in points.",
    "axis_color": "Colour of the axis lines and ticks.",
    "which_axis": "The axis to format. Click an axis on the graph to pick it here.",
    "axis_visible": "Untick to hide the axis line, ticks and numbers, for example when a scale bar replaces them.",
    "right_axis": "Add a Y axis on the right. With no data set on it, it repeats the left scale; set a data set's axis to Right to give it its own.",
    "axis_title": "The axis title. Markup: \\alpha for α (and other Greek names), ^{2} for superscript, _{2} for subscript.",
    "range": "Leave blank for automatic. The Y axis starts at 0 when the data are near it.",
    "reverse": "Turn the axis upside down, with the largest values at the bottom.",
    "scale": "Linear, or a log scale for values that span several powers of ten. Values ≤ 0 cannot be shown on a log axis.",
    "major": "The interval between numbered ticks, for example 20 for 0, 20, 40. Blank for automatic. On a log axis, a number of powers (2: every 100-fold).",
    "minor": "Unnumbered ticks between two numbered ticks. On a log axis, any value above 0 shows the usual 2 to 9 ticks.",
    "tick_direction": "Ticks pointing out of the data area (the usual choice), in, across the axis line, or none.",
    "tick_length": "Length of the numbered ticks, in points; minor ticks are about half.",
    "decimals": "Decimal places of the axis numbers. Auto uses enough to tell the ticks apart.",
    "thousands": "Write 12,000 rather than 12000.",
    "notation": "Decimal (1500), scientific (1.5×10³) or, on a log axis, powers of the base (10³).",
    "percent": "Write a % sign after each number, for example for % of target. The numbers are not multiplied by 100.",
    "prefix": "Text written before every axis number.",
    "suffix": "Text written after every axis number, for example a unit.",
    "breaks": "Split the Y axis to leave out an empty range, for example when one group is much higher than the others. "
              "From and to are the values left out; a second break makes three segments.",
    "segments": "Height of each segment as a share of the axis, bottom first. Auto sizes them by the range each one shows.",
    "label_angle": "Turn the column labels, for example 45° when long group names overlap.",
    "label_wrap": "Wrap column labels after this many characters. 0 keeps each on one line.",
    "hide_labels": "Hide the column labels, for example when a legend names the data sets.",
    # Text and legend
    "title": "A title above the graph. Journals usually put it in the figure legend instead. Markup as for axis titles.",
    "title_loc": "Where the title sits above the graph.",
    "legend_show": "A legend naming each data set with its symbol or bar.",
    "legend_position": "Inside a corner, outside on the right, or a master legend: one row above the graph, the same on every graph that uses the style. Drag the legend to place it anywhere.",
    "legend_frame": "A box around the legend.",
    "legend_order": "Drag the names to set the legend order.",
    "text_style": "Font, size (0: the graph's), bold, italic and colour of this text.",
    # Annotations
    "add_text": "Add a text box. Drag it on the graph to move it; double-click to edit.",
    "add_arrow": "Add an arrow, with optional text at its tail, for example to point at one value.",
    "add_line": "Add a straight line.",
    "add_bracket": "Add a bracket with text above it, for example to group conditions or mark a comparison by hand.",
    "annotation_text": "The text. Markup: \\alpha for α, ^{2} superscript, _{2} subscript.",
    "annotation_position": "Position as a share of the data area: 0 is the left or bottom edge, 1 the right or top. Dragging on the graph sets it too.",
    "annotation_box": "Draw a box around the text.",
    "delete_annotation": "Remove the selected text box, line, arrow or bracket.",
    # Significance
    "bracket_width": "Thickness of the significance brackets, in points.",
    "bracket_gap": "Space between the highest value and the bracket, as a share of the axis.",
    "bracket_tip": "Length of the bracket's downward ends, as a share of the axis.",
    "star_size": "Font size of the stars, P values and letters, in points. 0 uses the graph's font size.",
    # Styles
    "style": "Built-in and saved styles. Apply copies the style's look (colours, symbols, axes, fonts, legend) onto this graph; the data and titles stay.",
    "apply_style": "Apply the chosen style to this graph.",
    "save_style": "Save this graph's look as a named style, to apply to other graphs. Saved styles are kept for your user account on this computer.",
    "delete_style": "Delete the chosen saved style. Built-in styles cannot be deleted.",
    "magic": "Make this graph look like another graph or a style, choosing what to copy.",
    "copy_format": "Copy this graph's format (everything but the titles and text boxes) to paste onto another graph.",
    "paste_format": "Paste the format copied from another graph onto this one.",
}
MAGIC_SOURCE = "The graph or style to copy the look from."
MAGIC_TARGETS = "Other graphs that should take the same look at once."

# --- Data processing (transform, normalize, fraction of total, remove baseline, column math) -------------------------

PROCESS = {
    "transform": "A new table with every value of the ticked columns changed by one function, for example log10(Y) before a t test "
                 "on values that span several powers of ten, or Y × 100 to turn fractions into percentages. The table it comes from "
                 "is not changed; the new one follows it.",
    "normalize": "A new table with each ticked column rescaled so its 0% value becomes 0 and its 100% value becomes 100: "
                 "Y' = 100 × (Y − Z) ÷ (H − Z). Use it to put columns measured on different scales on one scale.",
    "fraction": "A new table with each value divided by a total: its column's total, the grand total, or its row's total. "
                "For example the share of all positive cells that each group holds.",
    "baseline": "A new table with every column compared with a baseline column, for example % of control: 100 × Y ÷ the control "
                "mean. Row by row (paired) uses the baseline value in the same row, for example each replicate's own control.",
    "column_math": "A new table with one column computed row by row from two columns, for example the paired difference B − A "
                   "or the ratio B ÷ A of each replicate.",
}
FUNCTION = {
    "multiply": "Y × K. For example K = 100 turns fractions into percentages.",
    "divide": "Y ÷ K. For example K = 100 turns percentages into fractions.",
    "add": "Y + K.",
    "subtract": "Y − K, for example to remove a constant background.",
    "power": "Y raised to the power K.",
    "square": "Y × Y.",
    "sqrt": "The square root of Y; a negative value has none and is left blank. Often used for counts.",
    "reciprocal": "1 ÷ Y; zero has none and is left blank.",
    "log10": "Log base 10. For values that span several powers of ten or grow by factors (intensities, areas, fold changes). Values ≤ 0 are left blank.",
    "log2": "Log base 2: each step of 1 is a doubling. Common for fold changes. Values ≤ 0 are left blank.",
    "ln": "Natural log (base e). Values ≤ 0 are left blank.",
    "exp10": "10 to the power Y: undoes log10.",
    "exp": "e to the power Y: undoes the natural log.",
    "abs": "The absolute value: negative values become positive.",
    "logit": "ln(Y ÷ (1 − Y)) for proportions between 0 and 1 (not percentages): spreads values near 0 and 1. 0 and 1 are left blank.",
    "arcsine": "arcsine(√Y) for proportions from 0 to 1 (not percentages): an older way to steady the variance of proportions.",
    "zscore": "How many SDs each value lies from its column's mean: (Y − mean) ÷ SD. Each column then has mean 0 and SD 1.",
}
K_VALUE = "The constant K used by the functions that name it (Y × K, Y ÷ K, Y + K, Y − K, Y^K)."
PROCESS_COLUMNS = "Tick the columns to process. Columns left unticked are copied unchanged into the new table."
NORMALIZE_ZERO = "The value that becomes 0%: the smallest value of each column, its first value (row 1), or a number you type."
NORMALIZE_HUNDRED = "The value that becomes 100%: the largest value of each column, its last value, its sum, or a number you type."
RESULT = "Show the results as percentages (0 to 100) or as fractions (0 to 1)."
FRACTION_TOTAL = (
    "Column total: each value ÷ the sum of its column. Grand total: ÷ the sum of every ticked column. "
    "Row total: ÷ the sum of the ticked values in the same row (for example the share of each category in one replicate)."
)
BASELINE = "The baseline (control) column every column is compared with. Put the control first in the table to have it chosen."
BASELINE_OPERATION = (
    "Subtract: Y − baseline (the difference, in the data's units). Divide: Y ÷ baseline (a ratio). % of baseline: "
    "100 × Y ÷ baseline (the control is then 100% on average). % change: 100 × (Y − baseline) ÷ baseline (the control is then 0%)."
)
BASELINE_BY = (
    "The baseline column's mean: one number for every value, for example % of the control mean (unpaired groups). "
    "Same row: each value against the baseline value in its own row, for paired data such as each replicate's own control."
)
KEEP_BASELINE = "Keep the baseline column in the new table (it becomes 0, 1 or 100 on average), or leave it out."
COLUMN_A = "The first column of the formula."
COLUMN_B = "The second column of the formula. Rows are paired: row 1 of A with row 1 of B, and so on."
MATH_OPERATION = (
    "Difference A − B, ratio A ÷ B, sum, product, % change from B to A (100 × (A − B) ÷ B) or log2(A ÷ B) "
    "(a log fold change: +1 is a doubling, −1 a halving)."
)
CHANGE_PROCESS = "Change how this table is computed from its source; its results and graphs update."

# --- Page layouts ---------------------------------------------------------------------------------------------------

NEW_LAYOUT = ("A page with several graphs on it, for a figure: a grid of cells with panel letters (A, B, C…). "
              "It starts with the graphs of the project and follows them as they change.")
LAYOUT_EXPORT = ("Save the page as SVG (every graph stays vector, for a vector editor such as Inkscape, which can also save it "
                 "as PDF) or as PNG, TIFF (LZW) or JPEG at 300 or 600 dpi.")
LAYOUT_PAPER = "The page size. Choose Custom size for a figure exactly as wide as a journal column (85 or 175 mm, for example)."
LAYOUT_CUSTOM = "Width and height of a custom page, in mm. Used when Paper is Custom size."
LAYOUT_GRID = ("How many cells across and down. Each cell holds one graph. A graph keeps its own size and font sizes; "
               "one larger than its cell is shrunk to fit (text too), never enlarged.")
LAYOUT_MARGIN = "Blank space around the page edge, in mm."
LAYOUT_GAP = "Blank space between cells, in mm."
LAYOUT_LABELS = "Letters that name each panel in the figure legend, in reading order, counting filled cells only."
LAYOUT_LABEL_SIZE = "The size of the panel letters, in points (1 pt = 1/72 inch)."
LAYOUT_TITLE = "Optional text across the top of the page, and its size in points."
LAYOUT_CELLS = "Which graph goes in each cell, row by row. Click a cell on the page to choose its graph."
LAYOUT_ALIGN = "Where a graph sits in its cell: at the top left, right under its panel letter (the usual figure look), or centred."

# --- Grouped tables and two-way ANOVA ---------------------------------------------------------------------------------

NEW_GROUPED = ("An empty grouped table: two grouping factors. Rows are one factor (for example treatment), data sets the other "
               "(for example genotype), and each data set's replicates sit side by side.")
GROUPED_TABLE = ("Type or paste values: one row per level of the row factor, one data set per level of the other factor, with the "
                 "replicates side by side. Blank cells are missing values.")
GROUPED_ADD_ROW = "Add an empty row (a new level of the row factor) at the bottom."
GROUPED_ADD_SET = "Add an empty data set (a new level of the column factor) at the right."
GROUPED_ADD_REPLICATE = "Add one more replicate subcolumn to every data set."
COLUMN_BY = ("A second factor makes a grouped table for two-way ANOVA: Group by gives the rows, this gives the data sets. "
             "Leave it at none for a column table.")
TWO_WAY = ("Two-way ANOVA asks three questions at once: does the row factor change the mean, does the data set factor change it, "
           "and does the effect of one depend on the other (the interaction)?")
TWO_WAY_INTERACTION = ("With the interaction, each cell has its own mean; without it, the effects of the two factors simply add up. "
                       "Keep it unless you have a reason not to; it cannot be fitted when a cell is empty or every cell has one value.")
TWO_WAY_COMPARE = ("Within each row: compare the data sets at every level of the row factor (simple effects). Main effect: compare "
                   "factor means averaged over the other factor (least-squares means, every cell weighted equally); most useful "
                   "when the interaction is small.")
TWO_WAY_FAMILY = ("One family per row (or data set) corrects for the comparisons inside each one; all comparisons in one family corrects "
                  "for every comparison in the table at once, which is stricter.")
TWO_WAY_METHOD = ("Tukey for every pair and Dunnett against a reference are the most powerful corrections for those goals. Šidák, "
                  "Holm-Šidák and Bonferroni work for any set of comparisons; Fisher's LSD does not correct; the FDR methods give q values.")
GROUPED_LAYOUT = ("How a grouped table is laid out. Interleaved: one block per row, its data sets side by side in their colours (the legend "
                  "names them). Separated: one block per data set, its rows side by side, the data set named under its block.")
CLUSTER_GAP = "The space between blocks, in column widths (0: no gap)."
NEW_XY = ("An XY table: one X column (dose, time, concentration…) and one or more Y data sets, each with replicates side by side. "
          "For regression, correlation and curves.")
XY_TABLE = ("Each row is one X value; each data set's replicates sit side by side. Type or paste numbers (X first when pasting "
            "into the X column); blank cells are missing values. Double-click a title to rename it. A row with Y but no X is an "
            "unknown: a curve fit can read its X off the curve (a standard curve).")
XY_ADD_SET = "Add a Y data set (another condition measured at the same X values)."
XY_ADD_REPLICATE = "Add a replicate subcolumn to every data set (several Y values at the same X)."
LINREG = ("A straight line through each data set: slope, intercept, their confidence intervals, R² and whether the slope differs "
          "from zero. With two or more data sets, whether their slopes (then intercepts) differ.")
LINREG_ORIGIN = ("Only when theory says Y must be 0 at X = 0 (and the data reach near it). Otherwise leave it off: forcing the line "
                 "can bias the slope.")
XYCORR = ("How closely Y rises or falls with X in each data set: r from −1 to 1, its confidence interval and P. Correlation does "
          "not fit a line; for a slope use Linear regression.")
XYCORR_METHOD = ("Pearson r assumes X and Y are both measured and roughly Gaussian. Spearman r works on ranks: use it for skewed "
                 "data, outliers or a relationship that is monotonic but not straight.")
XY_REPLICATES = ("Each replicate is a point: every value counts (the usual choice when replicates are independent). Only the mean: "
                 "one point per X, when replicates are repeated measurements of one sample. This sets what the fit uses; how a "
                 "graph shows the points is set separately on the graph (Points).")
XY_GRAPH_POINTS = ("How the graph shows each data set; it does not change what a fit uses (set that in the analysis's options).")
XY_BAND = ("Around the fitted line or curve: the confidence band holds the true line with the chosen confidence; the prediction band "
           "holds that share of new points, so it is wider. When the fit used only each row's mean, the prediction band is for a new "
           "mean of a row, not a single replicate.")

CURVEFIT = ("Fit a curve to each data set by least squares: best-fit parameters with standard errors and confidence intervals, R², "
            "and optionally a comparison with a second fit or between data sets, and X read off the curve for unknowns.")
CURVE_MODEL = ("The equation to fit. Dose-response: a sigmoid from Bottom to Top with its midpoint at the EC50 (choose the X is log(dose) "
               "form when X holds log concentrations). Michaelis-Menten: rate against substrate. One-phase exponential: decay or rise "
               "to a plateau, with its half-life. Gaussian: a bell-shaped peak. Line and polynomials: for comparing with a curve or a "
               "standard curve. Starting values are found from the data.")
CURVE_FIX = ("Hold this parameter at a constant instead of fitting it: for example Bottom = 0 and Top = 100 for data normalized to "
             "percent, or HillSlope = 1 for a standard slope. Fix a parameter only when theory or controls set it.")
CURVE_INTERPOLATE = ("Rows with Y values and a blank X are unknowns (for example samples measured against a standard curve). Their X "
                     "is read off each fitted curve within the range of X it was fitted on; outside it they are reported as out of "
                     "range, not extrapolated.")
CURVE_COMPARE_NONE = "Fit each data set and report its parameters."
CURVE_COMPARE_MODEL = ("Fit each data set a second way (another model, or the same model with a parameter fixed) and say which "
                       "fits better: by the extra sum-of-squares F test when one is a special case of the other, and by AICc.")
CURVE_COMPARE_DATASETS = ("Is one curve enough for every data set, or does each need its own? Tests one shared curve against "
                          "separate fits (F test and AICc).")
CURVE_COMPARE_PARAMETER = ("Does this parameter (for example LogEC50) differ between data sets? Fits them with it shared and the "
                           "rest separate, and tests that against separate fits (F test and AICc).")

# Replicates and SuperPlots
REPLICATES = (
    "Add a Replicate column that names each row's biological replicate (N), so the table can hold every value (for example "
    "every cell, n) while its analyses use one value per replicate: its mean (or median) in each column. Lay the rows out in "
    "blocks: the first replicate's values of every column, then the next replicate's. Graph → SuperPlot then shows both levels.\n"
    "Click again to remove the column: the analyses then use every value, which treats cells of one sample as independent "
    "(pseudoreplication)."
)
REPLICATE_COLUMN = (
    "The biological replicate (N) each row belongs to, for example Experiment 1, Animal 2 or a date. Type it on the first row of "
    "each block; a blank continues the replicate above. Use the same names in every column, so a replicate keeps one colour on "
    "the SuperPlot (and, with Paired, is compared with itself). Paste a block whose first column is words to fill it."
)
REPLICATE_VALUE = (
    "Each replicate's value in a column, which the analyses and the large markers of a SuperPlot use. Mean: the usual choice. "
    "Median: when a few values (cells) of a replicate are far from the rest. Each replicate counts once however many "
    "values it has, so keep the number of values per replicate similar."
)
NEW_REPLICATE_TABLE = (
    "A column table with a Replicate column: paste every value (for example every cell) of each condition, in blocks by "
    "biological replicate. Analyses use the replicate means (N) and Graph → SuperPlot shows both levels."
)
CSV_REPLICATE = (
    "The column that names each row's biological replicate (N), if the file has one. The table then holds every value and "
    "its analyses use one value per replicate (the replicate means), ready for a SuperPlot."
)
SUPERPLOT_COLORS = ("One colour per biological replicate, the same in every column: the small dots (values) and the large marker "
                    "(the replicate's mean) share it. Greys suits print; use Shapes with it.")
SUPERPLOT_SHAPES = "A different symbol for each replicate's marker too, so replicates can be told apart without colour."
SUPERPLOT_LAYOUT = ("Together: every value of a column in one swarm, coloured by replicate (the usual SuperPlot). By replicate: each "
                    "replicate's values in a lane of their own with its marker in the middle, which helps when one replicate hides another.")
SUPERPLOT_ALPHA = "How opaque the small dots (values) are. Lower it when there are many values, so the replicate markers stand out."
SUPERPLOT_LINK = ("A line joining each replicate's marker across the columns. For replicates measured in every condition (a paired "
                  "design): shows whether every replicate moves the same way.")
